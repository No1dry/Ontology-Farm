from uuid import UUID
from sqlalchemy import select
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle


def test_large_preview_is_persisted_f05_job_with_paged_result(configuration_case):
    from lzo.ingestion.models import source_uploads
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    import io,json
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ingestion.schemas import FileOptions
    from lzo.ingestion.models import preview_work_rows,import_previews,import_rows,import_authorizations,source_evaluations
    from sqlalchemy import func
    s=configuration_case;bundle=ordinary_bundle(s)
    s.config=s.config.model_copy(update={"ingestion_preview_sync_bytes":1,"ingestion_preview_rows_per_batch":1,"list_page_size":1})
    s.client.app.state.settings=s.config
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(json.dumps([{'code':'A','name':'First','area':'1'},{'code':'B','name':'Second','area':'2'}]).encode()),'application/json')
        ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,filename='two.json',metadata={})
        uow.commit()
    options=FileOptions(format='json',encoding='utf-8',delimiter=',',sheet=None,header_row=1,records_path='',key_paths=('code',),source_crs=None,source_timezone=None,data_nature='synthetic')
    from lzo.semantic.facts import json_value
    command={"selection":{"source_id":str(s.i02_source.id),"upload":json_value(blob),"file_options":options.model_dump(mode='json'),"mode":"full"},"bundle_id":str(bundle.id)}
    s.login();headers=s.headers()
    response=s.client.post("/api/v1/imports/preview",headers=headers,json=command)
    assert response.status_code==202,response.text
    repeated=s.client.post("/api/v1/imports/preview",headers=headers,json=command)
    assert repeated.status_code==202 and repeated.json()["id"]==response.json()["id"]
    identifier=UUID(response.json()["id"])
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None and getattr(error,"code",None)!="JOB_YIELD":raise error
    runner=DiagnosticRunner(s.app,s.config.model_copy(update={"job_max_batches_per_run":1}))
    observed=[]
    for _ in range(200):
        with s.transaction() as uow:
            status=uow.session.execute(select(jobs.c.status).where(jobs.c.id==identifier)).scalar_one()
            planned=uow.session.execute(select(func.count()).select_from(preview_work_rows).where(preview_work_rows.c.job_id==identifier,preview_work_rows.c.payload.is_not(None))).scalar_one()
            observed.append(planned)
            assert uow.session.execute(select(func.count()).select_from(source_evaluations)).scalar_one()==0
        if status in ("succeeded","failed","paused"):break
        assert runner.run_once()
    assert status=="succeeded"
    job=s.client.get("/api/v1/jobs/"+str(identifier)).json()
    result=s.client.get(job["result_ref"]["api_path"])
    assert result.status_code==200 and result.json()["ready_count"]==2 and result.json()["complete"]
    rows=s.client.get(job["result_ref"]["api_path"]+"/rows")
    assert len(result.json()['rows'])==1
    assert rows.status_code==200 and len(rows.json()["items"])==1
    cursor=rows.json()['next_cursor'];assert cursor is not None
    second=s.client.get(job['result_ref']['api_path']+'/rows',params={'cursor':cursor})
    assert second.status_code==200 and len(second.json()['items'])==1 and second.json()['next_cursor'] is None
    assert rows.json()['items'][0]['source_key']!=second.json()['items'][0]['source_key']
    assert 0 in observed and 1 in observed and 2 in observed
    with s.transaction() as uow:
        for table,count in ((import_previews,1),(import_authorizations,1),(import_rows,2),(preview_work_rows,2)):
            assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==count
