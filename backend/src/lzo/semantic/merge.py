"""S03-owned finite composite lifecycle. No caller-selected bypass or callback."""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime,timezone
from uuid import UUID,uuid5
from sqlalchemy import select,text
from lzo.errors import Conflict,ApiError,Forbidden
from lzo.auth.permissions import permission_context
from lzo.ontology.drafts import definition_source_closure,head
from lzo.ontology.releases import current_execution_bundle
from lzo.semantic import facts
from lzo.semantic.schemas import FactPatch,AssertionInput,SourceSupport,DecisionEvidence

_reader=None
_effect=ContextVar("s03_verified_merge_effect",default=None)


def register_plan_reader(reader):
    """One deployment-owned reader for persisted I02 merge proposals."""
    global _reader
    _reader=reader


def build_plan(uow,actor,proposal_id,canonical_id,duplicate_ids):
    duplicates=tuple(sorted(set(duplicate_ids),key=str))
    if not duplicates or canonical_id in duplicates or len(duplicates)!=len(duplicate_ids):
        raise ApiError("INVALID_MERGE_IDENTITIES",422,"Distinct noncanonical duplicates required")
    def load(identifier):
        if actor is not None: return facts.get_object_row(uow,actor,identifier,"edit")
        row=uow.session.execute(select(facts.objects).where(facts.objects.c.id==identifier)).mappings().first()
        if row is None: raise ApiError("NOT_FOUND",404,"Merge resource not found")
        return row
    canonical=load(canonical_id)
    duplicate_rows=[load(i) for i in duplicates]
    if actor is not None: bundle=current_execution_bundle(uow,actor)
    else:
        from lzo.ontology.models import bundles,catalog
        from lzo.contracts import ExecutionBundle
        bundle=ExecutionBundle(**uow.session.execute(select(bundles).join(catalog,catalog.c.active_bundle_id==bundles.c.id)).mappings().one())
    affected={canonical_id,*duplicates}; refs={bundle.id}; blocked=[]; preserved=[]
    if any(r["payload"]["state"]=="inactive" for r in (canonical,*duplicate_rows)):
        blocked.append("inactive_identity")
    for row in duplicate_rows:
        if set(row["payload"]["class_iris"])!=set(canonical["payload"]["class_iris"]): blocked.append("incompatible_type:"+str(row["id"]))
        geometry=uow.session.execute(text("SELECT payload FROM semantic_geometry WHERE resource_id=:id"),{"id":row["id"]}).scalar_one_or_none()
        keep_geometry=uow.session.execute(text("SELECT payload FROM semantic_geometry WHERE resource_id=:id"),{"id":canonical_id}).scalar_one_or_none()
        if geometry and (not keep_geometry or geometry["normalized"]!=keep_geometry["normalized"]):
            blocked.append("unexpressed_geometry:"+str(row["id"]))
        inbound=facts._deactivate_descriptor(uow,[],{"operation":"object.deactivate","resource_id":str(row["id"]),
            "expected_revision":row["revision"],"reason":"merge impact census"}).impact["blocking_inbound_ids"]
        affected.update(UUID(i) for i in inbound)
    rows={r["id"]:r for r in (canonical,*duplicate_rows)}
    for identifier in sorted(affected-set(rows),key=str): rows[identifier]=load(identifier)
    for identifier in affected:
        refs.update(uow.session.execute(select(facts.object_dependencies.c.dependency_id).where(
            facts.object_dependencies.c.resource_id==identifier)).scalars())
    refs=definition_source_closure(uow,refs|affected)
    additions={};withdrawals={};lineage={}
    prospective={identifier:facts.current_assertions(uow,identifier,eligible_only=False)
        for identifier in (canonical_id,*duplicates)}
    def normalized_term(payload):
        term=payload["object"]
        if term["kind"]=="iri" and term["value"] in {facts.object_iri(i) for i in duplicates}:
            return {**term,"value":facts.object_iri(canonical_id)}
        return term
    def overlaps(a,b):
        def instant(value):return datetime.fromisoformat(value.replace("Z","+00:00"))
        return ((not a["valid_to"] or instant(b["valid_from"])<instant(a["valid_to"]))
            and (not b["valid_to"] or instant(a["valid_from"])<instant(b["valid_to"])))
    accepted=[a for values in prospective.values() for a in values if a["payload"]["state"]=="accepted"]
    disputed=set()
    for assertion in accepted:
        data=assertion["payload"]
        if any(other["id"]!=assertion["id"] and other["payload"]["predicate_iri"]==data["predicate_iri"]
                and normalized_term(other["payload"])!=normalized_term(data) and overlaps(data,other["payload"])
                for other in accepted):
            disputed.add(assertion["id"])
    def transfer(assertion,support,target,*,rewrite=False):
        payload={k:v for k,v in assertion["payload"].items() if k!="decision_ref"}
        predicate=payload["predicate_iri"]
        term=payload["object"]
        if term["kind"]=="iri" and term["value"] in {facts.object_iri(i) for i in duplicates}:
            term={**term,"value":facts.object_iri(canonical_id)}
        state=payload["state"]
        if target==canonical_id and not rewrite and state=="accepted" and assertion["id"] in disputed:
            state="candidate";preserved.append(str(assertion["id"]))
        if state=="accepted" and not facts.support_is_current(uow,assertion,support):
            blocked.append("invalid_support:"+str(support["id"]))
        new_assertion=uuid5(proposal_id,"assertion:"+str(assertion["id"])+":"+str(target)+":"+state)
        new_support=uuid5(proposal_id,"support:"+str(support["id"])+":"+str(target))
        source=SourceSupport.model_validate(support["payload"])
        original={"assertion_id":str(assertion["id"]),"support_id":str(support["id"]),"resource_id":str(assertion["resource_id"]),
            "decision_ref":assertion["payload"].get("decision_ref"),"withdrawn_by_merge":rewrite}
        source=source.model_copy(update={"support_id":new_support,"raw_value":{"original_value":source.raw_value,"merge_origin":original}})
        addition=AssertionInput(**{**payload,"assertion_id":new_assertion,"resource_id":target,"state":state,"object":term},support=source)
        additions.setdefault(target,[]).append(addition.model_dump(mode="json"));lineage[str(new_support)]=original
    for duplicate in duplicates:
        for assertion in prospective[duplicate]:
            for support in assertion["supports"]: transfer(assertion,support,canonical_id)
    for identifier in sorted(affected-set(duplicates),key=str):
        for assertion in facts.current_assertions(uow,identifier,eligible_only=False):
            data=assertion["payload"]
            if data["state"]!="accepted" or data["object"]["kind"]!="iri" or data["object"]["value"] not in {facts.object_iri(i) for i in duplicates}: continue
            end=datetime.fromisoformat(data["valid_to"].replace("Z","+00:00")) if data["valid_to"] else None
            if end and end<=facts.evaluation_time(uow): continue
            for support in assertion["supports"]:
                transfer(assertion,support,identifier,rewrite=True)
                withdrawals.setdefault(identifier,[]).append(str(support["id"]))
    patches=[]
    for identifier in sorted(set(additions)|set(withdrawals),key=str):
        patches.append(FactPatch(resource_id=identifier,expected_revision=rows[identifier]["revision"],
            additions=additions.get(identifier,()),withdraw_support_ids=withdrawals.get(identifier,())).model_dump(mode="json"))
    return {"contract":"s03-composite-merge-v1","canonical_id":str(canonical_id),"duplicate_ids":list(map(str,duplicates)),
        "bundle_id":str(bundle.id),"baseline":{str(i):head(uow,i)["revision"] for i in refs},
        "affected_ids":sorted(map(str,affected)),"protected_ids":sorted(map(str,refs)),"patches":patches,"lineage":lineage,
        "blocked_effects":sorted(set(blocked)),"preserved_candidate_assertion_ids":sorted(set(preserved)),
        "canonical_management":{"owner_scope_id":canonical["payload"]["owner_scope_id"],"related_scope_ids":canonical["payload"]["related_scope_ids"],
            "class_iris":canonical["payload"]["class_iris"]},
        "different_original_managers":[str(r["id"]) for r in duplicate_rows if r["payload"]["owner_scope_id"]!=canonical["payload"]["owner_scope_id"]]}


def effect_evidence(uow,actor,effect,bundle):
    verified=_effect.get()
    if verified is None: return None
    session,subject,expected,bundle_id,pins=verified
    if session is not uow.session or actor.subject_id!=subject or effect!=expected or bundle.id!=bundle_id:
        raise Forbidden("COMPOSITE_EFFECT_MISMATCH","Effect is outside verified merge plan")
    return DecisionEvidence(references=pins)


def owns_origin_withdrawal(uow, proposal_ref, origin, support):
    """Only this exact enumerated incoming-edge rewrite explains retirement.
    It does not establish continued source justification for the replacement.
    """
    if not origin.get("withdrawn_by_merge") or str(support["assertion_id"])!=origin["assertion_id"]:
        return False
    def matches(effect):
        patch=effect.get("patch",{})
        return (effect.get("operation")=="facts.patch" and patch.get("decision_ref")==facts.json_value(proposal_ref)
            and patch.get("resource_id")==origin["resource_id"] and origin["support_id"] in patch.get("withdraw_support_ids",[]))
    event_id=support["withdrawn_event_id"]
    if event_id:
        from lzo.governance.models import revisions
        payload=uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==event_id,
            revisions.c.resource_id==UUID(origin["resource_id"]))).scalar_one_or_none()
        return bool(payload and matches(payload.get("effect",{})))
    # S03 marks the old support inactive before record_state assigns its event.
    # Only the private exact effect context for this session covers that instant.
    current=_effect.get()
    return bool(current and current[0] is uow.session and matches(current[2]))


def execute(uow,actor,proposal_ref,approval_id):
    if _reader is None: raise Forbidden("MERGE_PLAN_UNAVAILABLE","Trusted merge plan reader required")
    with permission_context(uow):
        plan,pins=_reader.verify(uow,actor,proposal_ref,approval_id)
        identifiers=[UUID(i) for i in plan["affected_ids"]]
        uow.session.execute(select(facts.objects.c.id).where(facts.objects.c.id.in_(identifiers)).order_by(facts.objects.c.id).with_for_update()).all()
        fresh=build_plan(uow,actor,proposal_ref.resource_id,UUID(plan["canonical_id"]),tuple(UUID(i) for i in plan["duplicate_ids"]))
        if fresh!=plan: raise Conflict("APPROVAL_STALE","Merge plan changed under locks")
        bundle=current_execution_bundle(uow,actor); result=None
        for raw in plan["patches"]:
            patch=FactPatch.model_validate(raw).model_copy(update={"decision_ref":proposal_ref})
            effect={"operation":"facts.patch","patch":patch.model_dump(mode="json")}
            token=_effect.set((uow.session,actor.subject_id,effect,bundle.id,pins))
            try: receipt=facts.apply_fact_patch(uow,actor,patch,bundle)
            finally: _effect.reset(token)
            if str(patch.resource_id)==plan["canonical_id"]: result=receipt.revision
        for duplicate in plan["duplicate_ids"]:
            identifier=UUID(duplicate);row=facts.get_object_row(uow,actor,identifier,"edit")
            inbound=facts._deactivate_descriptor(uow,[],{"operation":"object.deactivate","resource_id":duplicate,
                "expected_revision":row["revision"],"reason":"verified merge retirement"}).impact["blocking_inbound_ids"]
            # Other duplicates are about to retire in this same root effect;
            # their transferred counterparts were already checked and repointed.
            if set(inbound)-set(plan["duplicate_ids"]): raise Conflict("MERGE_INBOUND_REMAINS","Accepted incoming support remains")
            changed=dict(row)|{"payload":{**row["payload"],"state":"inactive"}}
            facts.record_state(uow,actor,changed,row["revision"],bundle,{p.resource_id for p in pins},"object.deactivated",
                {"operation":"identity.merge.retire","proposal_ref":facts.json_value(proposal_ref),"approval_id":str(approval_id)})
        if result is None:
            canonical=facts.get_object_row(uow,actor,UUID(plan["canonical_id"]),"edit")
            result=facts.record_state(uow,actor,canonical,canonical["revision"],bundle,{p.resource_id for p in pins},"object.facts_changed",
                {"operation":"identity.merge.canonical","proposal_ref":facts.json_value(proposal_ref)}).revision
        return result
