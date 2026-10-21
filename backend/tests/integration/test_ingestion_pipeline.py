"""Governed imports use registered originals, real configuration and F05/S03."""
from importlib.util import find_spec
from uuid import UUID
import pytest
from sqlalchemy import select, func
from test_ingestion_rule_versions import configuration_case, activate, raw_change


def test_preview_then_apply_is_idempotent_and_version_bound(configuration_case):
    # The missing orchestrator is the feature under test, after the real
    # database/schema/configuration fixture has successfully initialized.
    assert find_spec("lzo.ingestion.pipeline") is not None, "Governed import orchestrator is missing"
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.schemas import ImportSelection, ImportPreviewCommand, ApplyImportCommand
    from lzo.ingestion.models import source_uploads
    from lzo.semantic.models import objects, supports
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s = configuration_case
    bundle = activate(s)
    change = raw_change(s)
    with s.transaction() as uow:
        binding = uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id == s.i02_source.id)).scalar_one()
        from lzo.ingestion.schemas import UploadBinding
        binding = UploadBinding.model_validate(binding)
        uow.ontology_settings = s.config
        service = IngestionService(uow)
        before = uow.session.execute(select(func.count()).select_from(objects)).scalar_one()
        report = service.preview(s.actor("alice"), ImportPreviewCommand(selection=ImportSelection(
            source_id=s.i02_source.id, upload=binding.blob, file_options=binding.options,
            mode="full"), bundle_id=bundle.id))
        assert report.ready_count == 1 and report.invalid_count == report.review_count == 0
        assert report.approval_id is not None, "Executable instance preview requires real F06 authorization"
        from lzo.governance.approvals import ApprovalService
        from lzo.errors import ApiError
        approval=ApprovalService(uow).get(s.actor("alice"),report.approval_id)
        assert approval.risk=="high"
        assert uow.session.execute(select(func.count()).select_from(objects)).scalar_one() == before
        command = ApplyImportCommand(preview_id=report.id, expected_revision=report.revision,
            input_digest=report.input_digest, approval_id=report.approval_id)
        with pytest.raises(ApiError) as pending:
            service.apply(uow,s.actor("alice"),command)
        assert pending.value.code=="APPROVAL_REQUIRED"
        ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Approve exact staged source effects")
        first = service.apply(uow, s.actor("alice"), command)
        again = service.apply(uow, s.actor("alice"), command)
        assert first.id == again.id
        uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self, ctx, error=None):
            super().finish(ctx,error)
            if error is not None:
                raise error
    runner = DiagnosticRunner(s.app, s.config)
    for _ in range(200):
        with s.transaction() as uow:
            state = uow.session.execute(select(jobs.c.status).where(jobs.c.id == first.id)).scalar_one()
        if state in ("succeeded", "failed", "paused"):
            break
        assert runner.run_once()
    assert state == "succeeded"
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        run = IngestionService(uow).get(s.actor("alice"), first.id)
        assert run.applied_count == 1 and run.failed_count == run.review_count == 0
        rows = IngestionService(uow).rows(s.actor("alice"), first.id)
        assert rows.items[0].state == "applied"
        assert rows.items[0].revision.resource_id == rows.items[0].entity_id
        assert uow.session.execute(select(func.count()).select_from(objects)).scalar_one() == before + 1
        assert uow.session.execute(select(func.count()).select_from(supports).where(
            supports.c.payload["source_id"].astext == str(s.i02_source.id))).scalar_one() == 1
        assert run.projection_state in ("pending", "synced")
        assert run.inference_watermark is not None
        assert IngestionService(uow).apply(uow,s.actor("alice"),command).id==first.id


def ordinary_bundle(s,important=False):
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import ImportAdmissionPolicy
    from test_ingestion_rule_versions import version
    from test_ingestion_review_boundaries import publish
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        authority=RuleService(uow).create_version(uow,s.actor("alice"),version("authority",authority_rules=tuple(
            r.model_copy(update={"important":important}) for r in s.i02_authority.authority_rules)))
        ruling=RuleService(uow).create_version(uow,s.actor("alice"),version("ruling",import_admission=ImportAdmissionPolicy(
            effect_classes=("create","type","facts"),predicate_iris=("urn:area",),class_iris=(s.i02_mapping.class_iri,),
            owner_scope_ids=(s.scopes["farm_a"],),max_resources=2,max_withdrawals=0,max_rows=2)))
        bundle=RuleService(uow).stage_configuration(s.actor("alice"),(s.i02_mapping.id,),authority.id,ruling.id)
        uow.commit()
    publish(s,bundle,authority)
    return bundle


@pytest.mark.parametrize("interruption",["resume","external_head","reviewer_revocation","actor_revocation"])
def test_ordinary_two_rows_resume_exact_own_consumption(configuration_case,interruption):
    import io,json
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ingestion.schemas import FileOptions,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.governance.approvals import ApprovalService,decisions
    from lzo.ingestion.models import import_run_rows
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case;bundle=ordinary_bundle(s,important=interruption=="reviewer_revocation")
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(json.dumps([{"code":"A","name":"One","area":"1"},{"code":"B","name":"Two","area":"2"}]).encode()),"application/json")
        ArtifactService(uow,store,s.config).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="two.json",metadata={})
        options=FileOptions(format="json",encoding="utf-8",delimiter=",",sheet=None,header_row=1,records_path="",
            key_paths=("code",),source_crs=None,source_timezone=None,data_nature="synthetic")
        service=IngestionService(uow)
        report=service.preview(s.actor("alice"),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=blob,file_options=options,mode="full"),bundle_id=bundle.id))
        assert report.ready_count==2
        approval=ApprovalService(uow).get(s.actor("alice"),report.approval_id)
        assert approval.risk==("high" if interruption=="reviewer_revocation" else "low") and approval.system_policy_version=="ordinary-import-v1"
        assert uow.session.execute(select(decisions).where(decisions.c.approval_id==approval.id)).first() is None
        if interruption=="reviewer_revocation":ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Independent high batch review")
        command=ApplyImportCommand(preview_id=report.id,expected_revision=1,input_digest=report.input_digest,approval_id=approval.id)
        job=service.apply(uow,s.actor("alice"),command);uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None and getattr(error,"code",None)!="JOB_YIELD":raise error
    runner=DiagnosticRunner(s.app,s.config.model_copy(update={"job_max_batches_per_run":1}))
    observed=[]
    for _ in range(300):
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            outcomes=uow.session.execute(select(import_run_rows).where(import_run_rows.c.run_id==job.id)).mappings().all()
            if len(outcomes) not in observed:
                observed.append(len(outcomes))
                if outcomes:
                    assert IngestionService(uow).apply(uow,s.actor("alice"),command).id==job.id
                    uow.commit()
            status=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if len(outcomes)==1 and interruption!="resume":
            from lzo.errors import ApiError
            if interruption=="external_head":
                from lzo.semantic.facts import apply_fact_patch
                from lzo.semantic.schemas import FactPatch
                first=outcomes[0]["payload"]["result"]
                with s.transaction() as uow:
                    uow.ontology_settings=s.config
                    apply_fact_patch(uow,s.actor("alice"),FactPatch(resource_id=UUID(first["entity_id"]),expected_revision=1),bundle)
                    uow.commit()
            elif interruption=="reviewer_revocation":s.revoke(s.review_source_grant)
            else:
                from lzo.auth.models import grants
                from lzo.auth.schemas import GrantDetail
                with s.transaction() as uow:
                    grant=GrantDetail.model_validate(dict(uow.session.execute(select(grants).where(grants.c.user_id==s.actor("alice").subject_id,
                        grants.c.role_id==s.roles["source_manager"])).mappings().one()))
                s.revoke(grant)
            with s.transaction() as uow:
                uow.ontology_settings=s.config
                with pytest.raises(ApiError) as rejected:IngestionService(uow).apply(uow,s.actor("alice"),command)
                assert rejected.value.code in ("IMPORT_PREVIEW_STALE","FORBIDDEN","NOT_FOUND")
                assert len(uow.session.execute(select(import_run_rows).where(import_run_rows.c.run_id==job.id)).all())==1
            # Advance the registered worker too: revocation/staleness must fence
            # the next row even without another client apply request.
            for _ in range(100):
                with s.transaction() as uow:
                    blocked=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
                if blocked in ('failed','paused','cancelled'):break
                try:
                    if not runner.run_once():break
                except ApiError as error:
                    assert error.code in ('IMPORT_PREVIEW_STALE','FORBIDDEN','NOT_FOUND','APPROVAL_REQUIRED','APPROVAL_STALE')
            with s.transaction() as uow:
                assert len(uow.session.execute(select(import_run_rows).where(import_run_rows.c.run_id==job.id)).all())==1
                stopped=uow.session.execute(select(jobs).where(jobs.c.id==job.id)).mappings().one()
                assert stopped['status']!='succeeded'
                assert stopped['error_code'] in ('HANDLER_FAILED','AUTHORIZATION_REVOKED'), 'Actual worker execution must observe the fence'
            return
        if status=="succeeded":break
        assert runner.run_once()
    assert status=="succeeded" and 1 in observed and 2 in observed
    with s.transaction() as uow:
        outcomes=uow.session.execute(select(import_run_rows.c.payload).where(import_run_rows.c.run_id==job.id)).scalars().all()
        assert len(outcomes)==2 and all(v["result"]["state"]=="applied" for v in outcomes)
        assert all(v["before_ref"] is None and v["decision_ref"] and v["result"]["revision"]["revision"]==1 for v in outcomes)


def test_same_idempotency_key_changed_digest_returns_409(configuration_case):
    from lzo.ingestion.models import source_uploads
    s=configuration_case
    bundle=activate(s);raw_change(s)
    with s.transaction() as uow:
        binding=uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.source_id==s.i02_source.id)).scalar_one()
    command={"selection":{"source_id":str(s.i02_source.id),"upload":binding["blob"],"file_options":binding["options"],"mode":"full"},
        "bundle_id":str(bundle.id)}
    s.login();headers=s.headers()
    response=s.client.post("/api/v1/imports/preview",headers=headers,json=command)
    assert response.status_code==201,response.text
    repeated=s.client.post("/api/v1/imports/preview",headers=headers,json=command)
    assert repeated.status_code==201 and repeated.json()["id"]==response.json()["id"]
    changed={**command,"selection":{**command["selection"],"mode":"incremental"}}
    assert s.client.post("/api/v1/imports/preview",headers=headers,json=changed).status_code==409
    identifier=response.json()["id"]
    assert s.client.get(f"/api/v1/imports/previews/{identifier}").status_code==200
    assert len(s.client.get(f"/api/v1/imports/previews/{identifier}/rows").json()["items"])==1
    apply={"preview_id":identifier,"expected_revision":1,"input_digest":response.json()["input_digest"],"approval_id":None}
    assert s.client.post("/api/v1/imports/apply",headers=s.headers(),json=apply).status_code==428
    s.login("nobody")
    assert s.client.get(f"/api/v1/imports/previews/{identifier}").status_code==404
