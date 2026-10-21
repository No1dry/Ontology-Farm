"""Immutable independently reviewed exact effects and relevant currentness.

Relevant equivalence v1 hashes selected field inputs and their expression paths,
identity inputs, intervals, source nature/scope, field authority and ruling rules,
and applicable class/property definitions plus the conservative shape closure.
Exact history pins remain a separate ALL-protected provenance closure.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5
from sqlalchemy import insert, select, exists, func, cast
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from lzo.contracts import ResourceScope, RevisionRef, ExecutionBundle, Page
from lzo.errors import ApiError, Conflict, Forbidden
from lzo.governance.approvals import ApprovalService
from lzo.governance.models import revisions, resource_heads, outbox_events
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import ChangeDescriptor, register_policy
from lzo.ingestion.models import conflicts, rulings, source_records, rule_versions, mapping_profiles, sources
from lzo.ingestion.schemas import (CandidateValue, ConflictView, DependencyBinding, RulingView,
    MappingProfile, SourceChange)
from lzo.ingestion.sources import SourceService, add_scoped, digest
from lzo.ingestion.mapping import MappingService, path_value, evaluate
from lzo.ontology.drafts import authorize, bind_dependencies, definition_source_closure, head, visible_statement
from lzo.ontology.models import catalog, bundles, releases
from lzo.semantic import facts
from lzo.semantic.schemas import CreateObjectInput, FactPatch, DecisionEvidence
from lzo.semantic.projection import register_nonsemantic


def _latest(uow, identifier):
    return uow.session.execute(select(revisions.c.payload).join(resource_heads,
        resource_heads.c.event_id==revisions.c.event_id).where(resource_heads.c.resource_id==identifier)).scalar_one()


def _context(uow, identifier, reference=None):
    if reference is not None:
        ref=RevisionRef(UUID(str(reference["resource_id"])),reference["revision"],UUID(str(reference["event_id"])))
        row=uow.session.execute(select(revisions).where(revisions.c.resource_id==identifier,
            revisions.c.event_id==ref.event_id,revisions.c.revision==ref.revision)).mappings().one_or_none()
        if row is None or "facts" not in row["payload"]:raise Conflict("RULING_CONTEXT_INVALID","Exact context unavailable")
        return row["payload"]|{"origin_ref":facts.json_value(ref)}
    row=uow.session.execute(select(revisions).where(revisions.c.resource_id==identifier,
        revisions.c.event_type.in_(("ingestion.conflict.created","ingestion.conflict.refreshed")))
        .order_by(revisions.c.revision.desc()).limit(1)).mappings().first()
    if row:
        return row["payload"]|{"origin_ref":facts.json_value(RevisionRef(identifier,row["revision"],row["event_id"]))}
    value=uow.session.execute(select(conflicts.c.payload).where(conflicts.c.id==identifier)).scalar_one_or_none()
    if value is None: raise ApiError("NOT_FOUND",404,"Conflict not found")
    return value


def _paths(expr):
    if not expr: return set()
    return ({expr.path} if expr.op=="path" else set()) | set().union(*(_paths(a) for a in expr.args))


def _ruling_context(uow, row):
    if row.get("context_ref"):
        return _context(uow,UUID(row["conflict_id"]),row["context_ref"])
    # Pre-round records always belong to the immutable first context, never a
    # subsequently appended case round. They can still be reviewed/replaced.
    return uow.session.execute(select(conflicts.c.payload).where(conflicts.c.id==UUID(row["conflict_id"]))).scalar_one()


def _current_change(uow, old):
    from lzo.ingestion.sources import current_source_change
    return current_source_change(uow,old)


def _relevant(uow, context, *, original=False):
    """Read-only trusted reconstruction; never consumes a caller digest/flag."""
    old=SourceChange.model_validate(context["change"])
    profile=MappingProfile.model_validate(context["profile"])
    predicates={a["predicate_iri"] for a in context["facts"]}
    predicates.update(context.get('withdrawn_predicates',()))
    if context.get("geometry") is not None or context.get("geometry_support_ids") or context.get('withdrawn_geometry_support_ids'):
        predicates.add("urn:lzo:geometry")
    current=old if original else _current_change(uow,old)
    selected=uow.session.execute(select(bundles).join(catalog,catalog.c.active_bundle_id==bundles.c.id)).mappings().one()
    aggregate=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==selected["mapping_version_id"])).scalar_one_or_none()
    member=next((m for m in (aggregate or {}).get("members",()) if m["source_id"]==str(old.key.source_id)),None)
    if member:
        updated=uow.session.execute(select(mapping_profiles.c.payload).where(mapping_profiles.c.id==UUID(member["profile_id"]))).scalar_one()
        profile=MappingProfile.model_validate(updated)
    else:
        raise ValueError("Source no longer has a current mapping")
    paths=set(profile.identity_paths) | (set() if context.get("type_only") else {profile.name_path})
    if "urn:lzo:geometry" in predicates and profile.geometry_path:paths.add(profile.geometry_path)
    fields=[]
    for field in profile.fields:
        if field.predicate_iri in predicates:
            paths.add(field.source_path); paths.update(_paths(field.expression))
            fields.append(field.model_dump(mode="json"))
    rule_payload=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==selected["authority_version_id"])).scalar_one()
    authority=[r for r in rule_payload["authority_rules"] if r["predicate_iri"] in predicates]
    ruling_payload=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==selected["ruling_policy_version_id"])).scalar_one()
    policies=[r for r in ruling_payload["ruling_policies"] if predicates.intersection(r["predicate_iris"])]
    from lzo.ingestion.schemas import RuleExpr
    evaluations=[]
    for policy in policies:
        expression=RuleExpr.model_validate(policy["expression"])
        inputs=_paths(expression);paths.update(inputs)
        if any(path_value(current.raw_fields,path) is None for path in inputs):
            raise ValueError("Ruling condition input unknown")
        result=evaluate(expression,current.raw_fields)
        if type(result) is not bool:raise ValueError("Ruling condition must be boolean")
        evaluations.append({"policy_digest":policy["digest"],"result":result})
    for rule in authority:
        if rule.get("comparable_time_path"): paths.add(rule["comparable_time_path"])
    source=uow.session.execute(select(sources.c.configuration).where(sources.c.id==old.key.source_id)).scalar_one()
    document=uow.session.execute(select(releases.c.document).where(releases.c.id==selected["schema_release_id"])).scalar_one()
    classes={profile.class_iri}
    while True:
        before=set(classes)
        for c in document["classes"]:
            if c["iri"] in classes: classes.update(c.get("parents",()))
        if before==classes: break
    schema={"classes":[{k:v for k,v in c.items() if k not in ("label","description")} for c in document["classes"] if c["iri"] in classes],
        "properties":[{k:v for k,v in p.items() if k not in ("label","description")} for p in document["properties"] if p["iri"] in predicates],
        "relations":[r for r in document["relations"] if r["iri"] in predicates],
        "shapes_digest":selected["shapes_digest"]}
    if context.get("type_only"):schema={"classes":schema["classes"]}
    alignment=None
    if profile.confirmed_identity_rule_id:
        alignment=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==profile.confirmed_identity_rule_id)).scalar_one()
        paths.update(alignment["key_paths"])
    evidence=digest({"operation":current.operation,"selected_values":{p:path_value(current.raw_fields,p) for p in sorted(paths)},
        "source_key":current.key.model_dump(mode="json"),"valid_from":str(current.valid_from),"valid_to":str(current.valid_to),
        "observed_at":str(current.observed_at),"nature":current.data_nature,"source_configuration":source})
    # Original snapshot replacement/rejection is a relevant event even when the
    # latest normalized record still happens to contain the same value.
    from lzo.artifacts.models import artifact_reviews
    review=uow.session.execute(select(artifact_reviews.c.decision).join(outbox_events,
        outbox_events.c.event_id==artifact_reviews.c.event_id).where(artifact_reviews.c.version_id==old.snapshot.version_id)
        .order_by(outbox_events.c.position.desc()).limit(1)).scalar_one_or_none()
    value={"contract":"ruling-relevant-v2","evidence":evidence,"review":review,"policy_evaluations":evaluations,
        "fields":fields,"identity_paths":list(profile.identity_paths),"alignment":alignment,
        "class":profile.class_iri,"authority":authority,"ruling":policies,"schema":schema}
    if context.get("effect_contract")=="source-row-v1":
        value.update(effect_contract="source-row-v1",source_crs=profile.source_crs,
            geometry=current.geometry if not profile.geometry_path else path_value(current.raw_fields,profile.geometry_path),
            typed_values=[a["object"] for a in context["facts"]],
            source_graph=current.raw_fields.get("graph"),source_subject=current.raw_fields.get("subject"))
    binding=DependencyBinding(entity_id=UUID(context["entity_id"]),source_keys=(old.key,),
        predicate_iris=tuple(sorted(predicates)),support_ids=tuple(UUID(a["support"]["support_id"]) for a in context["facts"]),
        evidence_digests=(evidence,),valid_from=old.valid_from,valid_to=old.valid_to,
        authority_rule_digest=digest(authority),ruling_rule_digest=digest(policies),schema_dependency_digest=digest(schema),digest=digest(value))
    return binding


def _effect(context, chosen):
    additions=[a | {"state":"accepted"} for a in context["facts"] if a["assertion_id"] in chosen]
    if context["expected_revision"]==0:
        if context.get("identity_candidate_id") not in chosen:
            if additions or context.get('geometry_candidate_id') in chosen:raise ApiError("IDENTITY_RULING_REQUIRED",422,"Fields cannot be adopted without identity/type decision")
            return {"operation":"candidate.reject","entity_id":context["entity_id"]}
        effect={"operation":"object.create","create":CreateObjectInput(resource_id=UUID(context["entity_id"]),
            class_iris=(context["profile"]["class_iri"],),display_name=context["display_name"],
            owner_scope_id=UUID(context["scope"]["owner_scope_id"]),related_scope_ids=tuple(UUID(i) for i in context["scope"]["related_scope_ids"]),
            state="active",data_nature=context["change"]["data_nature"],assertions=additions).model_dump(mode="json")}
    else:
        effect={"operation":"facts.patch","patch":FactPatch(resource_id=UUID(context["entity_id"]),expected_revision=context["expected_revision"],
            additions=additions).model_dump(mode="json")}
    if context.get('geometry_candidate_id') in chosen:
        from lzo.semantic.schemas import SourceRowInput
        row=SourceRowInput(create=effect.get('create'),patch=effect.get('patch'),geometry=context['row_geometry'],geometry_action='replace')
        return {'operation':'source.row','row':row.model_dump(mode='json')}
    return effect


def _descriptor(uow,targets,command):
    if set(command)!={"conflict_id","chosen_candidate_ids","rejected_candidate_ids","rationale"}:
        raise ApiError("INVALID_RULING_COMMAND",422,"Exact ruling choices required")
    identifier=UUID(command["conflict_id"]); context=_context(uow,identifier)
    chosen,rejected=set(command["chosen_candidate_ids"]),set(command["rejected_candidate_ids"])
    candidates={a["assertion_id"] for a in context["facts"]}
    if context.get("identity_candidate_id"):candidates.add(context["identity_candidate_id"])
    if context.get('geometry_candidate_id'):candidates.add(context['geometry_candidate_id'])
    if chosen&rejected or chosen|rejected!=candidates or not command["rationale"] or len(command["rationale"])>4000:
        raise ApiError("INVALID_RULING_CHOICES",422,"Each candidate requires one explicit disposition")
    try:current=_relevant(uow,context)
    except (ValueError,KeyError):raise Conflict("APPROVAL_STALE","Relevant evidence cannot be evaluated") from None
    if current.digest!=context["dependencies"]["digest"]:
        raise Conflict("APPROVAL_STALE","Relevant evidence changed")
    refs={UUID(i) for i in context["protected_ids"]}|{identifier}
    baseline={str(i):head(uow,i)["revision"] for i in refs}
    existing=uow.session.execute(select(facts.objects.c.revision).where(facts.objects.c.id==UUID(context["entity_id"]))).scalar_one_or_none()
    if (existing or 0)!=context["expected_revision"]:
        raise Conflict("APPROVAL_STALE","Semantic target changed or effect was consumed")
    state=_latest(uow,identifier)
    if state.get("state")=="resolved":
        resolved=uow.session.execute(select(rulings.c.payload).where(rulings.c.id==UUID(state["ruling_id"]))).scalar_one()
        original=RevisionRef(**{**context["origin_ref"],"resource_id":UUID(context["origin_ref"]["resource_id"]),"event_id":UUID(context["origin_ref"]["event_id"])})
        original_event=uow.session.execute(select(revisions).where(revisions.c.event_id==original.event_id,
            revisions.c.resource_id==identifier,revisions.c.revision==original.revision)).mappings().one()
        successor=facts.revision_ref(uow,identifier)
        ruling_ref=facts.revision_ref(uow,UUID(state["ruling_id"]))
        ruling_event=uow.session.execute(select(revisions).where(revisions.c.event_id==ruling_ref.event_id)).mappings().one()
        expected_state={"state":"resolved","ruling_id":state["ruling_id"],"revision":context["revision"]+1,
            "approval_id":resolved["approval_id"],"origin_ref":context["origin_ref"],"ruling_ref":facts.json_value(ruling_ref),
            "command_digest":digest(command),"effect_digest":digest(_effect(context,chosen))}
        if (resolved["command"]!=command or facts.json_value(successor)!=resolved["resolution_ref"]
                or state!=expected_state or ruling_ref.revision!=1
                or original_event["payload"]!={k:v for k,v in context.items() if k!="origin_ref"}
                or ruling_event["payload"]!={k:v for k,v in resolved.items() if k not in ("resolution_ref","supersession_ref")}):
            raise Conflict("APPROVAL_STALE","Conflict resolution changed")
        from lzo.governance.approvals import approvals
        initiator=uow.session.execute(select(approvals.c.submitter_id).where(approvals.c.id==UUID(resolved["approval_id"]))).scalar_one()
        successor_actor=uow.session.execute(select(revisions.c.actor_id).where(revisions.c.event_id==successor.event_id)).scalar_one()
        if ruling_event["actor_id"]!=initiator or successor_actor!=initiator:
            raise Conflict("APPROVAL_STALE","Resolution initiator differs from approval")
        if resolved.get("context_ref")!=context["origin_ref"]:
            raise Conflict("APPROVAL_STALE","Ruling context differs")
        if context.get("replaces_ref"):
            before=context["replaces_ref"]; prior_id=UUID(before["resource_id"])
            after=resolved.get("supersession_ref")
            if not after or facts.json_value(facts.revision_ref(uow,prior_id))!=after:
                raise Conflict("APPROVAL_STALE","Superseded ruling changed")
            prior=uow.session.execute(select(revisions).where(revisions.c.event_id==UUID(before["event_id"]),
                revisions.c.resource_id==prior_id,revisions.c.revision==before["revision"])).mappings().one()
            supersession=uow.session.execute(select(revisions).where(revisions.c.event_id==UUID(after["event_id"]),
                revisions.c.resource_id==prior_id,revisions.c.revision==before["revision"]+1)).mappings().one()
            expected_prior=prior["payload"]|{"state":"superseded","replacement_ref":facts.json_value(ruling_ref),
                "replacement_context_ref":context["origin_ref"],"replacement_approval_id":resolved["approval_id"]}
            if (prior["payload"]["state"]!="needs_review" or prior["payload"]["conflict_id"]!=str(identifier)
                    or resolved["replaces_id"]!=str(prior_id) or supersession["payload"]!=expected_prior
                    or supersession["actor_id"]!=initiator or supersession["event_type"]!="ingestion.ruling.superseded"):
                raise Conflict("APPROVAL_STALE","Supersession is not this exact approved transition")
            baseline[str(prior_id)]=before["revision"]
        baseline[str(identifier)]=context["revision"]
    elif state.get("state") not in (None,"open"):
        raise Conflict("APPROVAL_STALE","Conflict is not open")
    elif context.get("replaces_ref"):
        prior=context["replaces_ref"];prior_id=UUID(prior["resource_id"])
        if facts.json_value(facts.revision_ref(uow,prior_id))!=prior or _latest(uow,prior_id)["state"]!="needs_review":
            raise Conflict("APPROVAL_STALE","Previous ruling changed")
    return ChangeDescriptor(kind="ingestion.ruling",target_ids=(identifier,*sorted(refs-{identifier},key=str)),
        command=command,baseline=baseline,impact={"effect":_effect(context,chosen),"dependencies":current.model_dump(mode="json")},
        submit_action="conflict.resolve",review_action="conflict.review",execute_action="conflict.resolve",
        operation_key="ingestion.ruling:"+str(identifier),operation_target_ids=(identifier,))


@dataclass(frozen=True)
class SupportCurrentness:
    eligible: bool
    reason: str
    relevant_digest: str | None
    references: tuple[RevisionRef,...]


class RulingService:
    def __init__(self,uow=None): self.uow=uow

    def type_currentness(self,uow,decision_ref,resource_id):
        from lzo.auth.models import resource_scopes
        from lzo.ingestion.models import source_evaluations
        kind=uow.session.execute(select(resource_scopes.c.resource_kind).where(resource_scopes.c.resource_id==decision_ref.resource_id)).scalar_one_or_none()
        if kind=='ingestion.ruling':
            stored=uow.session.execute(select(rulings.c.payload).where(rulings.c.id==decision_ref.resource_id)).scalar_one_or_none()
            event=uow.session.execute(select(revisions).where(revisions.c.event_id==decision_ref.event_id,
                revisions.c.resource_id==decision_ref.resource_id,revisions.c.revision==decision_ref.revision)).mappings().one_or_none()
            if (not stored or not event or event['event_type']!='ingestion.ruling.resolved'
                    or event['payload']!={k:v for k,v in stored.items() if k not in ('resolution_ref','supersession_ref')}):
                raise Conflict('TYPE_EVIDENCE_UNAVAILABLE','Exact reviewed type decision is unavailable')
            context=_ruling_context(uow,stored)
            chosen=set(stored['chosen_candidate_ids'])
            effect=_effect(context,chosen)
            create=effect.get('create') or effect.get('row',{}).get('create')
            if (context['expected_revision']!=0 or context.get('identity_candidate_id') not in chosen
                    or context['entity_id']!=str(resource_id) or not create or create['resource_id']!=str(resource_id)
                    or effect!=stored['effect']):
                raise Conflict('TYPE_EVIDENCE_UNAVAILABLE','Reviewed decision did not create this type declaration')
            try:
                actual=_relevant(uow,context)
                valid=(_latest(uow,decision_ref.resource_id)['state']=='effective'
                    and actual.digest==stored['dependencies']['digest']
                    and facts.is_effective({'valid_from':context['change']['valid_from'] or datetime.min.replace(tzinfo=timezone.utc).isoformat(),
                        'valid_to':context['change']['valid_to']},facts.evaluation_time(uow)))
            except (ValueError,KeyError):return SupportCurrentness(False,'unknown_reviewed_type_equivalence',None,(decision_ref,))
            # Legacy bytes have a real full materiality binding, not a stored
            # type-only baseline. Use it conservatively without reconstructing
            # two baselines from today's configuration.
            return SupportCurrentness(valid,'unchanged' if valid else 'reviewed_type_invalidated',actual.digest,(decision_ref,))
        if kind!="ingestion.source_evaluation":return None
        stored=uow.session.execute(select(source_evaluations.c.payload).where(source_evaluations.c.id==decision_ref.resource_id)).scalar_one_or_none()
        if not stored or facts.revision_ref(uow,decision_ref.resource_id)!=decision_ref:
            return SupportCurrentness(False,"invalid_source_type_evaluation",None,(decision_ref,))
        context=stored["context"]
        try:
            actual=_relevant(uow,_type_context(context))
            valid=(context["entity_id"]==str(resource_id) and context["expected_revision"]==0
                and actual.digest==context["type_dependencies"]["digest"]
                and facts.is_effective({"valid_from":context["change"]["valid_from"] or datetime.min.replace(tzinfo=timezone.utc).isoformat(),
                    "valid_to":context["change"]["valid_to"]},facts.evaluation_time(uow)))
        except (ValueError,KeyError):return SupportCurrentness(False,"unknown_type_equivalence",None,(decision_ref,))
        return SupportCurrentness(valid,"unchanged" if valid else "source_type_invalidated",actual.digest,(decision_ref,))

    def prepare(self,uow,actor,mapped,bundle):
        return self._prepare_round(uow,actor,mapped,bundle)

    def _prepare_round(self,uow,actor,mapped,bundle,previous=None):
        from lzo.ingestion.identity import IdentityService
        from lzo.ingestion.rules import aggregate
        member=next((m for m in aggregate(uow,bundle)["members"] if m["source_id"]==str(mapped.change.key.source_id)),None)
        profile=SourceService(uow).get_mapping(actor,mapped.change.key.source_id,UUID(member["profile_id"])) if member else None
        if profile is None or mapped.identity.resource_id is None:
            raise ApiError("NO_MAPPED_CANDIDATES",422,"Reserved identity and exact published profile required")
        verified=MappingService(uow).plan(actor,mapped.change,profile,bundle,mapped.identity)
        if verified!=mapped: raise Conflict("MAPPING_PLAN_MISMATCH","Mapping plan differs")
        lock_ledger(uow)
        identifier=previous.conflict_id if previous else uuid4(); scope=mapped.scope
        prior_revision=head(uow,identifier)["revision"] if previous else 0
        if not previous:
            add_scoped(uow,actor,identifier,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),"ingestion.conflict","conflict.resolve")
        existing=uow.session.execute(select(facts.objects).where(facts.objects.c.id==mapped.identity.resource_id)).mappings().first()
        if existing: facts.get_object_row(uow,actor,mapped.identity.resource_id,"edit")
        refs={bundle.id,profile.id,mapped.change.change_id,mapped.change.snapshot.version_id,mapped.change.key.source_id}
        from lzo.ingestion.models import identity_bindings
        binding_id=uow.session.execute(select(identity_bindings.c.id).where(
            identity_bindings.c.source_id==mapped.change.key.source_id,
            identity_bindings.c.canonical_key==mapped.change.key.canonical_key)).scalar_one()
        refs.add(binding_id)
        if existing: refs.add(mapped.identity.resource_id)
        if previous:refs.add(previous.id)
        context={"id":str(identifier),"revision":prior_revision+1,"entity_id":str(mapped.identity.resource_id),
            "scope":facts.json_value(ResourceScope(identifier,scope.owner_scope_id,scope.related_scope_ids,scope.security_class)),
            "change":mapped.change.model_dump(mode="json"),"profile":profile.model_dump(mode="json"),"display_name":mapped.display_name,
            "bundle_id":str(bundle.id),"expected_revision":existing["revision"] if existing else 0,
            "facts":[a.model_dump(mode="json") for a in mapped.facts],"state":"open"}
        if mapped.geometry is not None:
            from lzo.semantic.schemas import RowGeometry,SourceSupport
            if not profile.source_crs:raise ApiError('GEOMETRY_CRS_REQUIRED',422,'Geometry requires an explicit CRS')
            sid=uuid5(identifier,'geometry-support:'+str(prior_revision+1))
            geometry=RowGeometry(geojson=mapped.geometry,source_crs=profile.source_crs,source_support=SourceSupport(
                support_id=sid,source_id=profile.source_id,record_key=mapped.change.key.canonical_key,
                source_version=mapped.change.position.source_version,snapshot=mapped.change.snapshot,
                raw_value={'geometry':mapped.geometry,'source_crs':profile.source_crs},mapping_version_id=profile.id,
                evidence_refs=(facts.revision_ref(uow,mapped.change.change_id),facts.revision_ref(uow,profile.id)),
                observed_at=mapped.change.observed_at),valid_from=mapped.change.valid_from or datetime.min.replace(tzinfo=timezone.utc),
                valid_to=mapped.change.valid_to)
            context.update(effect_contract='source-row-v1',geometry=mapped.geometry,geometry_support_ids=[str(sid)],
                geometry_candidate_id=str(uuid5(identifier,'geometry-candidate:'+str(prior_revision+1))),row_geometry=geometry.model_dump(mode='json'))
        if previous:
            context["replaces_ref"]=facts.json_value(previous.revision)
            # Source identity remains stable; a newly approved justification is
            # a distinct immutable assertion/support. Generate once before
            # preview and retain the verified mapper's exact evidence lineage.
            round_id=uuid4();context["round_id"]=str(round_id)
            for assertion in context["facts"]:
                support=assertion["support"]
                origin={"round_id":str(round_id),"mapped_assertion_id":assertion["assertion_id"],
                    "mapped_support_id":support["support_id"],"change_id":str(mapped.change.change_id),
                    "profile_id":str(profile.id),"snapshot":facts.json_value(mapped.change.snapshot)}
                assertion["assertion_id"]=str(uuid5(round_id,"assertion:"+origin["mapped_assertion_id"]))
                support["support_id"]=str(uuid5(round_id,"support:"+origin["mapped_support_id"]))
                support["raw_value"]={**support["raw_value"],"ruling_round_origin":origin}
        if not existing:
            context["identity_candidate_id"]=str(uuid5(identifier,"source-type-identity"))
        context["protected_ids"]=sorted(map(str,definition_source_closure(uow,refs)-{identifier}))
        try:
            original=_relevant(uow,context,original=True)
            current=_relevant(uow,context)
        except (ValueError,KeyError):
            raise Conflict("RULING_EVALUATION_UNKNOWN","Relevant evidence cannot be evaluated") from None
        if original.digest!=current.digest:
            raise Conflict("SOURCE_EVIDENCE_SUPERSEDED","Candidate evidence is materially superseded")
        context["dependencies"]=original.model_dump(mode="json")
        event=append_revision(uow,actor,identifier,prior_revision,context,
            "ingestion.conflict.refreshed" if previous else "ingestion.conflict.created")
        from lzo.ontology.drafts import bind_history_dependencies
        # Old rulings already depend on this stable case. Keep their new reverse
        # links on this round's immutable history, never create a current cycle.
        current_refs=definition_source_closure(uow,refs)-{identifier}
        if previous:
            current_refs.difference_update(uow.session.execute(select(rulings.c.id).where(rulings.c.conflict_id==identifier)).scalars())
        bind_dependencies(uow,identifier,current_refs,event.event_id)
        bind_history_dependencies(uow,event.event_id,{UUID(i) for i in context["protected_ids"]})
        context={**context,"origin_ref":facts.json_value(event)}
        if not previous:uow.session.execute(insert(conflicts).values(id=identifier,payload=context))
        return self.get(actor,identifier)

    def refresh(self,uow,actor,identifier,expected_revision,ruling_ref):
        lock_ledger(uow)
        authorize(uow,actor,identifier,"conflict.resolve")
        view=self.get(actor,identifier); previous=self.ruling(actor,ruling_ref.resource_id)
        state=_latest(uow,identifier)
        if (view.revision!=expected_revision or previous.revision!=ruling_ref or previous.state!="needs_review"
                or previous.conflict_id!=identifier or state.get("state")!="needs_review"
                or state.get("ruling_id")!=str(previous.id)):
            raise Conflict("REVISION_CONFLICT","Review round changed")
        old=_context(uow,identifier)
        change=_current_change(uow,SourceChange.model_validate(old["change"]))
        from lzo.ontology.releases import current_execution_bundle
        from lzo.ingestion.rules import aggregate
        from lzo.ingestion.identity import IdentityService
        bundle=current_execution_bundle(uow,actor)
        SourceService(uow).read(actor,change.key.source_id,"source.manage")
        member=next((m for m in aggregate(uow,bundle)["members"] if m["source_id"]==str(change.key.source_id)),None)
        if member is None:raise Conflict("MAPPING_UNAVAILABLE","Current source mapping required")
        profile=SourceService(uow).get_mapping(actor,change.key.source_id,UUID(member["profile_id"]))
        identity=IdentityService(uow).resolve(uow,actor,change,profile,False)
        if str(identity.resource_id)!=old["entity_id"]:
            raise Conflict("IDENTITY_REVIEW_REQUIRED","Replacement cannot change the reserved identity")
        mapped=MappingService(uow).plan(actor,change,profile,bundle,identity)
        return self._prepare_round(uow,actor,mapped,bundle,previous)

    def get(self,actor,identifier):
        authorize(self.uow,actor,identifier)
        context=_context(self.uow,identifier); state=_latest(self.uow,identifier)
        for protected in context["protected_ids"]:authorize(self.uow,actor,UUID(protected))
        change=SourceChange.model_validate(context["change"])
        candidates=tuple(CandidateValue(id=UUID(a["assertion_id"]),entity_id=UUID(context["entity_id"]),predicate_iri=a["predicate_iri"],
            raw_value=a["support"]["raw_value"],normalized_value=a["object"],source_key=change.key,snapshot=change.snapshot,locator=change.locator,
            evidence_refs=a["support"]["evidence_refs"],bundle_id=UUID(context["bundle_id"]),source_version=change.position.source_version,
            observed_at=change.observed_at,valid_from=a["valid_from"],valid_to=a["valid_to"],data_nature=change.data_nature) for a in context["facts"])
        if context.get("identity_candidate_id"):
            from lzo.semantic.rdf import RdfTerm
            candidates=(CandidateValue(id=UUID(context["identity_candidate_id"]),entity_id=UUID(context["entity_id"]),predicate_iri=None,
                raw_value={p:path_value(change.raw_fields,p) for p in context["profile"]["identity_paths"]},
                normalized_value=RdfTerm("iri",context["profile"]["class_iri"]),source_key=change.key,snapshot=change.snapshot,
                locator=change.locator,evidence_refs=(facts.revision_ref(self.uow,change.change_id),),bundle_id=UUID(context["bundle_id"]),
                source_version=change.position.source_version,observed_at=change.observed_at,valid_from=change.valid_from,
                valid_to=change.valid_to,data_nature=change.data_nature),*candidates)
        if context.get('geometry_candidate_id'):
            from lzo.semantic.rdf import RdfTerm
            from lzo.ingestion.sources import canonical
            geometry=context['row_geometry']
            candidates=(*candidates,CandidateValue(id=UUID(context['geometry_candidate_id']),entity_id=UUID(context['entity_id']),
                predicate_iri='urn:lzo:geometry',raw_value=geometry['source_support']['raw_value'],
                normalized_value=RdfTerm('literal',canonical(geometry['geojson']),'http://www.w3.org/2001/XMLSchema#string'),
                source_key=change.key,snapshot=change.snapshot,locator=change.locator,evidence_refs=geometry['source_support']['evidence_refs'],
                bundle_id=UUID(context['bundle_id']),source_version=change.position.source_version,observed_at=change.observed_at,
                valid_from=geometry['valid_from'],valid_to=geometry['valid_to'],data_nature=change.data_nature))
        return ConflictView(id=identifier,revision=head(self.uow,identifier)["revision"],scope=context["scope"],entity_id=UUID(context["entity_id"]),
            predicate_iri=candidates[0].predicate_iri if len(candidates)==1 else None,candidate_ids=tuple(c.id for c in candidates),
            candidate_values=candidates,dependencies=context["dependencies"],state=state["state"],reason="Independent source evidence ruling")

    def preview(self,actor,identifier,chosen,rejected,rationale):
        command={"conflict_id":str(identifier),"chosen_candidate_ids":sorted(map(str,chosen)),"rejected_candidate_ids":sorted(map(str,rejected)),"rationale":rationale}
        service=ApprovalService(self.uow); change=service.preview(actor,"ingestion.ruling",[identifier],command)
        return service.submit(actor,change.kind,list(change.target_ids),change.change_digest,change.baseline,{"command":command})

    def resolve(self,uow,actor,identifier,command):
        view=self.get(actor,identifier)
        if view.revision!=command.expected_revision or view.state!="open": raise Conflict("REVISION_CONFLICT","Conflict changed")
        intent={"conflict_id":str(identifier),"chosen_candidate_ids":sorted(map(str,command.chosen_candidate_ids)),
            "rejected_candidate_ids":sorted(map(str,command.rejected_candidate_ids)),"rationale":command.rationale}
        service=ApprovalService(uow); change=service.preview(actor,"ingestion.ruling",[identifier],intent)
        approval=service.require_executable(actor,command.approval_id,change.change_digest,change.baseline)
        ruling_id=uuid4(); context=_context(uow,identifier); scope=view.scope
        add_scoped(uow,actor,ruling_id,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),"ingestion.ruling","conflict.resolve")
        payload={"id":str(ruling_id),"conflict_id":str(identifier),"dependencies":view.dependencies.model_dump(mode="json"),
            "chosen_candidate_ids":intent["chosen_candidate_ids"],"rejected_candidate_ids":intent["rejected_candidate_ids"],
            "state":"effective","rationale":command.rationale,"reviewer_id":str(approval.reviewer_id),"decided_at":approval.decided_at.isoformat(),
            "replaces_id":context.get("replaces_ref",{}).get("resource_id"),"context_ref":context["origin_ref"],
            "command":intent,"approval_id":str(approval.id),"effect":change.impact["effect"],"bundle_id":context["bundle_id"]}
        event=append_revision(uow,actor,ruling_id,0,payload,"ingestion.ruling.resolved")
        refs=definition_source_closure(uow,set(change.target_ids)|{approval.id})
        bind_dependencies(uow,ruling_id,refs,event.event_id)
        supersession=None
        if context.get("replaces_ref"):
            prior=context["replaces_ref"]; prior_id=UUID(prior["resource_id"])
            if facts.json_value(facts.revision_ref(uow,prior_id))!=prior or _latest(uow,prior_id)["state"]!="needs_review":
                raise Conflict("REVISION_CONFLICT","Previous ruling changed")
            supersession=append_revision(uow,actor,prior_id,prior["revision"],_latest(uow,prior_id)|{
                "state":"superseded","replacement_ref":facts.json_value(event),"replacement_context_ref":context["origin_ref"],
                "replacement_approval_id":str(approval.id)},"ingestion.ruling.superseded")
            from lzo.ontology.drafts import bind_history_dependencies
            bind_history_dependencies(uow,supersession.event_id,{ruling_id,approval.id})
        successor=append_revision(uow,actor,identifier,view.revision,{"state":"resolved","ruling_id":str(ruling_id),"revision":view.revision+1,
            "approval_id":str(approval.id),"origin_ref":context["origin_ref"],"ruling_ref":facts.json_value(event),
            "command_digest":digest(intent),"effect_digest":digest(change.impact["effect"])},"ingestion.conflict.resolved")
        from lzo.ontology.drafts import bind_history_dependencies
        bind_history_dependencies(uow,successor.event_id,refs-{identifier})
        uow.session.execute(insert(rulings).values(id=ruling_id,conflict_id=identifier,payload=payload|{
            "resolution_ref":facts.json_value(successor),**({"supersession_ref":facts.json_value(supersession)} if supersession else {})}))
        return self.ruling(actor,ruling_id)

    def ruling(self,actor,identifier):
        authorize(self.uow,actor,identifier)
        payload=_latest(self.uow,identifier)
        keys=RulingView.model_fields.keys()-{"revision"}
        return RulingView(**{k:payload[k] for k in keys},revision=facts.revision_ref(self.uow,identifier))

    def list_conflicts(self,actor,cursor=None,limit=50):
        from lzo.auth.models import resource_scopes
        from lzo.auth.permissions import validate_actor
        validate_actor(self.uow,actor)
        # Current context-only references deliberately stay outside the case's
        # dependency edges (an old ruling already depends on the case). Apply
        # the same shared authorize predicate to each before page selection.
        resources=select(resource_scopes.c.resource_id.label("id")).subquery()
        visible=visible_statement(actor,resources).with_only_columns(resources.c.id).cte()
        latest_context=select(revisions.c.payload).where(revisions.c.resource_id==conflicts.c.id,
            revisions.c.event_type.in_(("ingestion.conflict.created","ingestion.conflict.refreshed")))\
            .order_by(revisions.c.revision.desc()).limit(1).correlate(conflicts).scalar_subquery()
        protected=func.jsonb_array_elements_text(func.coalesce(latest_context,conflicts.c.payload)["protected_ids"])\
            .table_valued("value").alias()
        hidden=exists(select(protected.c.value).where(cast(protected.c.value,PgUUID).not_in(select(visible.c.id)))
            .correlate(conflicts))
        statement=visible_statement(actor,conflicts).where(~hidden)
        if cursor:
            try:statement=statement.where(conflicts.c.id>UUID(cursor))
            except ValueError:raise ApiError("INVALID_CURSOR",422,"Invalid conflict cursor") from None
        rows=self.uow.session.execute(statement.order_by(conflicts.c.id).limit(limit+1)).mappings().all()
        return Page([self.get(actor,r["id"]) for r in rows[:limit]],str(rows[limit-1]["id"]) if len(rows)>limit else None)

    def list_rulings(self,actor,identifier,cursor=None,limit=50):
        self.get(actor,identifier)
        statement=visible_statement(actor,rulings).where(rulings.c.conflict_id==identifier)
        if cursor:
            try:statement=statement.where(rulings.c.id>UUID(cursor))
            except ValueError:raise ApiError("INVALID_CURSOR",422,"Invalid ruling cursor") from None
        rows=self.uow.session.execute(statement.order_by(rulings.c.id).limit(limit+1)).mappings().all()
        return Page([self.ruling(actor,r["id"]) for r in rows[:limit]],str(rows[limit-1]["id"]) if len(rows)>limit else None)

    def accepted_effect(self,actor,identifier):
        view=self.ruling(actor,identifier)
        if view.state!="effective": raise Conflict("RULING_REVIEW_REQUIRED","Ruling is not effective")
        payload=self.uow.session.execute(select(rulings.c.payload).where(rulings.c.id==identifier)).scalar_one()
        effect={k:dict(v) if isinstance(v,dict) else v for k,v in payload["effect"].items()}
        if effect["operation"]=="candidate.reject":raise Conflict("NO_ACCEPTED_EFFECT","Ruling rejected the proposed identity")
        effect["row" if "row" in effect else "create" if "create" in effect else "patch"]["decision_ref"]=facts.json_value(view.revision)
        return effect

    def reevaluate(self,uow,actor,ruling_id,dependencies,has_material_conflict):
        view=self.ruling(actor,ruling_id); lock_ledger(uow)
        authorize(uow,actor,view.conflict_id,"conflict.resolve")
        if view.state=="superseded":return view
        row=uow.session.execute(select(rulings.c.payload).where(rulings.c.id==ruling_id)).scalar_one()
        context=_ruling_context(uow,row)
        try: actual=_relevant(uow,context)
        except (ValueError,KeyError): actual=None
        # Both legacy arguments are advisory only. Persisted authority alone
        # determines relevant equivalence; False cannot suppress a conflict.
        if actual is not None and actual.digest==view.dependencies.digest and view.state=="effective": return view
        if view.state!="needs_review":
            payload=_latest(uow,ruling_id) | {"state":"needs_review"}
            event=append_revision(uow,actor,ruling_id,view.revision.revision,payload,"ingestion.ruling.reevaluated")
            bind_dependencies(uow,ruling_id,definition_source_closure(uow,{view.conflict_id}),event.event_id)
            state=_latest(uow,view.conflict_id)
            if state.get("state")=="resolved" and state.get("ruling_id")==str(ruling_id):
                append_revision(uow,actor,view.conflict_id,head(uow,view.conflict_id)["revision"],
                    {"state":"needs_review","ruling_id":str(ruling_id),"ruling_ref":facts.json_value(event)},"ingestion.conflict.review_required")
        return self.ruling(actor,ruling_id)

    def currentness(self,uow,decision_ref,support_id):
        row=uow.session.execute(select(rulings.c.payload).where(rulings.c.id==decision_ref.resource_id)).scalar_one_or_none()
        if row is None:
            from lzo.auth.models import resource_scopes
            kind=uow.session.execute(select(resource_scopes.c.resource_kind).where(resource_scopes.c.resource_id==decision_ref.resource_id)).scalar_one_or_none()
            if kind=="ingestion.source_evaluation":
                from lzo.ingestion.models import source_evaluations
                value=uow.session.execute(select(source_evaluations.c.payload).where(
                    source_evaluations.c.id==decision_ref.resource_id)).scalar_one_or_none()
                if not value or facts.revision_ref(uow,decision_ref.resource_id)!=decision_ref:
                    return SupportCurrentness(False,"invalid_source_evaluation",None,(decision_ref,))
                try:
                    context=value["context"]
                    actual=_relevant(uow,context)
                    matched=(any(a["support"]["support_id"]==str(support_id) for a in context["facts"])
                        or str(support_id) in context.get("geometry_support_ids",()))
                    valid=matched and actual.digest==context["dependencies"]["digest"]
                except (ValueError,KeyError):
                    return SupportCurrentness(False,"unknown_equivalence",None,(decision_ref,))
                return SupportCurrentness(valid,"unchanged" if valid else "material_invalidation",actual.digest,(decision_ref,))
            if kind=="ingestion.merge":
                from lzo.ingestion.models import merge_proposals
                proposal=uow.session.execute(select(merge_proposals.c.payload).where(merge_proposals.c.id==decision_ref.resource_id)).scalar_one_or_none()
                origin=(proposal or {}).get("plan",{}).get("lineage",{}).get(str(support_id))
                exact=uow.session.execute(select(revisions.c.event_id).where(revisions.c.event_id==decision_ref.event_id,
                    revisions.c.resource_id==decision_ref.resource_id,revisions.c.revision==decision_ref.revision)).first()
                if not origin or not exact:return SupportCurrentness(False,"merge_lineage_unavailable",None,(decision_ref,))
                old=uow.session.execute(select(facts.supports).where(facts.supports.c.id==UUID(origin["support_id"]))).mappings().first()
                from lzo.semantic.merge import owns_origin_withdrawal
                if not old or (not old["active"] and not owns_origin_withdrawal(uow,decision_ref,origin,old)):
                    return SupportCurrentness(False,"original_support_withdrawn",None,(decision_ref,))
                ref=origin["decision_ref"]
                if ref:
                    prior=self.currentness(uow,RevisionRef(UUID(ref["resource_id"]),ref["revision"],UUID(ref["event_id"])),UUID(origin["support_id"]))
                    if prior is not None:return SupportCurrentness(prior.eligible,prior.reason,prior.relevant_digest,(decision_ref,*prior.references))
                return SupportCurrentness(True,"preserved_merge_origin",None,(decision_ref,))
            return SupportCurrentness(False,"unknown_decision",None,()) if kind=="ingestion.ruling" else None
        original=uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==decision_ref.event_id,
            revisions.c.resource_id==decision_ref.resource_id,revisions.c.revision==decision_ref.revision)).scalar_one_or_none()
        if original is None: return SupportCurrentness(False,"invalid_original_pin",None,())
        context=_ruling_context(uow,row)
        chosen=set(row["chosen_candidate_ids"])
        matched=any(a["assertion_id"] in chosen and a["support"]["support_id"]==str(support_id) for a in context["facts"])
        matched=matched or (context.get('geometry_candidate_id') in chosen and str(support_id) in context.get('geometry_support_ids',()))
        try: actual=_relevant(uow,context)
        except (ValueError,KeyError): return SupportCurrentness(False,"unknown_equivalence",None,(decision_ref,))
        valid=matched and _latest(uow,decision_ref.resource_id)["state"]=="effective" and actual.digest==row["dependencies"]["digest"]
        return SupportCurrentness(valid,"unchanged" if valid else "material_invalidation",actual.digest,(decision_ref,))

    def has_invalidated_supports(self,uow):
        # One authoritative selection limits the census to this producer's
        # accepted decisions; ordinary objects are not individually reloaded.
        from sqlalchemy import cast,String
        from lzo.auth.models import resource_scopes
        rows=uow.session.execute(select(facts.assertions.c.payload,facts.supports.c.id).join(
            facts.supports,facts.supports.c.assertion_id==facts.assertions.c.id).join(resource_scopes,
            cast(resource_scopes.c.resource_id,String)==facts.assertions.c.payload["decision_ref"]["resource_id"].astext).where(
            resource_scopes.c.resource_kind.in_(("ingestion.ruling","ingestion.merge","ingestion.source_evaluation")),
            facts.supports.c.active.is_(True),facts.assertions.c.payload["state"].astext=="accepted")).mappings()
        for row in rows:
            raw=row["payload"]["decision_ref"]
            result=self.currentness(uow,RevisionRef(UUID(raw["resource_id"]),raw["revision"],UUID(raw["event_id"])),row["id"])
            if not result.eligible: return True
        from lzo.semantic.models import geometry_supports,type_declarations
        for row in uow.session.execute(select(geometry_supports).where(geometry_supports.c.active.is_(True))).mappings():
            raw=row["payload"].get("decision_ref")
            if not raw:continue
            result=self.currentness(uow,RevisionRef(UUID(raw["resource_id"]),raw["revision"],UUID(raw["event_id"])),row["id"])
            if result is not None and (not result.eligible or not facts.is_effective(row["payload"],facts.evaluation_time(uow))):return True
        for declaration in uow.session.execute(select(type_declarations)).mappings():
            for raw in declaration["dependency_refs"]:
                result=self.type_currentness(uow,RevisionRef(UUID(raw["resource_id"]),raw["revision"],UUID(raw["event_id"])),declaration["resource_id"])
                if result is not None and not result.eligible:return True
        return False


class RulingDecisionPort:
    def verify(self,uow,actor,decision_ref,effect,bundle):
        from lzo.auth.models import resource_scopes
        kind=uow.session.execute(select(resource_scopes.c.resource_kind).where(
            resource_scopes.c.resource_id==decision_ref.resource_id)).scalar_one_or_none()
        if kind=="ingestion.source_evaluation":
            return verify_source_evaluation(uow,actor,decision_ref,effect,bundle)
        service=RulingService(uow); view=service.ruling(actor,decision_ref.resource_id)
        if decision_ref!=view.revision or view.state!="effective": raise Forbidden("ACCEPTED_DECISION_INVALID","Current exact ruling required")
        stored=uow.session.execute(select(rulings.c.payload).where(rulings.c.id==view.id)).scalar_one()
        if stored["bundle_id"]!=str(bundle.id) or service.accepted_effect(actor,view.id)!=effect:
            raise Forbidden("ACCEPTED_DECISION_INVALID","Decision does not bind this exact effect")
        change=ApprovalService(uow).preview(actor,"ingestion.ruling",[view.conflict_id],stored["command"])
        ApprovalService(uow).require_executable(actor,UUID(stored["approval_id"]),change.change_digest,change.baseline)
        pins=tuple(facts.revision_ref(uow,i) for i in definition_source_closure(uow,{view.id}))
        return DecisionEvidence(references=tuple(set(pins)|{decision_ref}))


def _type_context(context):
    return {k:v for k,v in {**context,"facts":[],"geometry":None,"geometry_support_ids":[],"type_only":True}.items()
        if k not in ('effect_contract','withdrawn_predicates','withdrawn_geometry_support_ids','prior_decision_refs','withdrawn_relation')}


def source_evaluation_plan(uow, actor, mapped, profile, bundle, expected_revision):
    """Reconstruct a deterministic source decision; uncertainty is never consent."""
    from lzo.ingestion.identity import registered_change
    from lzo.ingestion.rules import authority_rule
    from lzo.ingestion.models import identity_bindings
    from lzo.semantic.schemas import AssertionInput
    registered_change(uow,actor,mapped.change,profile)
    SourceService(uow).read(actor,profile.source_id,"source.manage")
    verified=MappingService(uow).plan(actor,mapped.change,profile,bundle,mapped.identity)
    if verified!=mapped:raise Conflict("MAPPING_PLAN_MISMATCH","Mapping differs from registered evidence")
    if mapped.identity.resource_id is None:
        raise Conflict("SOURCE_REVIEW_REQUIRED","Complete confirmed identity required")
    target=mapped.identity.resource_id
    existing=uow.session.execute(select(facts.objects).where(facts.objects.c.id==target)).mappings().first()
    if (existing["revision"] if existing else 0)!=expected_revision:
        raise Conflict("REVISION_CONFLICT","Target changed")
    if existing: facts.get_object_row(uow,actor,target,"edit")
    else:facts.require_create_permission(uow,actor,target,mapped.scope.owner_scope_id)
    old=facts.current_assertions(uow,target,eligible_only=False) if existing else []
    removed=[assertion for assertion in old if assertion['payload']['state']=='accepted'
        and mapped.change.operation=='delete' and any(s['payload'].get('source_id')==str(profile.source_id)
            and s['payload'].get('record_key')==mapped.change.key.canonical_key for s in assertion['supports'])]
    binding=uow.session.execute(select(identity_bindings.c.id).where(
        identity_bindings.c.source_id==profile.source_id,identity_bindings.c.canonical_key==mapped.change.key.canonical_key)).scalar_one()
    context={"entity_id":str(target),"change":mapped.change.model_dump(mode="json"),
        "profile":profile.model_dump(mode="json"),"facts":[a.model_dump(mode="json") for a in mapped.facts],
        "expected_revision":expected_revision,"display_name":mapped.display_name,"scope":facts.json_value(mapped.scope),
        "bundle_id":str(bundle.id),"effect_contract":"source-row-v1","geometry":mapped.geometry}
    from lzo.semantic.schemas import SourceSupport,RowGeometry,SourceRowInput
    from lzo.semantic.models import geometry_supports
    from lzo.semantic.source_rows import selected_geometry
    from uuid import NAMESPACE_URL
    geometry=None;withdraw_geometry=[]
    # Current-read eligibility hides superseded raw evidence, but its adopted
    # support/decision remains a predecessor until explicitly retired. Review
    # classification must retain that history independently of current reads.
    geometry_history=list(uow.session.execute(select(geometry_supports).where(
        geometry_supports.c.resource_id==target,geometry_supports.c.active.is_(True))).mappings())
    previous_geometry=[entry["payload"] for entry in geometry_history]
    current_geometry=selected_geometry(uow,target)
    if current_geometry:previous_geometry.append(current_geometry)
    if mapped.geometry is not None:
        rule=authority_rule(uow,actor,"urn:lzo:geometry",bundle)
        if rule is None or rule.owner_policy!="source_backed" or profile.source_id not in rule.owner_source_ids:
            raise Conflict("SOURCE_REVIEW_REQUIRED","Source is not the geometry authority")
        if not profile.source_crs:raise ApiError("GEOMETRY_CRS_REQUIRED",422,"Geometry mapping requires an explicit CRS")
        sid=uuid5(NAMESPACE_URL,"geometry:"+digest({"change":str(mapped.change.change_id),"profile":str(profile.id),"target":str(target)}))
        support=SourceSupport(support_id=sid,source_id=profile.source_id,record_key=mapped.change.key.canonical_key,
            source_version=mapped.change.position.source_version,snapshot=mapped.change.snapshot,
            raw_value={"geometry":mapped.geometry,"source_crs":profile.source_crs},mapping_version_id=profile.id,
            evidence_refs=(facts.revision_ref(uow,mapped.change.change_id),facts.revision_ref(uow,profile.id)),observed_at=mapped.change.observed_at)
        geometry=RowGeometry(geojson=mapped.geometry,source_crs=profile.source_crs,source_support=support,
            valid_from=mapped.change.valid_from or datetime.min.replace(tzinfo=timezone.utc),valid_to=mapped.change.valid_to)
        if any(previous["geojson"]!=geometry.geojson or previous["source_crs"]!=geometry.source_crs
                for previous in previous_geometry):
            raise Conflict("SOURCE_REVIEW_REQUIRED","Conflicting geometry requires review")
    for entry in geometry_history:
        support=entry["payload"]["source_support"]
        if support.get("source_id")==str(profile.source_id) and support.get("record_key")==mapped.change.key.canonical_key:
            if mapped.change.operation=="delete" or profile.geometry_path and mapped.geometry is None:
                withdraw_geometry.append(entry["id"])
    context["geometry_support_ids"]=[str(geometry.source_support.support_id)] if geometry else []
    context['withdrawn_predicates']=sorted({assertion['payload']['predicate_iri'] for assertion in removed})
    context['withdrawn_relation']=any(assertion['payload']['object']['kind']=='iri' for assertion in removed)
    context['withdrawn_geometry_support_ids']=list(map(str,withdraw_geometry))
    original=_relevant(uow,context,original=True);current=_relevant(uow,context)
    if original.digest!=current.digest:raise Conflict("SOURCE_EVIDENCE_SUPERSEDED","Source evidence changed")
    policy=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==bundle.ruling_policy_version_id)).scalar_one()
    predicates={a.predicate_iri for a in mapped.facts}
    predicates.update(context['withdrawn_predicates'])
    if geometry or withdraw_geometry:predicates.add("urn:lzo:geometry")
    for rule in policy["ruling_policies"]:
        if not predicates.intersection(rule["predicate_iris"]):continue
        from lzo.ingestion.schemas import RuleExpr
        if rule["always_review"] or evaluate(RuleExpr.model_validate(rule["expression"]),mapped.change.raw_fields) is not True:
            raise Conflict("SOURCE_REVIEW_REQUIRED","Published policy requires review")
    additions=tuple(a.model_copy(update={"state":"accepted"}) for a in mapped.facts)
    withdrawals=[]
    if existing:
        # Existing values need an explicit comparable source-time justification.
        # A human decision is never replaced by this deterministic branch.
        for assertion in old:
            if assertion["payload"]["state"]!="accepted":continue
            for support in assertion["supports"]:
                own=(support["payload"].get("source_id")==str(profile.source_id) and
                     support["payload"].get("record_key")==mapped.change.key.canonical_key)
                if mapped.change.operation=="delete":
                    if own:withdrawals.append(support["id"])
                    continue
                if assertion["payload"]["predicate_iri"] not in predicates:continue
                rule=authority_rule(uow,actor,assertion["payload"]["predicate_iri"],bundle)
                if not own or not rule or not rule.comparable_time_path:
                    raise Conflict("SOURCE_REVIEW_REQUIRED","Competing or unordered field evidence requires review")
                prior=assertion["payload"].get("decision_ref")
                from lzo.ingestion.models import source_evaluations
                previous=uow.session.execute(select(source_evaluations.c.payload).where(
                    source_evaluations.c.id==UUID(prior["resource_id"]))).scalar_one_or_none() if prior else None
                if previous is None:raise Conflict("SOURCE_REVIEW_REQUIRED","Prior human decision requires review")
                from lzo.ingestion.datetimes import source_datetime
                before=path_value(previous["context"]["change"]["raw_fields"],rule.comparable_time_path)
                after=path_value(mapped.change.raw_fields,rule.comparable_time_path)
                if before is None or after is None or source_datetime(after,profile.source_timezone)<=source_datetime(before,profile.source_timezone):
                    raise Conflict("SOURCE_REVIEW_REQUIRED","A strictly newer comparable source time is required")
                withdrawals.append(support["id"])
        effect={"operation":"facts.patch","patch":FactPatch(resource_id=target,expected_revision=expected_revision,
            additions=additions,withdraw_support_ids=tuple(withdrawals)).model_dump(mode="json")}
    else:
        if mapped.change.operation=="delete":raise Conflict("SOURCE_REVIEW_REQUIRED","No adopted identity to withdraw")
        effect={"operation":"object.create","create":CreateObjectInput(resource_id=target,class_iris=(mapped.class_iri,),
            display_name=mapped.display_name,owner_scope_id=mapped.scope.owner_scope_id,related_scope_ids=mapped.scope.related_scope_ids,
            state="active",data_nature=mapped.change.data_nature,assertions=additions).model_dump(mode="json")}
    refs={profile.source_id,profile.id,mapped.change.change_id,mapped.change.snapshot.version_id,bundle.id,binding}
    prior=[assertion['payload'].get('decision_ref') for assertion in old
        if any(s['id'] in withdrawals for s in assertion['supports'])]
    prior.extend(entry['payload'].get('decision_ref') for entry in uow.session.execute(select(geometry_supports).where(
        geometry_supports.c.id.in_(withdraw_geometry))).mappings())
    prior={digest(value):value for value in prior if value}
    context['prior_decision_refs']=[prior[key] for key in sorted(prior)]
    for value in context['prior_decision_refs']:
        ref=RevisionRef(UUID(value['resource_id']),value['revision'],UUID(value['event_id']))
        facts.verify_reference(uow,actor,ref)
        refs.add(ref.resource_id)
    if existing:refs.add(target)
    context["dependencies"]=original.model_dump(mode="json")
    if not expected_revision:context["type_dependencies"]=_relevant(uow,_type_context(context),original=True).model_dump(mode="json")
    if geometry or withdraw_geometry:
        row=SourceRowInput(create=effect.get("create"),patch=effect.get("patch"),geometry=geometry,
            geometry_action="replace" if geometry else "withdraw",withdraw_geometry_support_ids=tuple(withdraw_geometry))
        effect={"operation":"source.row","row":row.model_dump(mode="json")}
    return context,effect,definition_source_closure(uow,refs)


def persist_source_evaluation(uow,actor,mapped,profile,bundle,expected_revision):
    from lzo.ingestion.models import source_evaluations
    context,effect,refs=source_evaluation_plan(uow,actor,mapped,profile,bundle,expected_revision)
    identifier=uuid4()
    scope=mapped.scope
    add_scoped(uow,actor,identifier,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),"ingestion.source_evaluation","source.manage")
    payload={"contract":"source-evaluation-v1","context":context,"effect":effect,
        "mapped":mapped.model_dump(mode="json"),"actor_id":str(actor.subject_id),"bundle_id":str(bundle.id),
        "reason":"unique_source_identity" if expected_revision==0 else "comparable_source_update",
        "protected_ids":sorted(map(str,refs)),"prior_decision_refs":context['prior_decision_refs']}
    ref=append_revision(uow,actor,identifier,0,payload,"ingestion.source.evaluated")
    bind_dependencies(uow,identifier,refs,ref.event_id)
    uow.session.execute(insert(source_evaluations).values(id=identifier,payload=payload))
    key="row" if effect["operation"]=="source.row" else "create" if effect["operation"]=="object.create" else "patch"
    return ref,{**effect,key:{**effect[key],"decision_ref":facts.json_value(ref)}}


def verify_source_evaluation(uow,actor,ref,effect,bundle):
    from lzo.ingestion.models import source_evaluations
    from lzo.ingestion.schemas import MappedRecord
    authorize(uow,actor,ref.resource_id)
    stored=uow.session.execute(select(source_evaluations.c.payload).where(source_evaluations.c.id==ref.resource_id)).scalar_one_or_none()
    if not stored or facts.revision_ref(uow,ref.resource_id)!=ref or stored["bundle_id"]!=str(bundle.id) or stored["actor_id"]!=str(actor.subject_id):
        raise Forbidden("ACCEPTED_DECISION_INVALID","Exact source evaluation required")
    context,expected,refs=source_evaluation_plan(uow,actor,MappedRecord.model_validate(stored["mapped"]),
        MappingProfile.model_validate(stored["context"]["profile"]),bundle,stored["context"]["expected_revision"])
    key="row" if expected["operation"]=="source.row" else "create" if expected["operation"]=="object.create" else "patch"
    expected={**expected,key:{**expected[key],"decision_ref":facts.json_value(ref)}}
    if context!=stored["context"] or expected!=effect:
        raise Forbidden("ACCEPTED_DECISION_INVALID","Source evaluation effect changed")
    pins=tuple(facts.revision_ref(uow,i) for i in refs|{ref.resource_id})
    return DecisionEvidence(references=pins)


register_policy("ingestion.ruling",_descriptor)
for name in ("ingestion.conflict.created","ingestion.conflict.resolved","ingestion.ruling.resolved","ingestion.ruling.reevaluated",
        "ingestion.conflict.refreshed","ingestion.conflict.review_required","ingestion.ruling.superseded","ingestion.source.evaluated"):
    register_nonsemantic(name,"ingestion-v1")
