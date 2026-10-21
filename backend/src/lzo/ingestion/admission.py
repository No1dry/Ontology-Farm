"""Exact F06 import admission and finite, event-proven consumption.

Only this registered kind reconstructs its initial baseline after proving its
own committed row events. No current dependency or permission check is waived.
"""
from uuid import UUID
from sqlalchemy import select
from lzo.auth.permissions import current_version, require_permission
from lzo.contracts import ActorContext
from lzo.errors import ApiError, Conflict
from lzo.governance.approvals import ApprovalService
from lzo.governance.models import revisions
from lzo.governance.risk import ChangeDescriptor, OrdinaryImportEvidence, register_policy, system_policy_version
from lzo.ingestion.models import (import_previews, import_rows, import_runs, import_run_rows,
    import_authorizations, rule_versions, source_evaluations)
from lzo.ingestion.schemas import PreviewReport, MappedRecord, ImportAdmissionPolicy
from lzo.ingestion.sources import digest, SourceService, current_source_change
from lzo.ontology.drafts import authorize
from lzo.ontology.releases import current_execution_bundle
from lzo.semantic import facts
from lzo.worker.models import jobs

KIND="ingestion.import.execute"


def stale():raise Conflict("IMPORT_PREVIEW_STALE","Import evidence or exact consumption changed")


def _committed(uow,preview,report,rows):
    run=uow.session.execute(select(import_runs).where(import_runs.c.preview_id==report.id)).mappings().first()
    if run is None:return {}
    job=uow.session.execute(select(jobs).where(jobs.c.id==run["id"])).mappings().one()
    approval=uow.session.execute(select(import_authorizations.c.approval_id).where(
        import_authorizations.c.preview_id==report.id)).scalar_one()
    if (job["kind"]!="ingestion.apply" or str(job["initiator_id"])!=preview["actor_id"]
            or job["payload"]!={"preview_id":str(report.id)} or job["bundle_id"]!=report.bundle.id
            or run["payload"].get("approval_id")!=str(approval)):stale()
    consumed={}
    for record in uow.session.execute(select(import_run_rows).where(import_run_rows.c.run_id==run["id"])
            .order_by(import_run_rows.c.ordinal)).mappings():
        ordinal=record["ordinal"];value=record["payload"]
        if ordinal not in rows:stale()
        plan=rows[ordinal]["plan"]
        if value["result"]["state"]!="applied":continue
        if not plan or value["before_ref"]!=plan["before_ref"] or value["effect_digest"]!=digest(plan["effect"]):stale()
        after=value["result"]["revision"];identifier=UUID(after["resource_id"])
        if str(identifier)!=plan["mapped"]["identity"]["resource_id"] or identifier in consumed:stale()
        event=uow.session.execute(select(revisions).where(revisions.c.event_id==UUID(after["event_id"]),
            revisions.c.resource_id==identifier,revisions.c.revision==after["revision"])).mappings().first()
        decision=value["decision_ref"]
        evidence=uow.session.execute(select(source_evaluations.c.payload).where(
            source_evaluations.c.id==UUID(decision["resource_id"]))).scalar_one_or_none()
        decision_event=uow.session.execute(select(revisions).where(revisions.c.event_id==UUID(decision["event_id"]),
            revisions.c.resource_id==UUID(decision["resource_id"]),revisions.c.revision==decision["revision"])).mappings().first()
        key="row" if plan["effect"]["operation"]=="source.row" else "create" if plan["effect"]["operation"]=="object.create" else "patch"
        expected={**plan["effect"],key:{**plan["effect"][key],"decision_ref":decision}}
        if (not event or event["previous_revision"]!=plan["expected_revision"]
                or str(event["actor_id"])!=preview["actor_id"] or event["payload"].get("effect")!=expected
                or event["event_type"] not in ("object.created","object.facts_changed")
                or not evidence or evidence["mapped"]!=plan["mapped"] or evidence["effect"]!=plan["effect"]
                or evidence["actor_id"]!=preview["actor_id"] or evidence["bundle_id"]!=str(report.bundle.id)
                or not decision_event or decision_event["payload"]!=evidence
                or decision_event["event_type"]!="ingestion.source.evaluated"
                or str(decision_event["actor_id"])!=preview["actor_id"]
                or facts.json_value(facts.revision_ref(uow,UUID(decision['resource_id'])))!=decision
                or facts.json_value(facts.revision_ref(uow,identifier))!=after):stale()
        consumed[identifier]=value
    return consumed


def descriptor(uow,targets,command):
    if set(command)!={"preview_id","manifest_digest"}:raise ApiError("INVALID_IMPORT_COMMAND",422,"Exact immutable preview required")
    identifier=UUID(command["preview_id"])
    preview=uow.session.execute(select(import_previews.c.payload).where(import_previews.c.id==identifier)).scalar_one_or_none()
    if preview is None:raise ApiError("NOT_FOUND",404,"Preview not found")
    report=PreviewReport.model_validate(preview["report"])
    if command["manifest_digest"]!=report.change_digest or not report.ready_count or not report.complete:stale()
    actor=ActorContext(UUID(preview["actor_id"]),'import-admission:'+str(identifier),current_version(uow))
    if current_execution_bundle(uow,actor)!=report.bundle:stale()
    SourceService(uow).read(actor,report.selection.source_id,"source.manage")
    rows={r["ordinal"]:r["payload"] for r in uow.session.execute(select(import_rows).where(
        import_rows.c.preview_id==identifier).order_by(import_rows.c.ordinal)).mappings()}
    consumed=_committed(uow,preview,report,rows)
    for key,ref in preview["protected_refs"].items():
        target=UUID(key);authorize(uow,actor,target)
        if target not in consumed and facts.json_value(facts.revision_ref(uow,target))!=ref:stale()
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.rulings import source_evaluation_plan, _relevant
    from lzo.ingestion.rules import authority_rule
    profile=IngestionService(uow)._profile(actor,report.selection.source_id,report.bundle)
    from lzo.ingestion.planning import enabled,revalidate
    prospective=enabled(profile)
    if prospective:revalidate(uow,actor,profile,report.bundle,rows,consumed)
    policy_payload=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==report.bundle.ruling_policy_version_id)).scalar_one()
    policy=ImportAdmissionPolicy.model_validate(policy_payload["import_admission"]) if policy_payload.get("import_admission") else None
    predicates=set();classes=set();scopes=set();effects=set();withdrawals=0;ready=[]
    for ordinal,value in rows.items():
        plan=value["plan"]
        if plan is None:continue
        mapped=MappedRecord.model_validate(plan["mapped"]);target=mapped.identity.resource_id
        if current_source_change(uow,mapped.change)!=mapped.change:stale()
        if target not in consumed:
            current=uow.session.execute(select(facts.objects.c.revision).where(facts.objects.c.id==target)).scalar_one_or_none()
            if (current or 0)!=plan["expected_revision"]:stale()
            if not prospective:
                context,effect,_=source_evaluation_plan(uow,actor,mapped,profile,report.bundle,plan["expected_revision"])
                if effect!=plan["effect"] or context!=plan["context"]:stale()
        elif _relevant(uow,plan["context"]).digest!=plan["context"]["dependencies"]["digest"]:stale()
        if plan['expected_revision'] or target in consumed:facts.get_object_row(uow,actor,target,'edit')
        else:facts.require_create_permission(uow,actor,target,mapped.scope.owner_scope_id)
        classes.add(mapped.class_iri);scopes.add(mapped.scope.owner_scope_id);ready.append(target)
        predicates.update(a.predicate_iri for a in mapped.facts)
        predicates.update(plan['context'].get('withdrawn_predicates',()))
        effects.update(("create","type") if not plan["expected_revision"] else ("facts",))
        if mapped.facts:effects.add("facts")
        if any(a.object.kind=="iri" for a in mapped.facts) or plan['context'].get('withdrawn_relation'):effects.add("relation")
        effect=plan["effect"];row=effect.get("row",{});patch=row.get("patch") or effect.get("patch",{})
        count=len(patch.get("withdraw_support_ids",()))+len(row.get("withdraw_geometry_support_ids",()))
        withdrawals+=count
        if count:effects.add("withdraw")
        if count and mapped.facts:effects.add("replace")
        if row.get("geometry") or row.get("withdraw_geometry_support_ids"):
            effects.add("geometry");predicates.add("urn:lzo:geometry")
    if len(ready)!=report.ready_count or len(set(ready))!=len(ready):stale()
    classified=all((rule:=authority_rule(uow,actor,p,report.bundle)) is not None and not rule.important for p in predicates)
    covered=bool(policy and effects<=set(policy.effect_classes) and predicates<=set(policy.predicate_iris)
        and classes<=set(policy.class_iris) and scopes<=set(policy.owner_scope_ids) and classified)
    bounded=bool(policy and len(ready)<=policy.max_resources and len(rows)<=policy.max_rows and withdrawals<=policy.max_withdrawals)
    proof=OrdinaryImportEvidence(system_policy_version(KIND),digest(policy.model_dump(mode="json") if policy else {}),
        report.change_digest,True,True,covered,bounded,True,tuple(sorted(effects)))
    refs={UUID(i) for i in preview["protected_refs"]}|{identifier}
    return ChangeDescriptor(kind=KIND,target_ids=(identifier,*sorted(refs-{identifier},key=str)),command=command,
        baseline={**report.baseline,str(identifier):report.revision},impact={"manifest_digest":report.change_digest,
            "ready_count":len(ready),"review_count":report.review_count,"invalid_count":report.invalid_count,
            "effect_classes":sorted(effects),"withdrawals":withdrawals},submit_action="source.manage",
        review_action="conflict.review",execute_action="source.manage",operation_key="import:"+str(identifier),
        operation_target_ids=(identifier,),ordinary_import=proof)


def require(uow,actor,report):
    if not report.ready_count or report.selection.semantic_kind in ("schema","shapes"):return
    return ApprovalService(uow).require_executable(actor,report.approval_id,report.change_digest,report.baseline)


register_policy(KIND,descriptor)
