"""Immutable PostGIS index writes and authorized-ID spatial reads."""
import json
from sqlalchemy import text, bindparam
from lzo.synchronization.contracts import DomainReceipt


def apply_geometry(connection, envelope):
    _write_geometry_version(connection,envelope)
    connection.execute(text("""INSERT INTO gis_geometry_heads VALUES(:id,:revision)
        ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision
        WHERE gis_geometry_heads.revision<excluded.revision"""),
        dict(id=envelope.entity_id,revision=envelope.aggregate_revision))
    return DomainReceipt(event_id=envelope.event_id,target=envelope.target,payload_hash=envelope.payload_hash,
                         aggregate_revision=envelope.aggregate_revision,status="applied")


def apply_geometry_history(connection,envelope):
    """Late accepted versions never change either current head."""
    _write_geometry_version(connection,envelope)
    return DomainReceipt(event_id=envelope.event_id,target=envelope.target,payload_hash=envelope.payload_hash,
                         aggregate_revision=envelope.aggregate_revision,status="superseded")


def _write_geometry_version(connection,envelope):
    if envelope.operation != "geometry.index":
        raise ValueError("Unsupported GIS operation")
    value = envelope.payload
    geometry = value["geometry"]
    connection.execute(text("""INSERT INTO gis_geometry_versions
        (resource_id,revision,event_id,payload,payload_hash,active,observed_at,geom)
        VALUES(:id,:revision,:event,CAST(:payload AS jsonb),:hash,:active,:observed,
          public.ST_SetSRID(public.ST_GeomFromGeoJSON(CAST(:geo AS text)),4326)) ON CONFLICT(resource_id,revision) DO NOTHING"""),
        dict(id=envelope.entity_id, revision=envelope.aggregate_revision, event=envelope.event_id,
             payload=json.dumps(geometry), hash=envelope.payload_hash, active=value["active"],
             observed=envelope.observed_at, geo=json.dumps(geometry["normalized"]) if geometry else None))
    stored=connection.execute(text("SELECT payload_hash FROM gis_geometry_versions WHERE resource_id=:id AND revision=:revision"),
        dict(id=envelope.entity_id,revision=envelope.aggregate_revision)).scalar_one()
    if stored!=envelope.payload_hash:
        from lzo.synchronization.delivery import DeliveryConflict
        raise DeliveryConflict("Immutable GIS version content conflict")
    asset=value.get("asset")
    if asset:
        connection.execute(text("""INSERT INTO gis_spatial_assets VALUES(:asset,:id,:event,:revision,:artifact,
            CAST(:payload AS jsonb),public.ST_SetSRID(public.ST_GeomFromGeoJSON(:geo),4326)) ON CONFLICT(asset_id) DO NOTHING"""),
            dict(asset=asset["asset_id"],id=envelope.entity_id,event=asset.get("event_id",envelope.event_id),revision=asset.get("revision",envelope.aggregate_revision),
                 artifact=asset["artifact_version_id"],payload=json.dumps(asset),geo=json.dumps(asset["normalized"])))
        _check_metadata(connection,"gis_spatial_assets","asset_id",asset["asset_id"],envelope.entity_id,asset)
    observation=value.get("observation")
    if observation:
        position=observation["position"]
        connection.execute(text("""INSERT INTO gis_location_observations VALUES(:observation,:id,:event,:revision,
            :observed,CAST(:payload AS jsonb),public.ST_Transform(public.ST_SetSRID(public.ST_MakePoint(:x,:y),:srid),4326)) ON CONFLICT(observation_id) DO NOTHING"""),
            dict(observation=observation["observation_id"],id=envelope.entity_id,event=observation.get("event_id",envelope.event_id),
                 revision=observation.get("revision",envelope.aggregate_revision),observed=position["observed_at"],payload=json.dumps(observation),
                 x=position["longitude"],y=position["latitude"],srid={"EPSG:4326":4326,"EPSG:4490":4490}[position["horizontal_datum"]]))
        _check_metadata(connection,"gis_location_observations","observation_id",observation["observation_id"],envelope.entity_id,observation)


def _check_metadata(connection,table,key,identifier,resource_id,payload):
    row=connection.execute(text(f"SELECT resource_id,payload FROM {table} WHERE {key}=:id"),{"id":identifier}).mappings().one()
    if row["resource_id"]!=resource_id or row["payload"]!=payload:
        from lzo.synchronization.delivery import DeliveryConflict
        raise DeliveryConflict("Immutable GIS registration identity content conflict")


class GisRepository:
    def __init__(self, registry):
        self.registry = registry

    def assets(self, authorized_ids, *, cursor=None, limit=100):
        return self._metadata("gis_spatial_assets",authorized_ids,cursor=cursor,limit=limit)

    def observations(self, authorized_ids, *, start=None, end=None, cursor=None, limit=100):
        rows=self._metadata("gis_location_observations",authorized_ids,start=start,end=end,cursor=cursor,limit=limit)
        return [{**r["position"],"observation_id":r["observation_id"],"resource_id":r["resource_id"]} for r in rows]

    def _metadata(self, table, authorized_ids, *, start=None, end=None, cursor=None, limit=100):
        if not authorized_ids:return []
        if not 1<=limit<=1001:raise ValueError("Invalid GIS page size")
        query=f"SELECT resource_id,payload FROM {table} WHERE resource_id IN :ids "
        params=dict(ids=list(authorized_ids),limit=limit)
        historical=table=="gis_location_observations"
        if start is not None:
            query+="AND observed_at>=:start ";params["start"]=start
        if end is not None:
            query+="AND observed_at<:end ";params["end"]=end
        if cursor:
            if historical:
                query+="AND (observed_at,resource_id,observation_id)>(:time,:id,:observation) "
                params.update(zip(("time","id","observation"),cursor))
            else:
                query+="AND asset_id>:cursor "
                params["cursor"]=cursor
        query+="ORDER BY "+("observed_at,resource_id,observation_id" if historical else "asset_id")+" LIMIT :limit"
        with self.registry.engine("gis").connect() as connection:
            rows=connection.execute(text(query).bindparams(bindparam("ids",expanding=True)),params).mappings()
            return [{**r["payload"],"resource_id":str(r["resource_id"])} for r in rows]

    def batch(self, authorized_ids, *, bbox=None, cursor=None, limit=100, observed_from=None, observed_to=None):
        """IDs are freshly authorized by core. No copied scope authorizes a read.

        Observation queries use (observed_at, resource_id, revision) cursors;
        current head queries use resource ID cursors. Unknown height stays absent.
        """
        if not authorized_ids:
            return []
        if not 1 <= limit <= 1001:
            raise ValueError("Invalid GIS page size")
        historical = observed_from is not None or observed_to is not None
        query = "SELECT v.* FROM gis_geometry_versions v "
        if not historical:
            query += "JOIN gis_geometry_heads h USING(resource_id,revision) "
        query += "WHERE v.resource_id IN :ids AND v.active AND v.geom IS NOT NULL "
        params = dict(ids=list(authorized_ids),limit=limit)
        if bbox:
            query += "AND public.ST_Intersects(v.geom,public.ST_MakeEnvelope(:x1,:y1,:x2,:y2,4326)) "
            params.update(zip(("x1","y1","x2","y2"),bbox))
        if observed_from is not None:
            query += "AND v.observed_at>=:start "
            params["start"] = observed_from
        if observed_to is not None:
            query += "AND v.observed_at<:end "
            params["end"] = observed_to
        if cursor:
            if historical:
                query += "AND (v.observed_at,v.resource_id,v.revision)>(:time,:id,:revision) "
                params.update(zip(("time","id","revision"),cursor))
            else:
                query += "AND v.resource_id>:cursor "
                params["cursor"] = cursor
        query += "ORDER BY " + ("v.observed_at,v.resource_id,v.revision" if historical else "v.resource_id") + " LIMIT :limit"
        with self.registry.engine("gis").connect() as connection:
            return list(connection.execute(text(query).bindparams(bindparam("ids",expanding=True)),params).mappings())


from lzo.synchronization.delivery import register_delivery_handler
register_delivery_handler("gis",apply_geometry,history_handler=apply_geometry_history)
