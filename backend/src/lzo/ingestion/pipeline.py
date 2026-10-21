"""Version-bound staging and row-atomic adoption through the existing producers."""
from uuid import UUID, uuid4
from sqlalchemy import insert, select, update
from lzo.auth.permissions import permission_context
from lzo.contracts import JobRef, Page, ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.models import platform_watermarks
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.ingestion.models import (import_previews, import_rows, import_runs, import_run_rows,
    source_records, source_scans, source_uploads)
from lzo.ingestion.schemas import (ImportSelection, PreviewReport, RowResult, ImportRunView,
    MappedRecord, ParseIssue, SourceChange, UploadBinding)
from lzo.ingestion.sources import SourceService, UploadService, add_scoped, digest, current_source_change
from lzo.ingestion.identity import IdentityService
from lzo.ingestion.mapping import MappingService
from lzo.ingestion.rulings import source_evaluation_plan, persist_source_evaluation, RulingService
from lzo.ontology.drafts import authorize, bind_dependencies, head, settings_for, store_for
from lzo.ontology.releases import current_execution_bundle
from lzo.semantic import facts
from lzo.semantic.schemas import CreateObjectInput, FactPatch
from lzo.semantic.projection import register_nonsemantic
from lzo.worker.models import jobs
from lzo.worker.service import enqueue


def issue(change, code, message):
    return ParseIssue(locator=change.locator, field_path=None, code=code, message=message)


class IngestionService:
    def __init__(self, uow=None, context=None):
        self.uow, self.context = uow, context

    def _profile(self, actor, source_id, bundle):
        from lzo.ingestion.rules import aggregate
        member=next((m for m in aggregate(self.uow,bundle)["members"] if m["source_id"]==str(source_id)),None)
        if member is None:raise Conflict("MAPPING_NOT_PUBLISHED","Source requires a published profile")
        return SourceService(self.uow).get_mapping(actor,source_id,UUID(member["profile_id"]))

    def _changes(self, actor, selection):
        uow=self.uow
        if selection.scan_id:
            if selection.upload or selection.file_options or selection.semantic_format:
                raise ApiError("IMPORT_SELECTION_INVALID",422,"Scan and file selection are exclusive")
            authorize(uow,actor,selection.scan_id)
            scan=uow.session.execute(select(source_scans).where(source_scans.c.id==selection.scan_id,
                source_scans.c.source_id==selection.source_id)).mappings().one_or_none()
            if not scan or scan["payload"]["result"]["state"]!="complete":
                raise Conflict("SCAN_INCOMPLETE","Only a completed authenticated scan can be previewed")
            meta=scan['payload']
            if selection.mode!=meta['mode']:raise Conflict('SCAN_MODE_MISMATCH','Selection differs from authenticated scan mode')
            if meta['mode']=='full':
                # Only the immediately preceding comparable complete full scan
                # can prove absence. The producer rechecks the current full
                # snapshot fence and persists deterministic authenticated deletes.
                prior=[]
                for candidate in uow.session.execute(select(source_scans).where(
                        source_scans.c.source_id==selection.source_id)).mappings():
                    value=candidate['payload']
                    if (value['generation']<meta['generation'] and value['mode']=='full'
                            and value['result']['state']=='complete'
                            and value['result']['scope_digest']==meta['result']['scope_digest']
                            and value['connection_revision']==meta['connection_revision']
                            and value['mapping_version_id']==meta['mapping_version_id']):prior.append(candidate)
                if prior:
                    from lzo.ingestion.sync import SyncService
                    previous=max(prior,key=lambda item:item['payload']['generation'])
                    SyncService(uow=uow).deletions(actor,selection.scan_id,previous['id'])
            values=uow.session.execute(select(source_records.c.payload).where(source_records.c.scan_id==selection.scan_id)
                .order_by(source_records.c.ordinal)).scalars()
            return tuple(SourceChange.model_validate(v) for v in values)
        if selection.semantic_format:
            if not selection.upload or selection.semantic_kind!="instances":
                raise ApiError("IMPORT_SELECTION_INVALID",422,"Semantic instances require an original and a format")
            from lzo.ingestion.schemas import SemanticFileOptions
            from lzo.ingestion.semantic_files import SemanticFileParser
            source=SourceService(uow).read(actor,selection.source_id,"source.manage")
            service=UploadService(uow,store_for(uow),settings_for(uow))
            options=selection.file_options
            if options is not None and (not isinstance(options,SemanticFileOptions) or options.format!=selection.semantic_format):
                raise ApiError("IMPORT_SELECTION_INVALID",422,"Semantic options must match the selected format")
            options=(options or SemanticFileOptions(format=selection.semantic_format,data_nature=source.configuration.data_nature)).model_copy(
                update={"data_nature":source.configuration.data_nature})
            binding=service.register(actor,selection.source_id,selection.upload,options,
                "import-input:"+digest(selection.model_dump(mode="json")))
            parsed=SemanticFileParser(store_for(uow),binding).parse()
            service.persist(actor,binding,parsed)
            return parsed.changes
        from lzo.ingestion.schemas import FileOptions
        if not selection.upload or not isinstance(selection.file_options,FileOptions):
            raise ApiError("IMPORT_SELECTION_INVALID",422,"A registered file and exact parser options are required")
        service=UploadService(uow,store_for(uow),settings_for(uow))
        binding=service.register(actor,selection.source_id,selection.upload,selection.file_options,
            "import-input:"+digest(selection.model_dump(mode="json")))
        from lzo.ingestion.files import FileParser,ParserLimits
        parsed=FileParser(store_for(uow),binding,limits=ParserLimits.from_settings(settings_for(uow))).parse(
            binding.blob,binding.source_key,binding.options)
        service.persist(actor,binding,parsed)
        return parsed.changes

    def preview(self, actor, command):
        uow=self.uow
        with permission_context(uow):
            source=SourceService(uow).read(actor,command.selection.source_id,"source.manage")
            bundle=current_execution_bundle(uow,actor)
            if bundle.id!=command.bundle_id:raise Conflict("BUNDLE_NOT_ACTIVE","Preview requires current configuration")
            if command.selection.semantic_kind in ("schema","shapes"):
                from lzo.ingestion.draft_imports import preview
                return preview(uow,actor,command,source,bundle)
            profile=self._profile(actor,source.id,bundle)
            changes=self._changes(actor,command.selection)
            from lzo.ingestion.planning import arrange
            changes=arrange(uow,actor,profile,changes)
            rows,plans,refs,baseline=self.plan_changes(actor,source,profile,bundle,changes)
            return self.finish_preview(actor,command,source,bundle,changes,rows,plans,refs,baseline)

    def plan_changes(self,actor,source,profile,bundle,changes,*,prefix_plans=(),all_changes=None):
        from lzo.ingestion.planning import enabled,plan
        if enabled(profile):return plan(self.uow,actor,source,profile,bundle,changes,prefix_plans=prefix_plans,all_changes=all_changes)
        return self._plan_changes(actor,source,profile,bundle,changes)

    def _plan_changes(self,actor,source,profile,bundle,changes,*,persist_conflicts=True):
        uow=self.uow
        with permission_context(uow):
            rows=[];plans=[];refs={source.id,profile.id,bundle.id}
            baseline={str(source.id):source.revision}
            for change in changes:
                facts.fact_validation.begin(uow)
                refs.update((change.change_id,change.snapshot.version_id))
                plan=None;result=RowResult(locator=change.locator,source_key=change.key,state="invalid")
                from lzo.ingestion.mapping import semantic_dispositions
                dispositions=semantic_dispositions(change,profile)
                if dispositions:
                    rows.append(result.model_copy(update={"state":"needs_review","issue_refs":dispositions}));plans.append(None)
                    continue
                try:
                    identity=IdentityService(uow).resolve(uow,actor,change,profile,True)
                    if identity.resource_id is None:
                        result=result.model_copy(update={"state":"needs_review","issue_refs":(issue(change,"IDENTITY_REVIEW_REQUIRED",identity.explanation),)})
                    else:
                        mapped=MappingService(uow).plan(actor,change,profile,bundle,identity)
                        omitted=[]
                        for field in profile.fields:
                            from lzo.ingestion.mapping import path_value
                            if path_value(change.raw_fields,field.source_path) is not None and not any(a.predicate_iri==field.predicate_iri for a in mapped.facts):
                                omitted.append(ParseIssue(locator=change.locator,field_path=field.source_path,
                                    code="FIELD_AUTHORITY_EXCLUDED",message="Published field authority excludes this source value"))
                        result=result.model_copy(update={"issue_refs":tuple(omitted)})
                        target=uow.session.execute(select(facts.objects.c.revision).where(facts.objects.c.id==identity.resource_id)).scalar_one_or_none() or 0
                        baseline[str(identity.resource_id)]=target
                        context,effect,decision_refs=source_evaluation_plan(uow,actor,mapped,profile,bundle,target)
                        refs.update(decision_refs)
                        controlled_withdrawal=False
                        if effect["operation"]=="source.row":
                            from lzo.semantic.source_rows import validate_source_row,withdrawal_only
                            from lzo.semantic.schemas import SourceRowInput
                            source_row=SourceRowInput.model_validate(effect["row"])
                            validation=validate_source_row(uow,actor,source_row,bundle)
                            controlled_withdrawal=withdrawal_only(source_row)
                        elif target==0:
                            validation=facts.validate_object(actor,CreateObjectInput.model_validate(effect["create"]),bundle)
                        else:
                            validation=facts.validate_facts(actor,FactPatch.model_validate(effect["patch"]).additions,bundle) if mapped.facts else None
                        if validation is not None and not validation.conforms:
                            result=result.model_copy(update={"entity_id":identity.resource_id,"issue_refs":tuple(
                                ParseIssue(locator=change.locator,field_path=v.path_iri,code="SHACL_VIOLATION",
                                    message=v.message+"; shape="+str(v.shape_iri)) for v in validation.violations)})
                        elif controlled_withdrawal and validation is None:
                            result=result.model_copy(update={"issue_refs":(*result.issue_refs,
                                issue(change,"VALIDATION_PENDING","Withdrawal quality validation could not complete"))})
                        if validation is None or validation.conforms or controlled_withdrawal:
                            result=result.model_copy(update={"state":"ready","entity_id":identity.resource_id})
                            refs.update(getattr(uow,"facts_validation_business_refs",set()))
                            refs.update(getattr(uow,"facts_validation_extra_refs",set()))
                            plan={"mapped":mapped.model_dump(mode="json"),"expected_revision":target,"effect":effect,
                                "context":context,"before_ref":facts.json_value(facts.revision_ref(uow,identity.resource_id)) if target else None}
                except ApiError as error:
                    from lzo.ingestion.planning import incomplete
                    incomplete(error)
                    if error.status in (401,403,404):raise
                    review=error.code in ("SOURCE_REVIEW_REQUIRED","SOURCE_EVIDENCE_SUPERSEDED")
                    result=result.model_copy(update={"state":"needs_review" if review else "invalid",
                        "issue_refs":(issue(change,error.code,error.message),)})
                    if review and persist_conflicts and 'mapped' in locals() and mapped.change==change:
                        conflict=RulingService(uow).prepare(uow,actor,mapped,bundle)
                        result=result.model_copy(update={"conflict_ids":(conflict.id,),"entity_id":identity.resource_id})
                        refs.add(conflict.id)
                except ValueError:
                    result=result.model_copy(update={"issue_refs":(issue(change,"MAPPING_INVALID","Mapped value is invalid"),)})
                rows.append(result);plans.append(plan)
            return rows,plans,refs,baseline

    def finish_preview(self,actor,command,source,bundle,changes,rows,plans,refs,baseline):
        uow=self.uow;identifier=uuid4()
        with permission_context(uow):
            # No implicit predecessor chain: every overlapping target is an
            # explicit excluded conflict, including a ready/conflicted pair.
            from collections import Counter
            counts=Counter(r.entity_id for r in rows if r.entity_id)
            for index,row in enumerate(rows):
                if row.entity_id and counts[row.entity_id]>1:
                    rows[index]=row.model_copy(update={"state":"needs_review","issue_refs":(*row.issue_refs,
                        issue(changes[index],"IMPORT_TARGET_OVERLAP","Repeated target requires an explicit predecessor plan"))})
                    plans[index]=None
            protected_refs={str(i):facts.json_value(facts.revision_ref(uow,i)) for i in refs}
            baseline.update({i:r["revision"] for i,r in protected_refs.items()})
            input_digest=digest([c.model_dump(mode="json") for c in changes])
            change_digest=digest({"input":input_digest,"bundle":facts.json_value(bundle),"plans":plans,"baseline":baseline})
            report=PreviewReport(id=identifier,revision=1,selection=command.selection,bundle=bundle,input_digest=input_digest,
                baseline=baseline,rows=tuple(rows[:settings_for(uow).list_page_size]),ready_count=sum(r.state=="ready" for r in rows),
                review_count=sum(r.state=="needs_review" for r in rows),invalid_count=sum(r.state=="invalid" for r in rows),
                complete=True,change_digest=change_digest,approval_id=None)
            scope=source.configuration.scope
            add_scoped(uow,actor,identifier,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),"ingestion.preview","source.manage")
            payload={"report":report.model_dump(mode="json"),"actor_id":str(actor.subject_id),"protected_ids":sorted(map(str,refs)),
                "protected_refs":protected_refs}
            ref=append_revision(uow,actor,identifier,0,payload,"ingestion.preview.created")
            bind_dependencies(uow,identifier,refs,ref.event_id)
            uow.session.execute(insert(import_previews).values(id=identifier,payload=payload))
            for ordinal,(row,plan) in enumerate(zip(rows,plans)):
                uow.session.execute(insert(import_rows).values(preview_id=identifier,ordinal=ordinal,
                    payload={"result":row.model_dump(mode="json"),"plan":plan}))
            if report.ready_count:
                from lzo.ingestion.admission import KIND
                from lzo.ingestion.models import import_authorizations
                from lzo.governance.approvals import ApprovalService
                service=ApprovalService(uow)
                descriptor=service.preview(actor,KIND,[identifier],{"preview_id":str(identifier),"manifest_digest":change_digest})
                approval=service.submit(actor,KIND,list(descriptor.target_ids),descriptor.change_digest,descriptor.baseline,{"command":descriptor.command})
                uow.session.execute(insert(import_authorizations).values(preview_id=identifier,approval_id=approval.id))
            return self.get_preview(actor,identifier)

    def get_preview(self, actor, identifier):
        authorize(self.uow,actor,identifier)
        value=self.uow.session.execute(select(import_previews.c.payload).where(import_previews.c.id==identifier)).scalar_one_or_none()
        if value is None:raise ApiError("NOT_FOUND",404,"Preview not found")
        report=PreviewReport.model_validate(value["report"])
        SourceService(self.uow).read(actor,report.selection.source_id)
        from lzo.ingestion.models import import_authorizations
        from lzo.governance.approvals import ApprovalService
        approval_id=self.uow.session.execute(select(import_authorizations.c.approval_id).where(
            import_authorizations.c.preview_id==identifier)).scalar_one_or_none()
        if approval_id:
            approval=ApprovalService(self.uow).get(actor,approval_id)
            report=report.model_copy(update={"approval_id":approval_id,"change_digest":approval.change_digest,"baseline":approval.baseline})
        return report

    def apply(self, uow, actor, command):
        with permission_context(uow):
            report=self.get_preview(actor,command.preview_id)
            SourceService(uow).read(actor,report.selection.source_id,"source.manage")
            draft=report.selection.semantic_kind in ("schema","shapes")
            if draft:
                from lzo.ingestion.draft_imports import authorize as authorize_draft
                authorize_draft(uow,actor,report.selection)
            lock_ledger(uow)
            if command.expected_revision!=report.revision or command.input_digest!=report.input_digest:
                raise Conflict("IMPORT_PREVIEW_CHANGED","Preview revision or input digest differs")
            if command.approval_id!=report.approval_id:raise Conflict("IMPORT_APPROVAL_MISMATCH","Approval differs")
            from lzo.ingestion.admission import require
            require(uow,actor,report)
            existing=uow.session.execute(select(import_runs.c.id).where(import_runs.c.preview_id==report.id)).scalar_one_or_none()
            if existing:
                state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==existing)).scalar_one()
                return JobRef(existing,state)
            if draft or not report.ready_count:self._baseline(actor,report)
            if not report.complete:raise Conflict("IMPORT_PREVIEW_INCOMPLETE","Incomplete input cannot be applied")
            if command.approval_id!=report.approval_id:raise Conflict("IMPORT_APPROVAL_MISMATCH","Approval differs")
            job=enqueue(uow,actor,"ingestion.apply",{"preview_id":str(report.id)},report.bundle.id)
            uow.session.execute(insert(import_runs).values(id=job.id,preview_id=report.id,payload={"bundle_id":str(report.bundle.id),
                "approval_id":str(report.approval_id) if report.approval_id else None}))
            return job

    def _baseline(self, actor, report):
        if current_execution_bundle(self.uow,actor)!=report.bundle:
            raise Conflict("IMPORT_PREVIEW_STALE","Current configuration changed")
        for identifier,revision in report.baseline.items():
            if revision:
                if head(self.uow,UUID(identifier))["revision"]!=revision:
                    raise Conflict("IMPORT_PREVIEW_STALE","Source or target changed")
            elif self.uow.session.execute(select(facts.objects.c.id).where(facts.objects.c.id==UUID(identifier))).first():
                raise Conflict("IMPORT_PREVIEW_STALE","Reserved target was adopted")

    def process_row(self, uow, actor, row, bundle, expected_revision):
        profile=self._profile(actor,row.change.key.source_id,bundle)
        ref,effect=persist_source_evaluation(uow,actor,row,profile,bundle,expected_revision)
        if effect["operation"]=="source.row":
            from lzo.semantic.source_rows import apply_source_row
            from lzo.semantic.schemas import SourceRowInput
            receipt=apply_source_row(uow,actor,SourceRowInput.model_validate(effect["row"]),bundle)
        elif effect["operation"]=="object.create":
            receipt=facts.register_object(uow,actor,CreateObjectInput.model_validate(effect["create"]),bundle)
        else:
            receipt=facts.apply_fact_patch(uow,actor,FactPatch.model_validate(effect["patch"]),bundle)
        return RowResult(locator=row.change.locator,source_key=row.change.key,state="applied",entity_id=row.identity.resource_id,
            revision=receipt.revision,assertion_ids=receipt.assertion_ids)

    def run_batch(self,actor,job_id,lease_token):
        from lzo.worker.service import _execution
        from lzo.worker.runner import JobContext
        context=self.context
        if (not isinstance(context,JobContext) or _execution.get(None) is not context
                or context.job_id!=job_id or context.lease_token!=lease_token
                or actor.subject_id!=context.actor.subject_id):
            raise Conflict("JOB_BATCH_REQUIRED","An actual leased F05 execution context is required")
        from lzo.ingestion.worker import apply_import
        apply_import(context)
        from lzo.db import PgUnitOfWork
        with PgUnitOfWork(context.runner.engine) as uow:
            uow.ontology_settings=context.runner.settings
            return IngestionService(uow).get(context.actor,job_id)

    def rows(self, actor, identifier, cursor=None, *, preview=False):
        if preview:self.get_preview(actor,identifier)
        else:self.get(actor,identifier)
        table=import_rows if preview else import_run_rows
        key=table.c.preview_id if preview else table.c.run_id
        try:offset=int(cursor) if cursor is not None else -1
        except ValueError:raise ApiError("INVALID_CURSOR",422,"Invalid row cursor") from None
        size=settings_for(self.uow).list_page_size
        values=self.uow.session.execute(select(table).where(key==identifier,table.c.ordinal>offset)
            .order_by(table.c.ordinal).limit(size+1)).mappings().all()
        return Page([RowResult.model_validate(v["payload"]["result"]) for v in values[:size]],
            str(values[size-1]["ordinal"]) if len(values)>size else None)

    def get(self, actor, identifier):
        from lzo.worker.service import JobService
        # The job's full protected dependency closure is checked by F05.
        view=JobService(self.uow,settings_for(self.uow)).get(actor,identifier)
        run=self.uow.session.execute(select(import_runs).where(import_runs.c.id==identifier)).mappings().one_or_none()
        if run is None:raise ApiError("NOT_FOUND",404,"Import not found")
        report=self.get_preview(actor,run["preview_id"])
        values=self.uow.session.execute(select(import_run_rows.c.payload).where(import_run_rows.c.run_id==identifier)).scalars().all()
        values=[v["result"] for v in values]
        states=[v["state"] for v in values]
        from lzo.semantic.models import projection_events
        draft=report.selection.semantic_kind in ("schema","shapes")
        event_ids=[] if draft else [UUID(v["revision"]["event_id"]) for v in values if v.get("revision")]
        projection=list(self.uow.session.execute(select(projection_events.c.state).where(projection_events.c.event_id.in_(event_ids))).scalars())
        watermarks=self.uow.session.execute(select(platform_watermarks)).mappings().one()
        state="failed" if "failed" in projection else "synced" if len(projection)==len(event_ids) and all(v=="synced" for v in projection) else "pending"
        from lzo.ingestion.completion import published_inputs
        inferred=draft or published_inputs(self.uow,event_ids)
        return ImportRunView(id=identifier,job=view.job,preview_id=report.id,bundle_id=report.bundle.id,read_count=len(values),
            applied_count=states.count("applied"),review_count=states.count("needs_review"),failed_count=states.count("failed")+states.count("invalid"),
            schema_draft_id=next((UUID(v["entity_id"]) for v in values if v["state"]=="applied"),None) if draft else None,
            projection_watermark=watermarks["projection_watermark"],inference_watermark=watermarks["inference_watermark"],
            projection_state=state,complete=view.job.status=="succeeded" and not any(s in ("needs_review","failed","invalid") for s in states) and state=="synced" and inferred)


register_nonsemantic("ingestion.preview.created","ingestion-v1")
