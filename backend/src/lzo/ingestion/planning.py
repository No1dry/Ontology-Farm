"""Finite reference ordering and complete rollback-only S03 prefix validation."""
from collections import Counter
from uuid import UUID
from sqlalchemy import select,func
from lzo.errors import ApiError,Conflict
from lzo.governance.models import resource_heads,outbox_events,revisions
from lzo.ingestion.identity import IdentityService
from lzo.ingestion.mapping import field_terms
from lzo.ingestion.schemas import MappedRecord,RowResult,ParseIssue
from lzo.ingestion.sources import digest
from lzo.semantic import facts
from lzo.semantic.prospective import prospective_rows,prospective_row


def enabled(profile):return any(field.term_kind=='reference' for field in profile.fields)


def graph(uow,actor,profile,changes,*,reserve=False):
    identities={change.change_id:IdentityService(uow).resolve(uow,actor,change,profile,reserve) for change in changes}
    subjects={}
    for change in changes:
        value=change.raw_fields.get('subject',{}).get('value')
        if value is not None:subjects.setdefault(value,[]).append(change.change_id)
    edges={change.change_id:set() for change in changes}
    existing=set(uow.session.execute(select(facts.objects.c.id).where(facts.objects.c.id.in_(
        [match.resource_id for match in identities.values() if match.resource_id]))).scalars())
    for change in changes:
        for field in profile.fields:
            if field.term_kind!='reference':continue
            try:terms=field_terms(field,change.raw_fields)
            except ValueError:continue
            for term in terms:
                matches=subjects.get(term.value,())
                if (len(matches)==1 and matches[0]!=change.change_id
                        and identities[matches[0]].resource_id not in existing):
                    edges[change.change_id].add(matches[0])
    counts=Counter(value.resource_id for value in identities.values() if value.resource_id)
    blocked={key:'IMPORT_TARGET_OVERLAP' for key,value in identities.items() if value.resource_id and counts[value.resource_id]>1}
    ordered=[];pending=set(edges)
    while pending:
        available=[change.change_id for change in changes if change.change_id in pending and not edges[change.change_id]&pending]
        if not available:break
        ordered.extend(available);pending.difference_update(available)
    blocked.update({key:'IMPORT_DEPENDENCY_CYCLE' for key in pending})
    ordered.extend(change.change_id for change in changes if change.change_id in pending)
    return ordered,edges,identities,blocked


def arrange(uow,actor,profile,changes):
    if not enabled(profile):return changes
    ordered,_,_,_=graph(uow,actor,profile,changes,reserve=True)
    by_id={change.change_id:change for change in changes}
    return tuple(by_id[key] for key in ordered)


def incomplete(error):
    if error.code in ('JOB_QUEUE_FULL','VALIDATION_CONTEXT_BUDGET','SHACL_EXECUTION_INCOMPLETE','JOB_BATCH_TIMEOUT'):
        raise ApiError('IMPORT_PLANNING_INCOMPLETE',503,'Prospective planning did not finish within its operational budget') from error


def plan(uow,actor,source,profile,bundle,changes,*,prefix_plans=(),all_changes=None):
    from lzo.ingestion.pipeline import IngestionService
    all_changes=tuple(all_changes or changes)
    _,edges,identities,blocked=graph(uow,actor,profile,all_changes)
    by_target={match.resource_id:key for key,match in identities.items() if match.resource_id}
    rows=[];plans=[];refs={source.id,profile.id,bundle.id};baseline={str(source.id):source.revision};succeeded={};generated=set()
    start=uow.session.execute(select(func.coalesce(func.max(outbox_events.c.position),0))).scalar_one()
    with prospective_rows(uow,actor) as scoped:
        service=IngestionService(scoped)
        for prior in prefix_plans:
            mapped=MappedRecord.model_validate(prior['mapped'])
            try:
                with prospective_row(scoped):service.process_row(scoped,actor,mapped,bundle,prior['expected_revision'])
            except ApiError as error:
                incomplete(error)
                raise
            succeeded[mapped.change.change_id]=prior
        for change in changes:
            target=identities[change.change_id].resource_id
            missing=edges[change.change_id]-set(succeeded)
            code=blocked.get(change.change_id) or ('IMPORT_PREDECESSOR_UNAVAILABLE' if missing else None)
            if code:
                rows.append(RowResult(locator=change.locator,source_key=change.key,state='needs_review',entity_id=target,
                    issue_refs=(ParseIssue(locator=change.locator,field_path=None,code=code,
                        message='A complete valid predecessor prefix is required; the entire dependent row is excluded'),)))
                plans.append(None);refs.update((change.change_id,change.snapshot.version_id));continue
            try:
                with prospective_row(scoped):
                    result,planned,protected,heads=service._plan_changes(actor,source,profile,bundle,(change,),persist_conflicts=False)
                    row=result[0];value=planned[0]
                    if value is not None:
                        prerequisites={key for identifier,key in by_target.items() if identifier in protected and key in succeeded}
                        prerequisites.update(edges[change.change_id])
                        value['predecessors']=[{'target_id':succeeded[key]['mapped']['identity']['resource_id'],
                            'effect_digest':digest(succeeded[key]['effect']),'input_digest':digest(succeeded[key]['mapped']['change']),
                            'decision_digest':digest(succeeded[key]['context'])} for key in sorted(prerequisites,key=str)]
                        service.process_row(scoped,actor,MappedRecord.model_validate(value['mapped']),bundle,value['expected_revision'])
                        succeeded[change.change_id]=value
                    rows.append(row);plans.append(value);refs.update(protected);baseline.update(heads)
            except ApiError as error:
                incomplete(error)
                if error.status in (401,403,404):raise
                rows.append(RowResult(locator=change.locator,source_key=change.key,state='invalid',entity_id=target,
                    issue_refs=(ParseIssue(locator=change.locator,field_path=None,code=error.code,message=error.message),)))
                plans.append(None)
        generated=set(scoped.session.execute(select(revisions.c.resource_id).join(outbox_events,
            outbox_events.c.event_id==revisions.c.event_id).where(outbox_events.c.position>start)).scalars())
    # Only scopes with an original durable head can enter F06's target closure.
    durable=set(uow.session.execute(select(resource_heads.c.resource_id).where(resource_heads.c.resource_id.in_(refs))).scalars())
    if refs-durable-generated:raise Conflict('IMPORT_PLANNING_INCOMPLETE','Unproven prospective reference')
    for key in tuple(baseline):
        identifier=UUID(key)
        if identifier in generated:
            actual=uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id==identifier)).scalar_one_or_none()
            baseline[key]=actual or 0
    # Review cases are real durable records, created only after the sandbox.
    for index,row in enumerate(rows):
        if row.state=='needs_review' and any(issue.code in ('SOURCE_REVIEW_REQUIRED','SOURCE_EVIDENCE_SUPERSEDED') for issue in row.issue_refs):
            from lzo.ingestion.mapping import MappingService
            from lzo.ingestion.rulings import RulingService
            change=changes[index]
            mapped=MappingService(uow).plan(actor,change,profile,bundle,identities[change.change_id])
            conflict=RulingService(uow).prepare(uow,actor,mapped,bundle)
            rows[index]=row.model_copy(update={'conflict_ids':(conflict.id,)})
            durable.add(conflict.id)
    return rows,plans,durable,baseline


def revalidate(uow,actor,profile,bundle,rows,consumed):
    """One full prefix replay; committed predecessors require owner event proof."""
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.rulings import source_evaluation_plan
    ready={}
    with prospective_rows(uow,actor) as scoped:
        service=IngestionService(scoped)
        for value in rows.values():
            plan=value['plan']
            if plan is None:continue
            mapped=MappedRecord.model_validate(plan['mapped']);target=mapped.identity.resource_id
            for predecessor in plan.get('predecessors',()):
                original=ready.get(predecessor['target_id'])
                if original is None or predecessor!={'target_id':original['mapped']['identity']['resource_id'],
                        'effect_digest':digest(original['effect']),'input_digest':digest(original['mapped']['change']),
                        'decision_digest':digest(original['context'])}:
                    raise Conflict('IMPORT_PREVIEW_STALE','Prospective predecessor evidence changed')
            if target not in consumed:
                try:
                    with prospective_row(scoped):
                        context,effect,_=source_evaluation_plan(scoped,actor,mapped,profile,bundle,plan['expected_revision'])
                        if context!=plan['context'] or effect!=plan['effect']:
                            raise Conflict('IMPORT_PREVIEW_STALE','Prospective row changed')
                        service.process_row(scoped,actor,mapped,bundle,plan['expected_revision'])
                except ApiError as error:
                    incomplete(error)
                    raise
            ready[str(target)]=plan
