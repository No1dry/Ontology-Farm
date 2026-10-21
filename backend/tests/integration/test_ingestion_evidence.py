"""Source authority and every evidence route use actual PG/F04 identities."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func

from lzo.ingestion.sources import SourceService, UploadService
from lzo.ingestion.evidence import EvidenceService


def test_source_configuration_is_trusted_and_creation_grants_no_access(evidence_case):
    s=evidence_case
    forbidden=s.source(nature="verified")
    assert forbidden.status_code == 422
    source=s.source(); assert source.status_code == 201, source.text
    view=source.json()
    assert view["mapping_version_id"] is None and view["connection_revision"] is None
    assert s.http.get(f'/api/v1/sources/{view["id"]}',headers=s.headers()).status_code==200
    assert s.http.get(f'/api/v1/sources/{view["id"]}',headers=s.headers("admin")).status_code==404
    assert s.evidence(view["id"],nature="verified").status_code==422


def test_evidence_hash_dedup_does_not_grant_cross_scope_access(evidence_case):
    s=evidence_case
    a=s.evidence(); b=s.evidence(name="admin",farm="farm_b")
    assert a.status_code==b.status_code==201, (a.text,b.text)
    a,b=a.json(),b.json()
    assert a["id"]!=b["id"] and a["blob"]["sha256"]==b["blob"]["sha256"]
    assert a["id"]==a["blob"]["version_id"]==a["revision"]["resource_id"]
    for path in (f'/artifacts/evidence/{b["id"]}',f'/artifacts/{b["id"]}',f'/artifacts/{b["id"]}/content'):
        response=s.http.get('/api/v1'+path, headers=s.headers())
        assert response.status_code==404 and b["blob"]["sha256"] not in response.text
    assert s.http.get(f'/api/v1/artifacts/{a["id"]}/content',headers=s.headers()).content==b"original"


def test_same_upload_key_changed_bytes_conflicts(evidence_case):
    s=evidence_case
    first=s.evidence(key="stable")
    assert first.status_code==201, first.text
    assert s.evidence(key="stable").json()==first.json()
    assert s.evidence(key="stable",payload=b"changed").status_code==409
    second=s.evidence(payload=b"changed", replaces=first.json()["id"],revision=2)
    assert second.status_code==201, second.text
    assert second.json()["scope"]["resource_id"]==first.json()["scope"]["resource_id"]
    assert s.http.get(f'/api/v1/artifacts/{first.json()["id"]}/content',headers=s.headers()).content==b"original"


def test_evidence_action_revocation_hides_generic_facade_and_history(evidence_case):
    s=evidence_case
    first=s.evidence(); assert first.status_code==201, first.text
    evidence=first.json(); identifier=evidence["id"]
    s.revoke(s.evidence_grant)
    for path in (f'/artifacts/evidence/{identifier}',f'/artifacts/{identifier}',f'/artifacts/{identifier}/content',
                 f'/history/{identifier}/revisions/1',f'/history/{evidence["scope"]["resource_id"]}/revisions/1'):
        response=s.http.get('/api/v1'+path,headers=s.headers())
        assert response.status_code==404, (path,response.text)
    for path,body in (("reviews",{"decision":"accepted","comment":"ok"}),
                      ("links",{"target_resource_id":str(s.ids_by_name["object_a"]),"relation":"supports"})):
        assert s.http.post(f'/api/v1/artifacts/{identifier}/{path}',headers=s.headers(key=str(uuid4())),json=body).status_code==404


def test_exact_pins_all_dependencies_and_generic_write_guards(evidence_case):
    from lzo.governance.models import resource_heads
    s=evidence_case
    source=s.source().json()
    target_grant=s.grant("alice","editor",scope="farm_b")
    with s.transaction() as uow:
        pin=uow.session.execute(select(resource_heads).where(resource_heads.c.resource_id==s.ids_by_name["object_b"])).mappings().one()
    ref={"resource_id":str(pin["resource_id"]),"revision":pin["revision"],"event_id":str(pin["event_id"])}
    assert s.evidence(source["id"],targets=[{**ref,"event_id":str(uuid4())}]).status_code==422
    first=s.evidence(source["id"],targets=[ref]); assert first.status_code==201, first.text
    e=first.json(); identifier=e["id"]
    assert s.http.get(f'/api/v1/history/{identifier}/revisions/1',headers=s.headers()).status_code==200
    forged=s.http.post(f'/api/v1/artifacts?resource_id={e["scope"]["resource_id"]}&filename=forge.pdf',
        headers={**s.headers(key="forge",revision=2),"Content-Type":"application/pdf"},content=b"forge")
    assert forged.status_code==403
    s.revoke(target_grant)
    for path in (f'/artifacts/evidence/{identifier}',f'/artifacts/{identifier}',f'/artifacts/{identifier}/content',
                 f'/history/{identifier}/revisions/1',f'/history/{e["scope"]["resource_id"]}/revisions/1'):
        response=s.http.get('/api/v1'+path,headers=s.headers())
        assert response.status_code==404, (path,response.text)


def test_review_order_is_event_order_and_never_promotes_nature(evidence_case):
    s=evidence_case
    s.grant("admin","evidence_manager",scope="farm_a")
    e=s.evidence().json(); identifier=e["id"]
    body={"decision":"accepted","comment":"Independent review"}
    assert s.http.post(f'/api/v1/artifacts/{identifier}/reviews',headers=s.headers(key="self"),json=body).status_code==403
    for revision,decision,expected in ((1,"accepted","accepted"),(2,"needs_changes","candidate"),(3,"rejected","rejected")):
        response=s.http.post(f'/api/v1/artifacts/{identifier}/reviews',headers=s.headers("admin",key=str(uuid4()),revision=revision),json={**body,"decision":decision})
        assert response.status_code==201,response.text
        view=s.http.get(f'/api/v1/artifacts/evidence/{identifier}',headers=s.headers()).json()
        assert view["review_state"]==expected and view["data_nature"]=="imported_unverified"


def test_registered_upload_reuses_epoch_time_and_records_and_caller_controls_commit(evidence_case):
    import io
    from lzo.artifacts.service import ArtifactService
    from lzo.ingestion.files import FileParser
    from lzo.ingestion.schemas import FileOptions
    from lzo.ingestion.models import source_uploads,source_records
    from lzo.semantic.models import objects
    s=evidence_case
    source=UUID(s.source().json()["id"])
    blob=s.store.put(io.BytesIO(b'id,name\nPOND-001,synthetic\n'),"text/csv")
    with s.transaction() as uow:
        ArtifactService(uow,s.store,s.limits).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="pond.csv",metadata={})
        uow.commit()
    options=FileOptions(format="csv",encoding="utf-8",delimiter=",",sheet=None,header_row=1,records_path="",key_paths=("id",),source_crs=None,source_timezone=None,data_nature="synthetic")
    with s.transaction() as uow:
        binding=UploadService(uow,s.store,s.limits).register(s.actor("alice"),source,blob,options,"upload-first")
        uow.commit()
    with s.transaction() as uow:
        replay=UploadService(uow,s.store,s.limits).register(s.actor("alice"),source,blob,options,"different-command")
        assert replay==binding
        uow.commit()
    duplicate=s.store.put(io.BytesIO(b'id,name\nPOND-001,synthetic\n'),"text/csv")
    assert duplicate.version_id!=blob.version_id
    with s.transaction() as uow:
        ArtifactService(uow,s.store,s.limits).register_upload(s.actor("alice"),s.ids_by_name["object_a"],duplicate,filename="same.csv",metadata={})
        replay=UploadService(uow,s.store,s.limits).register(s.actor("alice"),source,duplicate,options,"new-physical-registration")
        assert replay==binding and replay.blob.version_id==blob.version_id
        new_config=options.model_copy(update={"encoding":"utf-8-sig"})
        distinct=UploadService(uow,s.store,s.limits).register(s.actor("alice"),source,duplicate,new_config,"different-parse-configuration")
        assert distinct.id!=binding.id and distinct.epoch!=binding.epoch
        uow.commit()
    parsed=FileParser(s.store,binding).parse(binding.blob,binding.source_key,options)
    assert parsed.complete and parsed.changes[0].key.canonical_key=='["POND-001"]'
    with s.transaction() as uow:
        UploadService(uow,s.store,s.limits).persist(s.actor("alice"),binding,parsed)
        # Normal UoW exit is rollback, not implicit acceptance.
    with s.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(source_records)).scalar_one()==0
        service=UploadService(uow,s.store,s.limits)
        service.persist(s.actor("alice"),binding,parsed)
        service.persist(s.actor("alice"),binding,parsed)
        assert uow.session.execute(select(func.count()).select_from(source_records)).scalar_one()==1
        assert uow.session.execute(select(func.count()).select_from(objects)).scalar_one()==0
        uow.commit()
    changed=s.store.put(io.BytesIO(b'id,name\nPOND-002,changed\n'),"text/csv")
    with s.transaction() as uow:
        ArtifactService(uow,s.store,s.limits).register_upload(s.actor("alice"),s.ids_by_name["object_a"],changed,filename="pond.csv",metadata={})
        uow.commit()
    from lzo.errors import ApiError
    with s.transaction() as uow, pytest.raises(ApiError) as failure:
        UploadService(uow,s.store,s.limits).register(s.actor("alice"),source,changed,options,"upload-first")
    assert failure.value.status==409


def test_concurrent_different_commands_share_database_upload_identity(evidence_case):
    import io
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from lzo.artifacts.service import ArtifactService
    from lzo.ingestion.schemas import FileOptions
    from lzo.ingestion.models import source_uploads
    s=evidence_case
    source=UUID(s.source().json()["id"])
    blob=s.store.put(io.BytesIO(b'[{"id":"one"}]'),"application/json")
    with s.transaction() as uow:
        ArtifactService(uow,s.store,s.limits).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="one.json",metadata={})
        uow.commit()
    options=FileOptions(format="json",encoding="utf-8",delimiter=",",sheet=None,header_row=1,records_path="",key_paths=("id",),source_crs=None,source_timezone=None,data_nature="synthetic")
    barrier=Barrier(2)
    def register(number):
        actor=s.actor("alice")
        barrier.wait(timeout=10)
        with s.transaction() as uow:
            result=UploadService(uow,s.store,s.limits).register(actor,source,blob,options,f"concurrent-{number}")
            uow.commit()
            return result
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(register,range(2)))
    assert results[0]==results[1]
    with s.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(source_uploads)).scalar_one()==1


def test_multipart_rejects_unknown_duplicate_truncated_and_actual_oversize(evidence_case):
    s=evidence_case
    response=s.http.post('/api/v1/artifacts/evidence',headers=s.headers(key="invalid"),
        files={"file":("a.pdf",b"ok","application/pdf")},data={"metadata":"{}","extra":"secret"})
    assert response.status_code==422 and "secret" not in response.text
    response=s.http.post('/api/v1/artifacts/evidence',headers={**s.headers(key="truncated"),"Content-Type":"multipart/form-data; boundary=abc"},
        content=b'--abc\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\nsecret')
    assert response.status_code==422 and "secret" not in response.text
    s.http.app.state.settings=s.http_settings.model_copy(update={"artifact_max_upload_bytes":8})
    assert s.evidence(payload=b"a"*32777).status_code==413


def test_public_attachment_primitive_cannot_forge_evidence_metadata(evidence_case):
    from lzo.artifacts.service import attach_evidence
    from lzo.contracts import BlobRef
    from lzo.errors import ApiError
    s=evidence_case
    e=s.evidence().json()
    with s.transaction() as uow, pytest.raises(ApiError):
        attach_evidence(uow,s.actor("alice"),UUID(e["scope"]["resource_id"]),BlobRef(**{**e["blob"],"version_id":UUID(e["blob"]["version_id"])}),{"forged":"secret"})


def test_empty_classified_evidence_cannot_leak_history(evidence_case):
    from sqlalchemy import insert
    from lzo.auth.models import resource_scopes
    from lzo.governance.revisions import append_revision
    s=evidence_case
    identifier=uuid4()
    with s.transaction() as uow:
        uow.session.execute(insert(resource_scopes).values(resource_id=identifier,scope_id=s.scopes["farm_a"],security_class="data",resource_kind="evidence"))
        append_revision(uow,s.actor("alice"),identifier,0,{"sensitive":"missing binding"},"evidence.created")
        uow.commit()
    response=s.http.get(f'/api/v1/history/{identifier}/revisions/1',headers=s.headers())
    assert response.status_code==404


def test_evidence_stream_rechecks_each_chunk_and_releases_lock(evidence_case,monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from starlette.requests import Request
    from lzo.artifacts.router import content as download
    from lzo.artifacts.store import ArtifactStore
    from lzo.errors import ApiError
    s=evidence_case
    created=s.evidence(payload=b"original evidence"*10)
    assert created.status_code==201
    identifier=UUID(created.json()["id"])
    request=Request({"type":"http","app":s.http.app,"headers":[(b"cookie",s.headers()["Cookie"].encode())],"state":{"request_id":"evidence-stream"}})
    original=ArtifactStore.open
    opened=[]
    def short_open(store,blob):
        stream=original(store,blob); opened.append(stream)
        class ShortRead:
            def read(self,size): return stream.read(min(size,16))
            def close(self): stream.close()
        return ShortRead()
    async def consume():
        first=await anext(response.body_iterator)
        assert len(first)==16
        with ThreadPoolExecutor(1) as pool:
            pool.submit(s.revoke,s.evidence_grant).result(timeout=5)
        with pytest.raises(ApiError) as failure:
            await anext(response.body_iterator)
        assert failure.value.status==404
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore,"open",short_open)
        response=download(identifier,request)
        asyncio.run(consume())
    assert opened and all(stream.closed for stream in opened)


def test_incomplete_normalization_cannot_persist_or_mutate_semantics(evidence_case):
    import io
    from lzo.artifacts.service import ArtifactService
    from lzo.ingestion.files import FileParser
    from lzo.ingestion.schemas import FileOptions
    from lzo.ingestion.models import source_records
    from lzo.semantic.models import objects
    from lzo.errors import ApiError
    s=evidence_case
    source=UUID(s.source().json()["id"])
    blob=s.store.put(io.BytesIO(b'[{"id":"duplicate"},{"id":"duplicate"}]'),"application/json")
    with s.transaction() as uow:
        ArtifactService(uow,s.store,s.limits).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="duplicate.json",metadata={})
        uow.commit()
    options=FileOptions(format="json",encoding="utf-8",delimiter=",",sheet=None,header_row=1,records_path="",key_paths=("id",),source_crs=None,source_timezone=None,data_nature="synthetic")
    with s.transaction() as uow:
        binding=UploadService(uow,s.store,s.limits).register(s.actor("alice"),source,blob,options,"duplicate-file")
        uow.commit()
    parsed=FileParser(s.store,binding).parse(blob,binding.source_key,options)
    assert not parsed.complete
    with s.transaction() as uow, pytest.raises(ApiError):
        UploadService(uow,s.store,s.limits).persist(s.actor("alice"),binding,parsed)
    with s.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(source_records)).scalar_one()==0
        assert uow.session.execute(select(func.count()).select_from(objects)).scalar_one()==0


def test_four_new_events_advance_actual_prefix_but_unknown_remains_barrier(evidence_case,rdf_repo):
    from lzo.semantic.projection import ProjectionService,register_nonsemantic
    from lzo.governance.revisions import append_revision
    from lzo.governance.models import outbox_events
    s=evidence_case
    for name in ("role.fixture_installed","grant.fixture_installed","object.fixture_created"):
        register_nonsemantic(name,"test-fixture-v1")
    with s.transaction() as uow:
        service=ProjectionService(uow,rdf_repo)
        baseline=service.advance_contiguous()
        for kind in ("source.created","source.upload.registered","source.record.registered","evidence.created"):
            append_revision(uow,s.actor("alice"),uuid4(),0,{"fixture":"synthetic classification"},kind)
        assert service.advance_contiguous()==baseline+4
        append_revision(uow,s.actor("alice"),uuid4(),0,{},"unknown.ingestion.test")
        assert service.advance_contiguous()==baseline+4
        uow.commit()


def test_ingestion_openapi_contract_and_real_migration_head(evidence_case):
    from sqlalchemy import text, create_engine
    s=evidence_case
    document=s.http.get('/openapi.json').json()
    assert document['paths']['/api/v1/artifacts/evidence']['post']['operationId']=='createEvidence'
    assert document['paths']['/api/v1/artifacts/evidence/{id}']['get']['operationId']=='getEvidence'
    assert document['paths']['/api/v1/sources']['get']['operationId']=='listSources'
    assert document['components']['schemas']['EvidenceCreate']['additionalProperties'] is False
    source_properties=document['components']['schemas']['SourceView']['properties']
    for name in ('mapping_version_id','connection_revision','schedule_revision','last_success_at'):
        assert source_properties[name].get('readOnly') is True
    with s.transaction() as uow:
        schema=uow.session.execute(text('SELECT current_schema()')).scalar_one()
    owner=create_engine(s.limits.migration_database_url)
    try:
        with owner.connect() as connection:
            assert connection.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar_one()=='0202_ingestion_mapping_rulings'
    finally:
        owner.dispose()


def test_evidence_rechecks_rights_after_bounded_multipart_transfer(evidence_case):
    import json
    from lzo.artifacts.models import artifact_versions
    s=evidence_case
    metadata={"source_id":None,"scope":{"resource_id":None,"owner_scope_id":str(s.scopes["farm_a"]),"related_scope_ids":[],"security_class":"data"},
        "target_refs":[],"title":"Original","locator":None,"data_nature":"imported_unverified","replaces_id":None}
    body=(b'--controlled\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'+json.dumps(metadata).encode()+
        b'\r\n--controlled\r\nContent-Disposition: form-data; name="file"; filename="evidence.pdf"\r\nContent-Type: application/pdf\r\n\r\noriginal\r\n--controlled--\r\n')
    def transfer():
        yield body[:20]
        s.revoke(s.evidence_grant)
        yield body[20:]
    response=s.http.post('/api/v1/artifacts/evidence',headers={**s.headers(key="revoke-transfer"),"Content-Type":"multipart/form-data; boundary=controlled"},content=transfer())
    assert response.status_code in (403,404)
    with s.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(artifact_versions)).scalar_one()==0


def test_selected_export_cannot_bypass_evidence_read_revocation(evidence_case):
    import io
    from types import SimpleNamespace
    from sqlalchemy import insert
    from lzo.auth.models import roles
    from lzo.auth.permissions import permission_context
    from lzo.semantic.exports import create_binding, ExportRequest
    from lzo.artifacts.service import ArtifactService
    from lzo.errors import ApiError
    s=evidence_case
    created=s.evidence()
    assert created.status_code==201
    evidence_id=UUID(created.json()['id'])
    s.roles['exporter']=uuid4()
    with s.transaction() as uow:
        uow.session.execute(insert(roles).values(id=s.roles['exporter'],name='exporter',actions=['export'],revision=1))
        uow.commit()
    s.grant('alice','exporter',scope='farm_a')
    owner=uuid4(); root=s.ids_by_name['object_a']
    from lzo.governance.revisions import append_revision
    from lzo.ingestion.sources import bind
    from lzo.worker.service import enqueue,JobService
    with s.transaction() as uow,permission_context(uow):
        old=append_revision(uow,s.actor('alice'),root,1,{'private_history':'not selected'},'test.private_history')
        bind(uow,old,[s.ids_by_name['object_b']])
        create_binding(uow,s.actor('alice'),owner,ExportRequest(resource_ids=(root,),format='turtle',layout='dataset'),
            SimpleNamespace(bundle_id=root,input_watermark=0),{root},{root,evidence_id},set(),'0'*64)
        job=enqueue(uow,s.actor('alice'),'semantic.export',{'export_id':str(owner),
            'reference_ids':list(map(str,[root,owner,evidence_id]))},None)
        blob=s.store.put(io.BytesIO(b'<urn:one> <urn:p> "synthetic" .'),'text/turtle')
        exported=ArtifactService(uow,s.store,s.limits).register_semantic_export(s.actor('alice'),owner,blob,
            filename='selected.ttl',metadata={'scope':'authorized_subset'})
        uow.commit()
    with s.transaction() as uow:
        assert ArtifactService(uow,s.store,s.limits).get(s.actor('alice'),exported.version_id)
        assert JobService(uow,s.limits).get(s.actor('alice'),job.id)
    s.revoke(s.evidence_grant)
    with s.transaction() as uow,pytest.raises(ApiError) as failure:
        ArtifactService(uow,s.store,s.limits).get(s.actor('alice'),exported.version_id)
    assert failure.value.status==404
    from lzo.auth.permissions import authorized_resource_ids
    with s.transaction() as uow:
        visible=set(authorized_resource_ids(s.actor('alice'),'view',uow=uow))
        assert owner not in visible and exported.version_id not in visible and job.id not in visible
    with s.transaction() as uow,pytest.raises(ApiError):
        JobService(uow,s.limits).get(s.actor('alice'),job.id)


def test_read_permission_does_not_replace_fixed_evidence_manage(evidence_case):
    s=evidence_case
    created=s.evidence()
    assert created.status_code==201
    identifier=created.json()['id']
    s.grant('alice','evidence_reader',scope='farm_a')
    s.revoke(s.evidence_grant)
    response=s.http.get(f'/api/v1/artifacts/evidence/{identifier}',headers=s.headers())
    assert response.status_code==200
    response=s.http.post(f'/api/v1/artifacts/{identifier}/reviews',headers=s.headers(key='manage-revoked'),json={'decision':'accepted','comment':'no authority'})
    assert response.status_code==403
    response=s.http.post(f'/api/v1/artifacts/{identifier}/links',headers=s.headers(key='link-revoked'),json={'target_resource_id':str(s.ids_by_name['object_a']),'relation':'supports'})
    assert response.status_code==403
