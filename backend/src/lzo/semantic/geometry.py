"""PostGIS geometry with explicit CRS, original evidence and transactional effects."""
import json
import math
from uuid import UUID
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from lzo.auth.permissions import validate_actor
from lzo.errors import ApiError, Conflict
from lzo.governance.models import platform_watermarks
from lzo.semantic import facts
from lzo.semantic.schemas import GeoFeatureCollection

# Deployment-owned extensible CRS policy; requests cannot register transforms.
_crs = {"EPSG:4326": 4326, "EPSG:3857": 3857, "EPSG:4490": 4490}


def register_crs(name: str, srid: int):
    if not name or type(srid) is not int or srid <= 0:
        raise ValueError("Invalid CRS registration")
    _crs[name] = srid


def _invalid(code="GEOMETRY_INVALID"):
    raise ApiError(code, 422, "Geometry must be finite, nonempty, valid and in its declared CRS")


def normalize(uow, geometry):
    srid = _crs.get(geometry.source_crs)
    if srid is None:
        _invalid("GEOMETRY_CRS_UNSUPPORTED")
    value = geometry.geojson
    if value.get("type") not in ("Point", "MultiPoint", "LineString", "MultiLineString",
                                 "Polygon", "MultiPolygon"):
        _invalid()
    if "crs" in value and value["crs"] != {"type": "name", "properties": {"name": geometry.source_crs}}:
        _invalid("GEOMETRY_CRS_CONFLICT")
    count = 0
    def coordinates(part):
        nonlocal count
        if not isinstance(part, list) or not part:
            _invalid()
        if isinstance(part[0], (float, int)):
            if len(part) != 2 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                                     not math.isfinite(v) for v in part):
                _invalid()
            if srid in (4326, 4490) and not (-180 <= part[0] <= 180 and -90 <= part[1] <= 90):
                _invalid("GEOMETRY_OUT_OF_RANGE")
            count += 1
            if count > 100000:
                _invalid("GEOMETRY_BUDGET_EXCEEDED")
        else:
            for child in part:
                coordinates(child)
    coordinates(value.get("coordinates"))
    parameters = {"value": json.dumps(value, allow_nan=False), "srid": srid}
    try:
        with uow.session.begin_nested():
            valid = uow.session.execute(text("""WITH source AS (
                SELECT public.ST_SetSRID(public.ST_GeomFromGeoJSON(:value), :srid) AS geom)
                SELECT public.ST_IsEmpty(geom) AS empty,
                    (public.ST_IsValidDetail(geom)).valid AS valid
                FROM source"""), parameters).mappings().one()
            if valid["empty"] or not valid["valid"]:
                _invalid()
            row = uow.session.execute(text("""WITH normalized AS (
                SELECT public.ST_Transform(public.ST_SetSRID(public.ST_GeomFromGeoJSON(:value), :srid),4326) AS geom)
                SELECT public.ST_AsGeoJSON(geom) AS geojson,
                    public.ST_Area(geom::public.geography) AS area_square_metres,
                    public.ST_IsValid(geom) AS valid, public.ST_IsEmpty(geom) AS empty,
                    public.ST_XMin(public.Box3D(geom)) AS xmin, public.ST_XMax(public.Box3D(geom)) AS xmax,
                    public.ST_YMin(public.Box3D(geom)) AS ymin, public.ST_YMax(public.Box3D(geom)) AS ymax
                FROM normalized"""), parameters).mappings().one()
    except DBAPIError:
        _invalid("GEOMETRY_PARSE_OR_TRANSFORM_FAILED")
    if (not row["valid"] or row["empty"] or not -180 <= row["xmin"] <= row["xmax"] <= 180
            or not -90 <= row["ymin"] <= row["ymax"] <= 90):
        _invalid()
    return {"normalized": json.loads(row["geojson"]), "area_square_metres": row["area_square_metres"],
            "area_method": "PostGIS ST_Area(geography), square metres",
            "coordinate_method": f"ST_Transform EPSG:{srid} to EPSG:4326; x/y order"}


def write_geometry(uow, actor, geometry, bundle):
    facts.fact_validation.begin(uow)
    row = facts.get_object_row(uow, actor, geometry.resource_id, "edit")
    document = facts.effective_bundle(uow, actor, bundle)
    row = facts.get_object_row(uow, actor, geometry.resource_id, "edit")
    if row["revision"] != geometry.expected_revision:
        raise Conflict("REVISION_CONFLICT", "Object revision changed")
    if row["payload"]["state"] == "inactive":
        raise Conflict("OBJECT_INACTIVE", "Object is inactive")
    effect = {"operation": "geometry.write", "geometry": geometry.model_dump(mode="json")}
    refs = facts.authorize_effect(uow, actor, document, bundle, effect,
        geometry_support=geometry.source_support, decision_ref=geometry.decision_ref)
    normalized = normalize(uow, geometry)
    payload = {**geometry.model_dump(mode="json"), **normalized,
        "revision": row["revision"] + 1, "data_nature": row["payload"]["data_nature"]}
    values = facts.effective_inputs(facts.assertion_inputs(facts.current_assertions(uow, row["id"])),
                                   facts.evaluation_time(uow))
    report, validation_refs = facts.check_graph(uow, actor, row["id"], facts.current_classes(uow,actor,row['id']),
                                                values, document, bundle)
    refs.update(validation_refs)
    receipt = facts.record_state(uow, actor, row, row["revision"], bundle, refs,
        "object.geometry_changed", effect, report, geometry=payload)
    from lzo.gis.service import legacy_index
    legacy_index(uow,row["id"],receipt.revision,payload)
    return receipt.revision


def list_geometry(uow, actor, *, identifier=None, bbox=None, scope_id=None, cursor=None):
    validate_actor(uow, actor)
    if identifier:
        facts.get_object_row(uow, actor, identifier)
    authorized = facts.visible_objects(actor)
    if identifier:
        authorized = authorized.where(facts.objects.c.id == identifier)
    if scope_id:
        authorized = authorized.where(facts.objects.c.payload["owner_scope_id"].astext == str(scope_id))
    if cursor:
        try:
            authorized = authorized.where(facts.objects.c.id > UUID(cursor))
        except ValueError:
            raise ApiError("INVALID_CURSOR", 422, "Invalid cursor") from None
    authorized = authorized.where(facts.objects.c.payload["state"].astext != "inactive")
    ids = list(uow.session.execute(authorized.with_only_columns(facts.objects.c.id)
                                   .order_by(facts.objects.c.id)).scalars())
    bounds = None
    if bbox:
        try:
            bounds = tuple(float(x) for x in bbox.split(","))
            if (len(bounds) != 4 or any(not math.isfinite(v) for v in bounds)
                or not -180 <= bounds[0] <= bounds[2] <= 180 or not -90 <= bounds[1] <= bounds[3] <= 90):
                raise ValueError()
        except ValueError:
            raise ApiError("INVALID_BBOX", 422, "Invalid bounding box") from None
    size = facts.settings_for(uow).list_page_size
    from lzo.gis.service import split
    if split(uow):
        return _list_split(uow,ids,bounds,size)
    features = []
    for resource_id in ids:
        from lzo.semantic.source_rows import selected_geometry
        payload=selected_geometry(uow,resource_id)
        if payload is not None and bounds:
            intersects=uow.session.execute(text('SELECT public.ST_Intersects(public.ST_SetSRID('
                'public.ST_GeomFromGeoJSON(:geo),4326),public.ST_MakeEnvelope(:x1,:y1,:x2,:y2,4326))'),
                {'geo':json.dumps(payload['normalized']),**dict(zip(('x1','y1','x2','y2'),bounds))}).scalar_one()
            if not intersects:payload=None
        if payload is not None:
            features.append({"type": "Feature", "id": str(resource_id), "geometry": payload["normalized"],
                "properties": {"object_id": str(resource_id), "geometry_revision": payload["revision"],
                    "data_nature": payload["data_nature"], "source_crs": payload["source_crs"],
                    "area_square_metres": payload["area_square_metres"]}})
        if len(features) > size:
            break
    watermark = uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
    return GeoFeatureCollection(features=features[:size], projection_watermark=watermark,
        next_cursor=features[size-1]["id"] if len(features) > size else None)


def _list_split(uow,ids,bounds,size):
    from lzo.storage.registry import StorageRegistry
    from lzo.gis.repository import GisRepository
    from lzo.governance.models import resource_heads
    # Gate against current core heads, including newer withdrawals/deactivation.
    # Filtering precedes pagination so stale/unauthorized IDs never consume a page.
    from lzo.synchronization.delivery import require_deliveries, DeliveriesPending, DeliveriesFailed
    pending=False
    failed=False
    synced=[]
    for identifier,event in uow.session.execute(select(resource_heads.c.resource_id,resource_heads.c.event_id).where(resource_heads.c.resource_id.in_(ids))):
        try:
            require_deliveries(uow,event,("gis",))
            synced.append(identifier)
        except DeliveriesFailed:failed=True
        except DeliveriesPending: pending=True
    registry=StorageRegistry.from_settings(facts.settings_for(uow))
    unavailable=False
    current=dict(uow.session.execute(select(facts.objects.c.id,facts.objects.c.revision).where(facts.objects.c.id.in_(ids))).all())
    from lzo.semantic.source_rows import selected_geometry
    selected={identifier:selected_geometry(uow,identifier) for identifier in synced}
    eligible=[identifier for identifier in synced if selected[identifier] is not None]
    rows=[]
    try:
        after=None
        while True:
            batch=GisRepository(registry).batch(eligible,bbox=bounds,cursor=after,limit=size+1)
            for indexed in batch:
                identifier=indexed["resource_id"]
                if current.get(identifier)==indexed["revision"] and indexed["payload"]==selected[identifier]:
                    rows.append(indexed)
                else:pending=True
            if len(rows)>size or len(batch)<size+1:break
            after=batch[-1]["resource_id"]
    except DBAPIError:
        rows=[]
        unavailable=True
    finally:
        registry.close()
    features=[]
    for row in rows:
        payload=row["payload"]
        features.append({"type":"Feature","id":str(row["resource_id"]),"geometry":payload["normalized"],
            "properties":{"object_id":str(row["resource_id"]),"geometry_revision":payload["revision"],
                "data_nature":payload["data_nature"],"source_crs":payload["source_crs"],
                "area_square_metres":payload["area_square_metres"]}})
    watermark=uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
    return GeoFeatureCollection(features=features[:size],projection_watermark=watermark,
        next_cursor=features[size-1]["id"] if len(features)>size else None,
        gis_status="unavailable" if unavailable else "failed" if failed else "pending" if pending else "current")
