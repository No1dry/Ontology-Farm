"""Core evidence/legacy boundary and durable index scheduling."""
import json
from uuid import UUID
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from lzo.governance.models import revisions
from lzo.semantic.facts import settings_for
from lzo.synchronization.contracts import SyncEnvelope
from lzo.synchronization.delivery import enqueue_delivery
from lzo.gis.repository import apply_geometry


def split(uow):
    return settings_for(uow).storage_mode == "split"


def accepted_geometry(uow, identifier):
    if split(uow):
        from lzo.semantic.facts import objects, OBJECT_EVENTS
        from lzo.errors import Conflict
        # The stored object revision is the snapshot boundary. A recognized
        # legacy attachment tail has no spatial effect; unknown newer events
        # must never make us silently resurrect an older geometry.
        rows = uow.session.execute(select(revisions).where(revisions.c.resource_id == identifier,
            revisions.c.revision >= select(objects.c.revision).where(objects.c.id == identifier).scalar_subquery())
            .order_by(revisions.c.revision)).mappings().all()
        if rows and (rows[0]['event_type'] not in OBJECT_EVENTS or
                any(row['event_type'] != 'evidence.attached' for row in rows[1:])):
            raise Conflict('OBJECT_SNAPSHOT_INCONSISTENT', 'Object history requires governed reconciliation')
        payload = rows[0]['payload'] if rows else None
        return payload.get("geometry") if payload else None
    return uow.session.execute(text("SELECT payload FROM semantic_geometry WHERE resource_id=:id AND active"),
                               {"id":identifier}).scalar_one_or_none()


def legacy_index(uow, identifier, revision, payload):
    """Never dual-write a mutable legacy head after split cutover."""
    if split(uow):
        return
    if payload is None:
        uow.session.execute(text("UPDATE semantic_geometry SET active=false WHERE resource_id=:id"), {"id":identifier})
        return
    uow.session.execute(text("""INSERT INTO semantic_geometry(resource_id,revision,event_id,geom,payload)
        VALUES(:id,:revision,:event,public.ST_SetSRID(public.ST_GeomFromGeoJSON(:geo),4326),CAST(:payload AS jsonb))
        ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision,event_id=excluded.event_id,
          geom=excluded.geom,payload=excluded.payload,active=true"""),
        dict(id=identifier,revision=revision.revision,event=revision.event_id,
             geo=json.dumps(payload["normalized"]),payload=json.dumps(payload)))


def schedule_geometry(uow, ref, snapshot):
    if not split(uow):
        return
    geometry = snapshot["geometry"]
    effect=snapshot["effect"]
    # Every object revision carries a full index state, including tombstones.
    envelope = SyncEnvelope(event_id=ref.event_id,entity_id=ref.resource_id,aggregate_revision=ref.revision,
        target="gis",operation="geometry.index",payload={"geometry":geometry,
        "active":snapshot["object"]["state"] != "inactive","asset":effect.get("asset"),
        "observation":effect.get("observation")},data_nature=snapshot["object"]["data_nature"],
        source_refs=tuple(sorted(str(p["resource_id"]) for p in snapshot["dependency_revisions"])))
    enqueue_delivery(uow,envelope)


def _artifact(uow,actor,version_id,expected_hash=None):
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.contracts import BlobRef
    from lzo.errors import Conflict
    settings=settings_for(uow)
    store=get_blob_store(settings)
    row=ArtifactService(uow,store,settings)._version(actor,version_id)
    if expected_hash is not None and row["sha256"]!=expected_hash:
        raise Conflict("SPATIAL_ASSET_HASH_MISMATCH","Artifact hash differs from registered package")
    with store.open(BlobRef(row["sha256"],row["size_bytes"],row["media_type"],row["id"])):
        pass
    return row


def _existing_registration(uow,identifier,key,identity,payload):
    from lzo.contracts import RevisionRef
    from lzo.errors import Conflict
    # Identity is immutable across retries, including retries with a new HTTP
    # idempotency key. Expected revision is transport concurrency, not content.
    rows=uow.session.execute(select(revisions).where(
        revisions.c.payload["effect"][key][identity].astext==str(payload[identity]))).mappings()
    for row in rows:
        previous=row["payload"]["effect"][key]
        if row["resource_id"]!=identifier or previous!=payload:
            raise Conflict("GIS_IDENTITY_CONFLICT","GIS registration identity already has different content")
        return RevisionRef(row["resource_id"],row["revision"],row["event_id"])
    return None


def _survey_evidence(uow,actor,version_id,*,link_target=None,verified=False):
    """Evidence authority comes from its controlled owner/version, never JSON shape."""
    from lzo.ingestion.evidence import EvidenceService
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.errors import ApiError
    settings=settings_for(uow)
    store=get_blob_store(settings)
    evidence=EvidenceService(uow,store,settings).read(actor,version_id)
    if link_target is not None:
        # Existing linking policy checks evidence.manage, source.manage, the
        # new target and all pinned targets. Authorization does not mutate evidence.
        ArtifactService(uow,store,settings).authorize_link(actor,version_id,link_target)
    if verified and evidence.data_nature!="verified":
        raise ApiError("SURVEY_NOT_VERIFIED",422,"Survey evidence is not verified")
    return _artifact(uow,actor,version_id)


def register_spatial_asset(uow,actor,command,bundle):
    from types import SimpleNamespace
    from lzo.semantic import facts
    from lzo.semantic.geometry import normalize
    from lzo.errors import Conflict, ApiError
    from lzo.governance.revisions import lock_ledger
    if not split(uow):raise ApiError("GIS_SPLIT_REQUIRED",409,"Spatial registration requires split GIS storage")
    lock_ledger(uow)
    facts.fact_validation.begin(uow)
    row=facts.get_object_row(uow,actor,command.resource_id,"edit")
    document=facts.effective_bundle(uow,actor,bundle)
    artifact=_artifact(uow,actor,command.artifact_version_id,command.sha256)
    refs={artifact["resource_id"]}
    if command.survey_evidence_version_id:
        survey=_survey_evidence(uow,actor,command.survey_evidence_version_id,link_target=command.resource_id,verified=command.quality=="verified")
        refs.add(survey["resource_id"])
    if command.quality=="verified" and row["payload"]["data_nature"]!="verified":
        raise ApiError("SPATIAL_SOURCE_NOT_VERIFIED",422,"Verified quality requires verified source nature")
    normalized=normalize(uow,SimpleNamespace(source_crs=command.horizontal_datum,geojson=command.footprint))
    payload=command.model_dump(mode="json",exclude={"expected_revision"})|normalized
    previous=_existing_registration(uow,command.resource_id,"asset","asset_id",payload)
    if previous:return previous
    if row["revision"]!=command.expected_revision:raise Conflict("REVISION_CONFLICT","Object revision changed")
    if row["payload"]["state"]=="inactive":raise Conflict("OBJECT_INACTIVE","Object is inactive")
    effect={"operation":"spatial.asset.register","asset":payload}
    refs.update(facts.authorize_effect(uow,actor,document,bundle,effect))
    return facts.record_state(uow,actor,row,command.expected_revision,bundle,refs,"object.spatial_asset_registered",effect).revision


def register_location_observation(uow,actor,command,bundle):
    from lzo.semantic import facts
    from lzo.errors import Conflict, ApiError
    from lzo.governance.revisions import lock_ledger
    if not split(uow):raise ApiError("GIS_SPLIT_REQUIRED",409,"Location registration requires split GIS storage")
    lock_ledger(uow)
    facts.fact_validation.begin(uow)
    row=facts.get_object_row(uow,actor,command.resource_id,"edit")
    document=facts.effective_bundle(uow,actor,bundle)
    payload=command.model_dump(mode="json",exclude={"expected_revision"})
    previous=_existing_registration(uow,command.resource_id,"observation","observation_id",payload)
    if previous:return previous
    if row["revision"]!=command.expected_revision:raise Conflict("REVISION_CONFLICT","Object revision changed")
    if row["payload"]["state"]=="inactive":raise Conflict("OBJECT_INACTIVE","Object is inactive")
    effect={"operation":"location.observation.register","observation":payload}
    refs=facts.authorize_effect(uow,actor,document,bundle,effect)
    return facts.record_state(uow,actor,row,command.expected_revision,bundle,refs,"object.location_observed",effect).revision


def list_spatial_assets(uow,actor,identifier,*,limit=None,cursor=None):
    from lzo.semantic import facts
    from lzo.storage.registry import StorageRegistry
    from lzo.gis.repository import GisRepository
    from lzo.synchronization.delivery import require_deliveries, DeliveriesPending, DeliveriesFailed
    from lzo.governance.models import resource_heads
    from lzo.errors import ApiError,Conflict
    import base64
    import binascii
    facts.get_object_row(uow,actor,identifier)
    if not split(uow):raise ApiError("GIS_SPLIT_REQUIRED",409,"Spatial registration requires split GIS storage")
    event=uow.session.execute(select(resource_heads.c.event_id).where(resource_heads.c.resource_id==identifier)).scalar_one()
    settings=settings_for(uow)
    size=limit if limit is not None else min(settings.list_page_size,settings.list_max_page_size,1000)
    if type(size) is not int or not 1<=size<=min(settings.list_max_page_size,1000):
        raise ApiError("INVALID_LIMIT",422,"Invalid asset page size")
    after=None
    if cursor is not None:
        try:
            if len(cursor)>1024:raise ValueError()
            value=json.loads(base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True))
            if not isinstance(value,dict) or set(value)!={'version','resource','event','actor','authorization','after'} or type(value['version']) is not int or value['version']!=1:raise ValueError()
            if any(not isinstance(value[key],str) for key in ('resource','actor','event','after')):raise ValueError()
            if UUID(value['resource'])!=identifier or UUID(value['actor'])!=actor.subject_id:raise ValueError()
            after=UUID(value['after'])
            cursor_event=UUID(value['event'])
            if type(value['authorization']) is not int:raise ValueError()
        except (ValueError,TypeError,KeyError,binascii.Error):
            raise ApiError("INVALID_CURSOR",422,"Invalid asset cursor") from None
        if cursor_event!=event or value['authorization']!=actor.authorization_version:
            raise Conflict("GIS_CURSOR_STALE","Object or authorization changed; restart asset pagination")
    try:
        require_deliveries(uow,event,("gis",))
        _require_history(uow,identifier,"object.spatial_asset_registered")
    except DeliveriesFailed:return {"status":"failed","assets":[],"next_cursor":None}
    except DeliveriesPending:return {"status":"pending","assets":[],"next_cursor":None}
    registry=StorageRegistry.from_settings(settings)
    output=[]
    try:
        while len(output)<=size:
            rows=GisRepository(registry).assets([identifier],cursor=after,limit=size+1)
            for asset in rows:
                after=UUID(asset['asset_id'])
                try:
                    _artifact(uow,actor,UUID(asset["artifact_version_id"]),asset["sha256"])
                    if asset.get("survey_evidence_version_id"):
                        _survey_evidence(uow,actor,UUID(asset["survey_evidence_version_id"]),verified=asset.get("quality")=="verified")
                except ApiError as error:
                    if error.status == 403 or (error.status == 404 and error.code == 'NOT_FOUND'):
                        continue
                    raise
                output.append(asset)
                if len(output)>size:break
            if len(rows)<size+1:break
    except DBAPIError:return {"status":"unavailable","assets":[],"next_cursor":None}
    finally:registry.close()
    next_cursor=None
    if len(output)>size:
        value={'version':1,'resource':str(identifier),'event':str(event),'actor':str(actor.subject_id),
            'authorization':actor.authorization_version,'after':output[size-1]['asset_id']}
        next_cursor=base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
    return {"status":"current","assets":output[:size],"next_cursor":next_cursor}


def list_location_observations(uow,actor,identifier,*,start=None,end=None,cursor=None):
    from lzo.semantic import facts
    from lzo.errors import ApiError
    from lzo.gis.repository import GisRepository
    from lzo.storage.registry import StorageRegistry
    from lzo.governance.models import resource_heads
    from lzo.synchronization.delivery import require_deliveries, DeliveriesPending, DeliveriesFailed
    facts.get_object_row(uow,actor,identifier)
    if not split(uow):raise ApiError("GIS_SPLIT_REQUIRED",409,"Location history requires split GIS storage")
    event=uow.session.execute(select(resource_heads.c.event_id).where(resource_heads.c.resource_id==identifier)).scalar_one()
    try:
        require_deliveries(uow,event,("gis",))
        _require_history(uow,identifier,"object.location_observed")
    except DeliveriesFailed:return {"status":"failed","observations":[],"next_cursor":None}
    except DeliveriesPending:return {"status":"pending","observations":[],"next_cursor":None}
    settings=settings_for(uow)
    registry=StorageRegistry.from_settings(settings)
    try:rows=GisRepository(registry).observations([identifier],start=start,end=end,cursor=cursor,limit=settings.list_page_size+1)
    except DBAPIError:return {"status":"unavailable","observations":[],"next_cursor":None}
    finally:registry.close()
    last=rows[settings.list_page_size-1] if len(rows)>settings.list_page_size else None
    return {"status":"current","observations":rows[:settings.list_page_size],
        "next_cursor":[last["observed_at"],last["resource_id"],last["observation_id"]] if last else None}


def _require_history(uow,identifier,event_type):
    from lzo.synchronization.delivery import require_deliveries
    for event in uow.session.execute(select(revisions.c.event_id).where(
            revisions.c.resource_id==identifier,revisions.c.event_type==event_type)).scalars():
        require_deliveries(uow,event,("gis",))


def retry_failed_projection_deliveries(uow,actor,job):
    """Called only by the existing authorized, locked failed-job retry command."""
    if job["kind"]!="rdf.project" or job["error_code"]!="DOMAIN_DELIVERY_FAILED":return None
    from lzo.contracts import RevisionRef
    from lzo.semantic.projection import ProjectionService,_revision
    from lzo.semantic.facts import OBJECT_EVENTS
    from lzo.auth.permissions import require_permission
    from lzo.worker.service import resource
    from lzo.synchronization.delivery import DeliveryConflict
    ref=RevisionRef(UUID(job["payload"]["resource_id"]),job["payload"]["revision"],UUID(job["payload"]["event_id"]))
    _,_,content,_=ProjectionService(uow,None)._content(ref)
    recovered=[]
    for dependency in (ref,*content.dependency_revisions):
        revision=_revision(uow,dependency)
        if revision["event_type"] not in OBJECT_EVENTS:continue
        row=uow.session.execute(text("SELECT * FROM store_deliveries WHERE event_id=:id AND target='gis' FOR UPDATE"),
            {"id":dependency.event_id}).mappings().first()
        if row is None or row["status"]!="failed":continue
        # Editing a primary job does not grant edit access to a pinned object.
        require_permission(actor,"edit",resource(uow,dependency.resource_id),uow=uow)
        envelope=SyncEnvelope.model_validate(row["envelope"])
        if (envelope.event_id!=dependency.event_id or envelope.entity_id!=dependency.resource_id
            or envelope.aggregate_revision!=dependency.revision or envelope.target!="gis"
            or envelope.envelope_hash!=row["envelope_hash"] or envelope.payload_hash!=row["payload_hash"]):
            raise DeliveryConflict("Stored GIS envelope does not match the exact accepted revision")
        recovered.append({"event_id":str(dependency.event_id),"resource_id":str(dependency.resource_id),
            "revision":dependency.revision,"target":"gis","previous_attempts":row["attempts"],
            "previous_failure":row["error"],"envelope_hash":row["envelope_hash"]})
        uow.session.execute(text("UPDATE store_deliveries SET status='retry',attempts=0,error=NULL,available_at=clock_timestamp() WHERE event_id=:id AND target='gis' AND status='failed'"),
            {"id":dependency.event_id})
    return recovered
