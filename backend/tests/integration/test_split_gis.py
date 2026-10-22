"""Independent PostGIS index, immutable revisions and authorization input."""
from uuid import uuid4
import pytest
from sqlalchemy import text
from test_store_delivery import stores, envelope, enqueue


def test_geometry_index_is_independent_filtered_and_monotonic(stores):
    from lzo.gis.repository import GisRepository, apply_geometry
    from lzo.synchronization.delivery import deliver_once
    identifier = uuid4()
    payload = {"geometry": {"normalized": {"type": "Point", "coordinates": [114, 30]},
        "revision": 2, "source_crs": "EPSG:4326"}, "active": True}
    item = envelope(entity_id=identifier, aggregate_revision=2, operation="geometry.index", payload=payload)
    enqueue(stores, item)
    repository = GisRepository(stores)
    assert repository.batch([identifier]) == []
    deliver_once(stores, "gis", apply_geometry)
    assert repository.batch([]) == []
    rows = repository.batch([identifier], bbox=(113, 29, 115, 31))
    assert rows[0]["resource_id"] == identifier
    assert rows[0]["payload"]["normalized"]["coordinates"] == [114, 30]
    assert repository.batch([identifier], bbox=(0, 0, 1, 1)) == []
    old = envelope(entity_id=identifier, aggregate_revision=1, operation="geometry.index", payload={"geometry": None, "active": False})
    enqueue(stores, old)
    deliver_once(stores, "gis", apply_geometry)
    assert repository.batch([identifier])[0]["revision"] == 2


def test_gis_version_and_receipt_survive_ack_loss(stores, monkeypatch):
    from lzo.gis.repository import apply_geometry
    from lzo.synchronization import delivery
    item = envelope(operation="geometry.index", payload={"geometry": None, "active": False})
    enqueue(stores, item)
    original = delivery._acknowledge
    monkeypatch.setattr(delivery, "_acknowledge", lambda *a: (_ for _ in ()).throw(ConnectionError()))
    assert delivery.deliver_once(stores, "gis", apply_geometry).failed >= 1
    monkeypatch.setattr(delivery, "_acknowledge", original)
    with stores.engine("core").begin() as c:
        c.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"), {"id": item.event_id})
    delivery.deliver_once(stores, "gis", apply_geometry)
    with stores.engine("gis").connect() as c:
        assert c.execute(text("SELECT count(*) FROM gis_geometry_versions WHERE event_id=:id"), {"id": item.event_id}).scalar_one() == 1


def test_late_history_rollback_and_ack_loss_never_change_current_heads(stores,monkeypatch):
    from lzo.gis.repository import apply_geometry,apply_geometry_history
    from lzo.synchronization import delivery
    newer=envelope(aggregate_revision=3,operation="geometry.index",payload={"geometry":None,"active":False})
    enqueue(stores,newer)
    delivery._apply(stores,newer,apply_geometry)
    older=envelope(entity_id=newer.entity_id,aggregate_revision=2,operation="geometry.index",payload={"geometry":None,"active":True})
    enqueue(stores,older)
    def failing(connection,item):
        apply_geometry_history(connection,item)
        raise RuntimeError("synthetic history failure")
    delivery.register_delivery_handler("gis",apply_geometry,history_handler=failing)
    try:
        delivery.deliver_once(stores,"gis",apply_geometry)
        with stores.engine("gis").connect() as c:
            assert c.execute(text("SELECT count(*) FROM gis_geometry_versions WHERE event_id=:id"),{"id":older.event_id}).scalar_one()==0
            assert c.execute(text("SELECT count(*) FROM store_inbox WHERE event_id=:id"),{"id":older.event_id}).scalar_one()==0
        delivery.register_delivery_handler("gis",apply_geometry,history_handler=apply_geometry_history)
        original=delivery._acknowledge
        monkeypatch.setattr(delivery,"_acknowledge",lambda *args:(_ for _ in ()).throw(ConnectionError()))
        with stores.engine("core").begin() as c:c.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"),{"id":older.event_id})
        delivery.deliver_once(stores,"gis",apply_geometry)
        monkeypatch.setattr(delivery,"_acknowledge",original)
        with stores.engine("core").begin() as c:c.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"),{"id":older.event_id})
        delivery.deliver_once(stores,"gis",apply_geometry)
        with stores.engine("gis").connect() as c:
            assert c.execute(text("SELECT count(*) FROM gis_geometry_versions WHERE event_id=:id"),{"id":older.event_id}).scalar_one()==1
            assert c.execute(text("SELECT aggregate_revision FROM store_heads WHERE entity_id=:id"),{"id":newer.entity_id}).scalar_one()==3
            assert c.execute(text("SELECT revision FROM gis_geometry_heads WHERE resource_id=:id"),{"id":newer.entity_id}).scalar_one()==3
    finally:delivery.register_delivery_handler("gis",apply_geometry,history_handler=apply_geometry_history)


def test_target_unavailable_is_retryable_and_core_reads_continue(stores):
    from sqlalchemy import create_engine
    from lzo.gis.repository import apply_geometry
    from lzo.synchronization.delivery import deliver_once, delivery_status, require_deliveries, DeliveriesPending
    from lzo.db import PgUnitOfWork
    item=envelope(operation="geometry.index",payload={"geometry":None,"active":False})
    enqueue(stores,item)
    original=stores._engines["gis"]
    unavailable=create_engine(original.url.set(port=1),connect_args={"connect_timeout":1})
    stores._engines["gis"]=unavailable
    try:
        deliver_once(stores,"gis",apply_geometry)
        with PgUnitOfWork(stores.engine("core")) as uow:
            assert uow.session.execute(text("SELECT 1")).scalar_one()==1
            assert delivery_status(uow,item.event_id)[0].status=="retry"
            with pytest.raises(DeliveriesPending): require_deliveries(uow,item.event_id,("gis",))
    finally:
        stores._engines["gis"]=original
        unavailable.dispose()
    with stores.engine("core").begin() as c:
        c.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"),{"id":item.event_id})
    deliver_once(stores,"gis",apply_geometry)
    with PgUnitOfWork(stores.engine("core")) as uow:require_deliveries(uow,item.event_id,("gis",))


def test_split_geometry_pending_read_and_publication_barrier(object_case):
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.delivery import deliver_once, DeliveriesPending
    from lzo.gis.repository import apply_geometry
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.contracts import RevisionRef
    from uuid import UUID
    s = object_case
    obj = s.create_object().json()["revision"]["resource_id"]
    geometry = {"resource_id":obj,"expected_revision":1,"source_crs":"EPSG:4326",
        "geojson":{"type":"Point","coordinates":[114,30]},"source_support":s.assertion(obj)["support"]}
    response = s.client.put(f"/api/v1/geometry/{obj}",headers=s.headers(1),json={"geometry":geometry,"bundle_id":str(s.bundle_id)})
    assert response.status_code == 200, response.text
    collection = s.client.get(f"/api/v1/geometry/{obj}").json()
    assert collection["gis_status"] == "pending"
    assert collection["features"] == []
    assert s.client.get(f"/api/v1/objects/{obj}").status_code==200
    ref = RevisionRef(**{k:UUID(v) if k != "revision" else v for k,v in response.json().items()})
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        with pytest.raises(DeliveriesPending):
            ProjectionService(uow,FusekiRepository(s.config.fuseki_url)).project_revision(ref)
    runner=s.runner()
    ctx=runner.claim()
    assert ctx is not None
    runner.finish(ctx,DeliveriesPending())
    with s.transaction() as uow:
        state=uow.session.execute(text("SELECT status,retry_count,available_at>clock_timestamp() AS delayed FROM jobs WHERE id=:id"),{"id":ctx.job_id}).mappings().one()
        assert state["status"]=="queued" and state["retry_count"]==0 and state["delayed"]
    # Pausing for more attempts than the normal retry budget does not kill RDF.
    for _ in range(5):
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE jobs SET available_at=clock_timestamp()-interval '1 hour' WHERE id=:id"),{"id":ctx.job_id})
            uow.commit()
        again=runner.claim()
        assert again.job_id==ctx.job_id
        runner.finish(again,DeliveriesPending())
    with s.transaction() as uow:
        assert uow.session.execute(text("SELECT retry_count FROM jobs WHERE id=:id"),{"id":ctx.job_id}).scalar_one()==0
    # A real independent worker handler progresses while GIS is paused.
    from lzo.worker.service import register_handler,enqueue
    def independent(ctx):
        with ctx.batch():ctx.checkpoint({"progress":True})
    register_handler("task3.independent",independent,action="view")
    with s.transaction() as uow:
        job=enqueue(uow,s.actor("alice"),"task3.independent",{"resource_id":obj},None)
        uow.commit()
    for _ in range(3):runner.run_once()
    with s.transaction() as uow:
        assert uow.session.execute(text("SELECT status FROM jobs WHERE id=:id"),{"id":job.id}).scalar_one()=="succeeded"
    registry=StorageRegistry.from_settings(s.config)
    registry._engines["core"].dispose()
    registry._engines["core"]=s.app
    try:
        deliver_once(registry,"gis",apply_geometry)
        result=s.client.get(f"/api/v1/geometry/{obj}").json()
        assert result["gis_status"]=="current"
        assert result["features"][0]["id"]==obj
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            assert ProjectionService(uow,FusekiRepository(s.config.fuseki_url)).project_revision(ref).state=="synced"
            uow.commit()
        # An exhausted target is an explicit failed dependency, not a healthy
        # delayed wait. Preserve the same RDF job/event for operator recovery.
        from lzo.synchronization.delivery import DeliveriesFailed,require_deliveries
        failure=s.create_object().json()["revision"]
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET status='failed',attempts=3 WHERE event_id=:id"),{"id":failure["event_id"]})
            uow.commit()
            with pytest.raises(DeliveriesFailed):require_deliveries(uow,UUID(failure["event_id"]),("gis",))
        assert s.client.get(f"/api/v1/geometry/{failure['resource_id']}").json()["gis_status"]=="failed"
        for _ in range(4):runner.run_once()
        with s.transaction() as uow:
            job_state=uow.session.execute(text("SELECT id,revision,status,error_code FROM jobs WHERE payload->>'event_id'=:id"),{"id":failure["event_id"]}).mappings().one()
            assert job_state["status"]=="failed" and job_state["error_code"]=="DOMAIN_DELIVERY_FAILED"
        unrelated=s.create_object().json()["revision"]
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET status='failed',attempts=3 WHERE event_id=:id"),{"id":unrelated["event_id"]})
            before=uow.session.execute(text("SELECT envelope_hash FROM store_deliveries WHERE event_id=:id"),{"id":failure["event_id"]}).scalar_one()
            uow.commit()
        s.login("nobody")
        denied=s.client.post(f"/api/v1/jobs/{job_state['id']}/retry",headers=s.headers(job_state["revision"]))
        assert denied.status_code==404
        s.login("alice")
        headers=s.headers(job_state["revision"])
        retried=s.client.post(f"/api/v1/jobs/{job_state['id']}/retry",headers=headers)
        assert retried.status_code==200,retried.text
        assert s.client.post(f"/api/v1/jobs/{job_state['id']}/retry",headers=headers).json()==retried.json()
        with s.transaction() as uow:
            delivery=uow.session.execute(text("SELECT status,attempts,envelope_hash FROM store_deliveries WHERE event_id=:id"),{"id":failure["event_id"]}).mappings().one()
            assert delivery["status"]=="retry" and delivery["attempts"]==0 and delivery["envelope_hash"]==before
            assert uow.session.execute(text("SELECT status FROM store_deliveries WHERE event_id=:id"),{"id":unrelated["event_id"]}).scalar_one()=="failed"
            audit=uow.session.execute(text("SELECT payload FROM revisions WHERE resource_id=:id ORDER BY revision DESC LIMIT 1"),{"id":job_state["id"]}).scalar_one()
            assert audit["dependency_recovery"][0]["previous_attempts"]==3
        deliver_once(registry,"gis",apply_geometry)
        for _ in range(5):runner.run_once()
        with s.transaction() as uow:
            assert uow.session.execute(text("SELECT status FROM jobs WHERE id=:id"),{"id":job_state["id"]}).scalar_one()=="succeeded"
    finally:
        registry.close()


def test_spatial_asset_requires_survey_and_preserves_unknown_height():
    from lzo.gis.schemas import SpatialAssetInput, PositionInput
    from pydantic import ValidationError
    raw=dict(resource_id=uuid4(),expected_revision=1,asset_id=uuid4(),artifact_version_id=uuid4(),
        sha256="a"*64,footprint={"type":"Point","coordinates":[114,30]},horizontal_datum="EPSG:4326",
        vertical_datum=None,captured_at="2026-10-22T00:00:00Z",accuracy_metres=None,source="synthetic fixture",quality="verified")
    with pytest.raises(ValidationError): SpatialAssetInput(**raw)
    asset=SpatialAssetInput(**{**raw,"quality":"display_only"})
    assert asset.accuracy_metres is None
    position=PositionInput(observed_at="2026-10-22T00:00:00Z",horizontal_datum="EPSG:4326",longitude=114,latitude=30)
    assert position.height_metres is None


def test_split_crs_pagination_and_current_revocation(object_case):
    from uuid import UUID
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.delivery import deliver_once
    from lzo.gis.repository import apply_geometry
    from lzo.auth import models
    from sqlalchemy import update
    s=object_case
    s.config=s.config.model_copy(update={"list_page_size":1})
    s.client.app.state.settings=s.config
    registry=StorageRegistry.from_settings(s.config)
    registry._engines["core"].dispose();registry._engines["core"]=s.app
    identifiers=[]
    for crs,xy in (("EPSG:4326",[114,30]),("EPSG:3857",[12690422,3503549]),("EPSG:4490",[114,30])):
        if not identifiers:
            hidden=UUID(int=uuid4().int>>64)
            response=s.client.post("/api/v1/objects",headers=s.headers(),json={"bundle_id":str(s.bundle_id),"create":{
                "resource_id":str(hidden),"class_iris":["urn:Pond"],"display_name":"Unshared fixture",
                "owner_scope_id":str(s.scopes["farm_a"]),"related_scope_ids":[],"state":"active","data_nature":"synthetic","assertions":[]}})
        else:response=s.create_object()
        assert response.status_code==201,response.text
        obj=response.json()["revision"]["resource_id"]
        identifiers.append(obj)
        geometry={"resource_id":obj,"expected_revision":1,"source_crs":crs,"geojson":{"type":"Point","coordinates":xy},
            "source_support":s.assertion(obj)["support"]}
        response=s.client.put(f"/api/v1/geometry/{obj}",headers=s.headers(1),json={"geometry":geometry,"bundle_id":str(s.bundle_id)})
        assert response.status_code==200,response.text
        for invalid in ("EPSG:999999","unknown"):
            geometry.update(expected_revision=2,source_crs=invalid)
            rejected=s.client.put(f"/api/v1/geometry/{obj}",headers=s.headers(2),json={"geometry":geometry,"bundle_id":str(s.bundle_id)})
            assert rejected.status_code==422
    deliver_once(registry,"gis",apply_geometry)
    page=s.client.get("/api/v1/geometry?bbox=113,29,115,31").json()
    assert len(page["features"])==1 and page["next_cursor"]
    seen=[page["features"][0]["id"]]
    while page["next_cursor"]:
        page=s.client.get("/api/v1/geometry",params={"bbox":"113,29,115,31","cursor":page["next_cursor"]}).json()
        seen.extend(f["id"] for f in page["features"])
    assert seen==sorted(identifiers,key=UUID)
    s.ids_by_name["shared_gis_device"]=UUID(identifiers[1])
    shared_grant=s.grant("nobody","viewer",resource="shared_gis_device")
    s.login("nobody")
    shared=s.client.get("/api/v1/geometry?bbox=113,29,115,31").json()
    assert len(shared["features"])==1 and shared["features"][0]["id"]!=str(hidden)
    s.revoke(shared_grant)
    assert s.client.get("/api/v1/geometry").json()["features"]==[]
    assert s.client.get(f"/api/v1/geometry/{identifiers[1]}").status_code==404
    s.login("alice")
    # GIS keeps its stale copied history; the latest core grant controls reads.
    s.revoke(s.object_editor_grant)
    assert s.client.get("/api/v1/geometry").json()["features"]==[]
    assert s.client.get(f"/api/v1/geometry/{identifiers[0]}").status_code==404
    with s.transaction() as uow:
        assert uow.session.execute(text("SELECT count(*) FROM semantic_geometry")).scalar_one()==0
    registry.close()


def test_observations_do_not_replace_geometry_head(stores):
    from lzo.gis.repository import GisRepository, apply_geometry
    from lzo.synchronization.delivery import deliver_once
    observation={"observation_id":str(uuid4()),"source":"synthetic observation",
        "position":{"observed_at":"2026-10-22T00:00:00Z","horizontal_datum":"EPSG:4326",
            "longitude":114,"latitude":30,"height_metres":None,"vertical_datum":None,"accuracy_metres":None}}
    item=envelope(operation="geometry.index",payload={"geometry":None,"active":True,"observation":observation})
    enqueue(stores,item)
    deliver_once(stores,"gis",apply_geometry)
    assert GisRepository(stores).batch([item.entity_id])==[]
    rows=GisRepository(stores).observations([item.entity_id])
    assert rows[0]["height_metres"] is None
    assert rows[0]["observation_id"]==observation["observation_id"]
    later={**observation,"observation_id":str(uuid4()),"position":{**observation["position"],"observed_at":"2026-10-22T01:00:00Z","height_metres":0,"vertical_datum":"test local zero"}}
    newer=envelope(entity_id=item.entity_id,aggregate_revision=2,operation="geometry.index",
        payload={"geometry":None,"active":True,"observation":later})
    enqueue(stores,newer)
    deliver_once(stores,"gis",apply_geometry)
    first=GisRepository(stores).observations([item.entity_id],limit=1)
    cursor=(first[0]["observed_at"],first[0]["resource_id"],first[0]["observation_id"])
    second=GisRepository(stores).observations([item.entity_id],cursor=cursor,limit=1)
    assert second[0]["height_metres"]==0
    assert GisRepository(stores).observations([],cursor=cursor)==[]


def test_spatial_asset_registration_is_immutable_and_permission_checked(object_case):
    import hashlib
    from uuid import UUID
    from lzo.gis.schemas import SpatialAssetInput
    from lzo.gis.service import register_spatial_asset, list_spatial_assets
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.delivery import deliver_once
    from lzo.gis.repository import apply_geometry
    from lzo.ontology.releases import get_execution_bundle
    from lzo.auth.permissions import permission_context
    from lzo.errors import Conflict, ApiError
    s=object_case
    obj=s.create_object().json()["revision"]["resource_id"]
    content=b"synthetic spatial package; no measured accuracy"
    owner=s.ids_by_name["object_a"]
    response=s.client.post(f"/api/v1/artifacts?resource_id={owner}&filename=synthetic.bin",
        headers={**s.headers(1),"Content-Type":"application/octet-stream"},content=content)
    assert response.status_code==201,response.text
    version=response.json()["version_id"]
    with s.transaction() as uow:
        revision=uow.session.execute(text("SELECT revision FROM semantic_objects WHERE id=:id"),{"id":obj}).scalar_one()
    command=SpatialAssetInput(resource_id=UUID(obj),expected_revision=revision,asset_id=uuid4(),
        artifact_version_id=UUID(version),sha256=hashlib.sha256(content).hexdigest(),
        footprint={"type":"Point","coordinates":[114,30]},horizontal_datum="EPSG:4326",
        captured_at="2026-10-22T00:00:00Z",source="synthetic package",quality="display_only")
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        actor=s.actor("alice")
        bundle=get_execution_bundle(actor,s.bundle_id)
        ref=register_spatial_asset(uow,actor,command,bundle)
        uow.commit()
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        assert register_spatial_asset(uow,s.actor("alice"),command,get_execution_bundle(s.actor("alice"),s.bundle_id))==ref
        assert list_spatial_assets(uow,s.actor("alice"),UUID(obj))["status"]=="pending"
        with pytest.raises(Conflict):register_spatial_asset(uow,s.actor("alice"),command.model_copy(update={"footprint":{"type":"Point","coordinates":[115,30]}}),get_execution_bundle(s.actor("alice"),s.bundle_id))
    replay=s.client.post("/api/v1/spatial-assets",headers=s.headers(command.expected_revision),json={"asset":command.model_dump(mode="json"),"bundle_id":str(s.bundle_id)})
    assert replay.status_code==201 and replay.json()["event_id"]==str(ref.event_id),replay.text
    observation={"resource_id":obj,"expected_revision":ref.revision,"observation_id":str(uuid4()),"source":"synthetic governed observation",
        "position":{"observed_at":"2026-10-22T00:00:00Z","horizontal_datum":"EPSG:4490","longitude":114,"latitude":30}}
    location=s.client.post("/api/v1/location-observations",headers=s.headers(ref.revision),json={"observation":observation,"bundle_id":str(s.bundle_id)})
    assert location.status_code==201,location.text
    repeated=s.client.post("/api/v1/location-observations",headers=s.headers(ref.revision),json={"observation":observation,"bundle_id":str(s.bundle_id)})
    assert repeated.json()==location.json(),repeated.text
    registry=StorageRegistry.from_settings(s.config)
    registry._engines["core"].dispose();registry._engines["core"]=s.app
    # A later full aggregate snapshot arrives before the asset registration.
    from lzo.synchronization.delivery import _apply
    from lzo.synchronization.contracts import SyncEnvelope
    with s.transaction() as uow:
        wire=uow.session.execute(text("SELECT envelope FROM store_deliveries WHERE event_id=:id"),{"id":location.json()["event_id"]}).scalar_one()
    _apply(registry,SyncEnvelope.model_validate(wire),apply_geometry)
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        assert list_spatial_assets(uow,s.actor("alice"),UUID(obj))["status"]=="pending"
    deliver_once(registry,"gis",apply_geometry)
    with registry.engine("gis").connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM gis_geometry_versions WHERE resource_id=:id"),{"id":obj}).scalar_one()==3
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        result=list_spatial_assets(uow,s.actor("alice"),UUID(obj))
        assert result["assets"][0]["quality"]=="display_only"
        assert result["assets"][0]["accuracy_metres"] is None
    history=s.client.get(f"/api/v1/objects/{obj}/location-observations").json()
    assert history["status"]=="current" and history["observations"][0]["height_metres"] is None
    assert s.client.get(f"/api/v1/objects/{obj}/location-observations?cursor=broken").status_code==422
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        with pytest.raises(ApiError): list_spatial_assets(uow,s.actor("nobody"),UUID(obj))
    registry.close()
