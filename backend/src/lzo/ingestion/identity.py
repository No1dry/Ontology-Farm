"""Stable source identity and confirmed shared business keys in the caller UoW."""
from uuid import UUID,uuid4
from sqlalchemy import select, insert
from lzo.contracts import ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.ingestion.models import source_records, identity_bindings, alignment_keys,merge_proposals,identity_aliases
from lzo.ingestion.schemas import SourceChange, IdentityMatch,MergeProposal
from lzo.ingestion.sources import SourceService, add_scoped, digest
from lzo.ingestion.mapping import path_value, evaluate
from lzo.ontology.drafts import authorize, bind_dependencies


def registered_change(uow, actor, change, profile):
    source = SourceService(uow).read(actor, profile.source_id)
    actual = SourceService(uow).get_mapping(actor, profile.source_id, profile.id)
    if actual != profile or change.key.source_id != source.id:
        raise Conflict("SOURCE_MAPPING_MISMATCH", "Source mapping does not match")
    row = uow.session.execute(select(source_records.c.payload).where(source_records.c.id == change.change_id)).scalar_one_or_none()
    if row is None or SourceChange.model_validate(row) != change:
        raise Conflict("SOURCE_CHANGE_UNREGISTERED", "An exact registered source event is required")
    authorize(uow, actor, change.change_id)
    config = source.configuration
    if (change.key.source_system, change.key.source_instance, change.key.collection, change.data_nature) != (
            config.source_system, config.source_instance, config.collection, config.data_nature):
        raise Conflict("SOURCE_CONFIGURATION_MISMATCH", "Source identity does not match")
    return source


class IdentityService:
    def __init__(self, uow=None):
        self.uow = uow

    def resolve(self, uow, actor, change, profile, reserve):
        source = registered_change(uow, actor, change, profile)
        if reserve:
            SourceService(uow).read(actor, source.id, "source.manage")
            lock_ledger(uow)
        statement = select(identity_bindings).where(identity_bindings.c.source_id == source.id,
            identity_bindings.c.canonical_key == change.key.canonical_key)
        row = uow.session.execute(statement).mappings().first()
        if row:
            authorize(uow, actor, row["id"])
            # A reserved ID need not yet be an S03 object. Its binding is its
            # protected provenance until registration creates the target scope.
            from lzo.auth.models import resource_scopes
            target=row["resource_id"]
            if uow.session.execute(select(resource_scopes.c.resource_id).where(resource_scopes.c.resource_id == row["resource_id"])).first():
                authorize(uow, actor, row["resource_id"])
                # S03 creates the protected scope before verifying the complete
                # create effect. A scope alone is not an adopted semantic object.
                from lzo.semantic.models import objects
                if uow.session.execute(select(objects.c.id).where(objects.c.id==target)).first():
                    target=IdentityService(uow).resolve_alias(actor,target)
            return IdentityMatch(state="existing", resource_id=target,
                matched_rule_id=row["payload"].get("rule_id"), binding_revision=1, explanation="Exact source identity")
        values = [path_value(change.raw_fields, p) for p in profile.identity_paths]
        if not values or any(v is None or isinstance(v, (dict, list)) or str(v).strip() == "" for v in values):
            return IdentityMatch(state="candidate", resource_id=None, matched_rule_id=None,
                binding_revision=None, explanation="A complete explicit business identity is required")
        rule_id, shared, refs = None, None, {source.id, profile.id, change.change_id, change.snapshot.version_id}
        if profile.confirmed_identity_rule_id:
            from lzo.ingestion.rules import confirmed_alignment
            rule = confirmed_alignment(uow, actor, profile.confirmed_identity_rule_id)
            if rule.class_iri != profile.class_iri:
                raise Conflict("ALIGNMENT_CLASS_MISMATCH", "Alignment class differs")
            if (rule.scope.security_class!=source.configuration.scope.security_class or
                    rule.scope.owner_scope_id is not None and rule.scope.owner_scope_id!=source.configuration.scope.owner_scope_id):
                raise Conflict("ALIGNMENT_SCOPE_MISMATCH","Confirmed key is outside source alignment scope")
            rule_id = rule.id
            values = [path_value(change.raw_fields, p) for p in rule.key_paths]
            if len(rule.normalizers) not in (0, len(values)):
                raise ApiError("INVALID_ALIGNMENT", 422, "Normalizer cardinality differs")
            if rule.normalizers:
                values = [evaluate(expr, {"value":value}) for expr, value in zip(rule.normalizers, values)]
            if any(v is None or isinstance(v, (dict, list)) or str(v).strip() == "" for v in values):
                return IdentityMatch(state="candidate", resource_id=None, matched_rule_id=rule.id,
                    binding_revision=None, explanation="Confirmed shared key is incomplete")
            shared = digest({"rule":rule.digest, "class":rule.class_iri, "values":values})
            refs.add(rule.id)
        shared_row = (uow.session.execute(select(alignment_keys).where(
            alignment_keys.c.rule_id == rule_id, alignment_keys.c.key_digest == shared)).mappings().first() if shared else None)
        if shared_row:
            authorize(uow, actor, shared_row["binding_id"])
            refs.add(shared_row["binding_id"])
            from lzo.auth.models import resource_scopes
            if uow.session.execute(select(resource_scopes.c.resource_id).where(resource_scopes.c.resource_id==shared_row["resource_id"])).first():
                authorize(uow,actor,shared_row["resource_id"])
        identifier = shared_row["resource_id"] if shared_row else (uuid4() if reserve else None)
        if shared_row and uow.session.execute(select(resource_scopes.c.resource_id).where(resource_scopes.c.resource_id==identifier)).first():
            identifier=IdentityService(uow).resolve_alias(actor,identifier)
        if reserve:
            binding_id = uuid4()
            scope = source.configuration.scope
            add_scoped(uow, actor, binding_id, ResourceScope(None, scope.owner_scope_id,
                scope.related_scope_ids, scope.security_class), "source.identity", "source.manage")
            payload = {"source_key":change.key.model_dump(mode="json"), "resource_id":str(identifier),
                "profile_id":str(profile.id), "rule_id":str(rule_id) if rule_id else None,
                "change_id":str(change.change_id), "shared_key_digest":shared}
            event = append_revision(uow, actor, binding_id, 0, payload, "source.identity.reserved")
            bind_dependencies(uow, binding_id, refs, event.event_id)
            uow.session.execute(insert(identity_bindings).values(id=binding_id, source_id=source.id,
                canonical_key=change.key.canonical_key, resource_id=identifier, payload=payload))
            if shared and not shared_row:
                uow.session.execute(insert(alignment_keys).values(rule_id=rule_id,
                    key_digest=shared, resource_id=identifier, binding_id=binding_id))
        return IdentityMatch(state="existing" if shared_row else "new", resource_id=identifier,
            matched_rule_id=rule_id, binding_revision=1 if reserve else None,
            explanation="Confirmed shared business key" if shared_row else "Distinct complete source identity")

    def preview_merge(self,actor,canonical_id,duplicate_ids):
        from lzo.semantic import facts,merge
        from lzo.governance.approvals import ApprovalService
        from lzo.ontology.drafts import definition_source_closure
        uow=self.uow
        facts.get_object_row(uow,actor,canonical_id,"identity.merge")
        lock_ledger(uow)
        identifier=uuid4()
        plan=merge.build_plan(uow,actor,identifier,canonical_id,duplicate_ids)
        row=facts.get_object_row(uow,actor,canonical_id)
        scope=ResourceScope(None,UUID(row["payload"]["owner_scope_id"]),tuple(UUID(i) for i in row["payload"]["related_scope_ids"]),"data")
        add_scoped(uow,actor,identifier,scope,"ingestion.merge","identity.merge")
        payload={"plan":plan,"initiator_id":str(actor.subject_id),"identity_basis":"independent_explicit_identity_merge"}
        ref=append_revision(uow,actor,identifier,0,payload,"ingestion.merge.proposed")
        bind_dependencies(uow,identifier,{UUID(i) for i in plan["protected_ids"]},ref.event_id)
        uow.session.execute(insert(merge_proposals).values(id=identifier,payload=payload))
        service=ApprovalService(uow);change=service.preview(actor,"ingestion.merge",[identifier],{"proposal_id":str(identifier)})
        approval=service.submit(actor,change.kind,list(change.target_ids),change.change_digest,change.baseline,{"command":change.command})
        scopes=[]
        from lzo.auth.models import resource_scopes
        for target in (canonical_id,*duplicate_ids):
            scoped=uow.session.execute(select(resource_scopes).where(resource_scopes.c.resource_id==target)).mappings().one()
            scopes.append(ResourceScope(target,scoped["owner_scope_id"],tuple(UUID(i) for i in scoped["related_scope_ids"]),scoped["security_class"]))
        return MergeProposal(id=identifier,revision=1,canonical_id=canonical_id,duplicate_ids=duplicate_ids,scopes=tuple(scopes),
            baseline=change.baseline,conflict_ids=(),evidence_refs=tuple(facts.revision_ref(uow,UUID(i)) for i in plan["protected_ids"]),
            change_digest=change.change_digest,approval_id=approval.id,
            preserved_candidate_assertion_ids=tuple(UUID(i) for i in plan["preserved_candidate_assertion_ids"]),
            blocking_reasons=tuple(plan["blocked_effects"]),different_manager_ids=tuple(UUID(i) for i in plan["different_original_managers"]))

    def merge(self,uow,actor,proposal_id,approval_id):
        from lzo.semantic import facts,merge
        from lzo.ontology.drafts import head
        authorize(uow,actor,proposal_id)
        lock_ledger(uow)
        if head(uow,proposal_id)["revision"]!=1: raise Conflict("APPROVAL_STALE","Merge proposal was consumed")
        proposal_ref=facts.revision_ref(uow,proposal_id)
        result=merge.execute(uow,actor,proposal_ref,approval_id)
        stored=uow.session.execute(select(merge_proposals.c.payload).where(merge_proposals.c.id==proposal_id)).scalar_one()
        for duplicate in stored["plan"]["duplicate_ids"]:
            uow.session.execute(insert(identity_aliases).values(duplicate_id=UUID(duplicate),canonical_id=UUID(stored["plan"]["canonical_id"]),
                proposal_id=proposal_id,revision_ref=facts.json_value(result)))
        event=append_revision(uow,actor,proposal_id,1,{"state":"consumed","approval_id":str(approval_id),
            "canonical_revision":facts.json_value(result)},"ingestion.merge.consumed")
        bind_dependencies(uow,proposal_id,{UUID(i) for i in stored["plan"]["protected_ids"]}|{approval_id},event.event_id)
        return result

    def resolve_alias(self,actor,identifier):
        from lzo.semantic import facts
        original=identifier;seen=set()
        while True:
            if identifier in seen: raise Conflict("IDENTITY_ALIAS_CYCLE","Alias chain is invalid")
            seen.add(identifier)
            facts.get_object_row(self.uow,actor,identifier)
            row=self.uow.session.execute(select(identity_aliases).where(identity_aliases.c.duplicate_id==identifier)).mappings().first()
            if row is None: return identifier
            authorize(self.uow,actor,row["proposal_id"])
            identifier=row["canonical_id"]


def merge_descriptor(uow,targets,command):
    from lzo.semantic import merge
    from lzo.governance.risk import ChangeDescriptor,ScopedActionRequirement
    from lzo.ontology.drafts import head
    if set(command)!={"proposal_id"}: raise ApiError("INVALID_MERGE_COMMAND",422,"Stored merge proposal required")
    identifier=UUID(command["proposal_id"])
    row=uow.session.execute(select(merge_proposals.c.payload).where(merge_proposals.c.id==identifier)).scalar_one_or_none()
    if row is None: raise ApiError("NOT_FOUND",404,"Merge proposal not found")
    if head(uow,identifier)["revision"]!=1: raise Conflict("APPROVAL_STALE","Merge proposal was consumed")
    plan=row["plan"]
    current=merge.build_plan(uow,None,identifier,UUID(plan["canonical_id"]),tuple(UUID(i) for i in plan["duplicate_ids"]))
    if current!=plan: raise Conflict("APPROVAL_STALE","Merge content or protected heads changed")
    canonical=UUID(plan["canonical_id"])
    refs={UUID(i) for i in plan["protected_ids"]}|{identifier}
    affected=tuple(sorted((UUID(i) for i in plan["affected_ids"]),key=str))
    return ChangeDescriptor(kind="ingestion.merge",target_ids=(canonical,*sorted(refs-{canonical},key=str)),command=command,
        baseline=plan["baseline"]|{str(identifier):1},impact={"plan":plan,"identity_basis":row["identity_basis"]},
        submit_action="identity.merge",review_action="identity.review",execute_action="identity.merge",
        operation_key="ingestion.merge:"+str(canonical),operation_target_ids=affected,
        unresolved_conflicts=bool(plan["blocked_effects"]),
        scoped_action_requirements=tuple(ScopedActionRequirement(phase,"edit",affected) for phase in ("execute","review","submit")))


class MergePlanReader:
    def verify(self,uow,actor,proposal_ref,approval_id):
        from lzo.governance.approvals import ApprovalService
        from lzo.semantic import facts
        facts.verify_reference(uow,actor,proposal_ref)
        if proposal_ref!=facts.revision_ref(uow,proposal_ref.resource_id) or proposal_ref.revision!=1:
            raise Conflict("APPROVAL_STALE","Exact unconsumed proposal required")
        change=ApprovalService(uow).preview(actor,"ingestion.merge",[proposal_ref.resource_id],{"proposal_id":str(proposal_ref.resource_id)})
        ApprovalService(uow).require_executable(actor,approval_id,change.change_digest,change.baseline)
        plan=change.impact["plan"]
        refs=tuple(facts.revision_ref(uow,i) for i in set(change.target_ids)|{approval_id})
        return plan,refs


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic("source.identity.reserved", "ingestion-v1")
register_nonsemantic("ingestion.merge.proposed","ingestion-v1")
register_nonsemantic("ingestion.merge.consumed","ingestion-v1")
from lzo.governance.risk import register_policy
register_policy("ingestion.merge",merge_descriptor)
