"""The single governed configuration publisher; historical impact remains I05.

This slice only publishes before any historical external semantic adoption.
Saving immutable versions or staging bundles cannot change active selection.
"""
from dataclasses import asdict
from uuid import UUID, uuid4
from sqlalchemy import insert, select, update
from lzo.auth.permissions import permission_context
from lzo.contracts import Page, ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.approvals import ApprovalService
from lzo.governance.models import revisions, platform_watermarks
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import ChangeDescriptor, ScopedActionRequirement, register_policy
from lzo.ingestion.models import rule_versions, rule_impacts, mapping_profiles
from lzo.ingestion.schemas import RuleVersion, RuleImpact, ImpactCommand, AlignmentRule
from lzo.ingestion.sources import SourceService, digest
from lzo.ontology.drafts import (add_resource, authorize, bind_dependencies, definition_source_closure,
    global_permission, head, visible_statement)
from lzo.ontology.models import catalog, bundles
from lzo.ontology.releases import current_execution_bundle, create_execution_bundle, get_execution_bundle
from lzo.semantic.facts import revision_ref, json_value
from lzo.semantic.projection import ProjectionService, register_nonsemantic, register_projection


def no_prior_adoption(uow):
    """Authoritative historical census, serialized with every S03 write.

    S03 stores full effect/authorization in every immutable object event. This
    covers withdrawn supports, replaced geometry, inactive objects, unprojected
    writes and source-backed type-only creation. Unknown event versions block.
    Raw uploads and candidates without accepted external effects do not count.
    """
    from lzo.semantic.models import assertions, supports
    lock_ledger(uow)
    for row in uow.session.execute(select(assertions.c.payload, supports.c.payload.label("support")).join(
            supports, supports.c.assertion_id == assertions.c.id)).mappings():
        if row["payload"].get("state") == "accepted" and (
                row["support"].get("source_id") or row["support"].get("mapping_version_id") or row["payload"].get("decision_ref")):
            raise Conflict("HISTORICAL_IMPACT_REQUIRED", "Prior source adoption requires historical impact")
    for row in uow.session.execute(select(revisions.c.event_type, revisions.c.payload).where(
            revisions.c.event_type.in_(("object.created", "object.facts_changed", "object.geometry_changed",
                "object.deactivated", "object.validity_changed")))).mappings():
        payload = row["payload"]
        if "effect" not in payload or "authorization" not in payload:
            raise Conflict("ADOPTION_CENSUS_INCOMPLETE", "Historical semantic provenance is incomplete")
        if payload["authorization"].get("basis") == "accepted_decision":
            raise Conflict("HISTORICAL_IMPACT_REQUIRED", "Prior external decision requires historical impact")
        geometry = payload["effect"].get("geometry", {})
        if geometry.get("source_support", {}).get("source_id") or geometry.get("source_support", {}).get("mapping_version_id"):
            raise Conflict("HISTORICAL_IMPACT_REQUIRED", "Historical source geometry requires impact")


def version_row(uow, identifier, kind=None):
    row = uow.session.execute(select(rule_versions).where(rule_versions.c.id == identifier)).mappings().first()
    if row is None or (kind and row["kind"] != kind):
        raise ApiError("RULE_VERSION_REQUIRED", 422, "Exact immutable rule version required")
    return row


def aggregate(uow, bundle):
    if bundle.mapping_version_id is None:
        raise Conflict("MAPPING_NOT_PUBLISHED", "No source mapping aggregate")
    value = version_row(uow, bundle.mapping_version_id, "aggregate")["payload"]
    if value.get("contract") != "source-profile-aggregate-v1":
        raise Conflict("MAPPING_NOT_PUBLISHED", "Unsupported mapping aggregate")
    return value


def profile_for_bundle(uow, actor, source_id, profile_id, bundle):
    with permission_context(uow):
        if get_execution_bundle(actor, bundle.id) != bundle:
            raise Conflict("BUNDLE_MISMATCH", "Immutable bundle mismatch")
    value = aggregate(uow, bundle)
    member = next((m for m in value["members"] if m["source_id"] == str(source_id)), None)
    if member is None or member["profile_id"] != str(profile_id):
        raise Conflict("MAPPING_NOT_IN_BUNDLE", "Source profile is absent from evaluation aggregate")
    profile = SourceService(uow).get_mapping(actor, source_id, profile_id)
    if profile.digest != member["digest"] or profile.revision != member["revision"]:
        raise Conflict("MAPPING_VERSION_CHANGED", "Immutable profile mismatch")
    return profile


def confirmed_alignment(uow, actor, identifier):
    authorize(uow, actor, identifier)
    row = version_row(uow, identifier, "alignment.member")
    bundle = current_execution_bundle(uow, actor)
    if str(identifier) not in aggregate(uow, bundle)["alignment_ids"]:
        raise Conflict("ALIGNMENT_NOT_PUBLISHED", "Confirmed alignment must be published")
    rule = AlignmentRule.model_validate(row["payload"])
    if not rule.confirmed:
        raise Conflict("ALIGNMENT_NOT_CONFIRMED", "Business identity rule is not confirmed")
    return rule


def authority_rule(uow, actor, predicate, bundle):
    from lzo.ingestion.schemas import AuthorityRule
    authorize(uow, actor, bundle.authority_version_id)
    row = uow.session.execute(select(rule_versions.c.payload).where(
        rule_versions.c.id == bundle.authority_version_id,rule_versions.c.kind=="authority")).scalar_one_or_none()
    if row is None: return None
    matches=[AuthorityRule.model_validate(r) for r in row["authority_rules"] if r["predicate_iri"]==predicate]
    return matches[0] if len(matches)==1 else None


class RuleAuthorityPort:
    def resolve(self,uow,actor,predicate,bundle):
        from lzo.semantic import facts
        from lzo.ontology.schemas import SchemaDocument
        from lzo.ontology.models import releases
        document=SchemaDocument.model_validate(uow.session.execute(select(releases.c.document).where(releases.c.id==bundle.schema_release_id)).scalar_one())
        configured=uow.session.execute(select(rule_versions.c.id).where(rule_versions.c.id==bundle.authority_version_id,
            rule_versions.c.kind=="authority")).first()
        if not configured: return facts.genesis_policy_kind(uow,actor,predicate,document,bundle)
        rule=authority_rule(uow,actor,predicate,bundle)
        prop=next((p for p in document.properties if p.iri==predicate),None)
        if prop is None and predicate!="urn:lzo:geometry" and not any(r.iri==predicate for r in document.relations):
            raise ApiError("FIELD_NOT_WRITABLE",403,"Field is not an ordinary writable definition")
        declared=prop.owner_policy_iri if prop else facts.POLICY+"ontology_owned"
        if prop and declared in (facts.POLICY+"ontology_owned",facts.POLICY+"derived",facts.POLICY+"read_only"):
            return declared
        if rule: return facts.POLICY+rule.owner_policy
        if declared==facts.POLICY+"ontology_owned": return declared
        raise ApiError("AUTHORITY_POLICY_UNAVAILABLE",403,"Published source authority rule is required")


def _descriptor(uow, targets, command):
    if set(command) != {"impact_id"}:
        raise ApiError("INVALID_PUBLICATION_COMMAND", 422, "Exact stored impact required")
    identifier = UUID(command["impact_id"])
    row = uow.session.execute(select(rule_impacts.c.payload).where(rule_impacts.c.id == identifier)).scalar_one_or_none()
    if row is None: raise ApiError("NOT_FOUND", 404, "Impact not found")
    selection = ImpactCommand.model_validate(row["command"])
    selected = uow.session.execute(select(catalog)).mappings().one()
    no_prior_adoption(uow)
    proposed = uow.session.execute(select(bundles).where(bundles.c.id == selection.target_bundle_id)).mappings().one()
    old = uow.session.execute(select(bundles).where(bundles.c.id == selected["active_bundle_id"])).mappings().one()
    if any(proposed[k] != old[k] for k in ("schema_release_id", "shapes_digest", "inference_version_id")):
        raise Conflict("PUBLICATION_CONTEXT_CHANGED", "Schema or inference context changed")
    if proposed["id"] == old["id"]:
        raise Conflict("CONFIGURATION_ALREADY_ACTIVE", "Configuration is already active")
    refs = definition_source_closure(uow, {proposed["id"], old["id"], identifier}) | {selected["resource_id"]}
    source_ids=set(selection.selection.source_ids)
    previous_aggregate=uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==old["mapping_version_id"])).scalar_one_or_none()
    source_ids.update(UUID(m["source_id"]) for m in (previous_aggregate or {}).get("members",()))
    source_ids=tuple(sorted(source_ids,key=str))
    refs.update(source_ids)
    return ChangeDescriptor(kind="ingestion.rules.publish", target_ids=(selected["resource_id"],
        *sorted(refs - {selected["resource_id"]}, key=str)), command=command,
        baseline={str(i):head(uow,i)["revision"] for i in refs},
        impact={"mode":"pre_adoption", "previous_bundle_id":str(old["id"]),
            "target_bundle_id":str(proposed["id"]), "affected_source_ids":list(map(str,source_ids)),
            "configuration":row["configuration"], "historical_adopted_entities":[], "guard":"s03-history-v1"},
        submit_action="schema.edit", review_action="schema.review", execute_action="schema.publish",
        global_security_class="schema", operation_key="ingestion.rules.publish",
        scoped_action_requirements=tuple(ScopedActionRequirement(phase, "source.manage", source_ids)
            for phase in ("execute", "review", "submit")))


class RuleService:
    def __init__(self, uow=None): self.uow = uow

    def create_version(self, uow, actor, version):
        global_permission(uow, actor, "schema.edit")
        lock_ledger(uow)
        if version.status != "draft" or version.scope != ResourceScope(None,None,(),"schema"):
            raise ApiError("INVALID_RULE_VERSION", 422, "New configuration requires a schema draft")
        if version.rules or (version.kind != "mapping" and version.mapping_profile) or (
                version.kind != "alignment" and version.alignment_rules) or (
                version.kind != "authority" and version.authority_rules) or (
                version.kind != "ruling" and (version.ruling_policies or version.import_admission is not None)):
            raise ApiError("INVALID_RULE_VERSION", 422, "Rule payload differs from kind")
        deps = set()
        from lzo.ingestion.mapping import validate_expression,path_value
        from lzo.semantic.rdf import iri
        try:
            for rule in version.alignment_rules:
                iri(rule.class_iri)
                if len(rule.normalizers) not in (0,len(rule.key_paths)):raise ValueError("Normalizer cardinality")
                for path in rule.key_paths:path_value({},path)
                for expression in rule.normalizers:validate_expression(expression)
                if rule.scope.resource_id is not None:raise ValueError("Alignment scope must select an owner")
            for rule in version.authority_rules:
                iri(rule.predicate_iri)
                if rule.comparable_time_path:path_value({},rule.comparable_time_path)
                if len(set(rule.owner_source_ids))!=len(rule.owner_source_ids) or len(set(rule.source_priority))!=len(rule.source_priority):
                    raise ValueError("Duplicate source authority")
                if set(rule.source_priority)-set(rule.owner_source_ids):raise ValueError("Priority source is not an owner")
                if rule.owner_policy=="source_backed" and not rule.owner_source_ids:raise ValueError("Source owner required")
            for rule in version.ruling_policies:
                if not rule.predicate_iris:raise ValueError("Relevant predicate required")
                for predicate in rule.predicate_iris:iri(predicate)
                validate_expression(rule.expression)
        except ValueError:
            raise ApiError("INVALID_RULE_EXPRESSION",422,"Invalid bounded rule, authority, or alignment") from None
        if version.parent_id:
            authorize(uow, actor, version.parent_id)
            version_row(uow, version.parent_id, version.kind)
            deps.add(version.parent_id)
        if version.kind == "mapping":
            if not version.mapping_profile: raise ApiError("INVALID_RULE_VERSION",422,"Mapping profile required")
            profile = SourceService(uow).get_mapping(actor, version.mapping_profile.source_id, version.mapping_profile.id)
            if profile != version.mapping_profile: raise Conflict("MAPPING_VERSION_CHANGED","Mapping differs")
            deps.add(profile.id)
        authority = []
        for rule in version.authority_rules:
            for source in set(rule.owner_source_ids) | set(rule.source_priority):
                SourceService(uow).read(actor, source, "source.manage")
                deps.add(source)
            authority.append(rule.model_copy(update={"digest":digest(rule.model_dump(mode="json",exclude={"digest"}))}))
        if len({r.predicate_iri for r in authority}) != len(authority):
            raise ApiError("DUPLICATE_AUTHORITY_RULE",422,"Duplicate authority predicate")
        alignments = []
        for rule in version.alignment_rules:
            member_id = uuid4()
            content = rule.model_dump(mode="json",exclude={"id","digest"})
            member = rule.model_copy(update={"id":member_id,"digest":digest(content)})
            add_resource(uow, member_id, "schema.alignment")
            append_revision(uow, actor, member_id, 0, member.model_dump(mode="json"), "ingestion.rule.created")
            uow.session.execute(insert(rule_versions).values(id=member_id,kind="alignment.member",payload=member.model_dump(mode="json")))
            alignments.append(member); deps.add(member_id)
        policies = tuple(r.model_copy(update={"digest":digest(r.model_dump(mode="json",exclude={"digest"}))}) for r in version.ruling_policies)
        identifier = uuid4()
        result = version.model_copy(update={"id":identifier,"revision":1,"authority_rules":tuple(authority),
            "alignment_rules":tuple(alignments),"ruling_policies":policies})
        result = result.model_copy(update={"digest":digest(result.model_dump(mode="json",exclude={"id","digest","status"}))})
        add_resource(uow, identifier, "schema.configuration")
        event = append_revision(uow, actor, identifier, 0, result.model_dump(mode="json"), "ingestion.rule.created")
        bind_dependencies(uow,identifier,definition_source_closure(uow,deps),event.event_id)
        uow.session.execute(insert(rule_versions).values(id=identifier,kind=result.kind,payload=result.model_dump(mode="json")))
        return result

    def get(self, actor, identifier):
        authorize(self.uow, actor, identifier)
        value=RuleVersion.model_validate(version_row(self.uow, identifier)["payload"])
        selected=self.uow.session.execute(select(bundles).join(catalog,catalog.c.active_bundle_id==bundles.c.id)).mappings().first()
        if selected:
            active={selected["authority_version_id"],selected["ruling_policy_version_id"],selected["mapping_version_id"]}
            aggregate_row=self.uow.session.execute(select(rule_versions.c.payload).where(rule_versions.c.id==selected["mapping_version_id"])).scalar_one_or_none()
            alignment_ids={UUID(i) for i in (aggregate_row or {}).get("alignment_ids",())}
            if identifier in active or any(r.id in alignment_ids for r in value.alignment_rules):
                value=value.model_copy(update={"status":"published"})
        return value

    def list(self, actor, cursor=None, limit=50):
        statement = visible_statement(actor, rule_versions).where(rule_versions.c.kind.in_(("mapping","alignment","authority","ruling")))
        if cursor:
            try:statement=statement.where(rule_versions.c.id>UUID(cursor))
            except ValueError:raise ApiError("INVALID_CURSOR",422,"Invalid rule cursor") from None
        rows=self.uow.session.execute(statement.order_by(rule_versions.c.id).limit(limit+1)).mappings().all()
        return Page([self.get(actor,r["id"]) for r in rows[:limit]],str(rows[limit-1]["id"]) if len(rows)>limit else None)

    def stage_configuration(self, actor, profile_ids, authority_id, ruling_id):
        uow = self.uow
        global_permission(uow, actor, "schema.publish")
        lock_ledger(uow)
        old=current_execution_bundle(uow,actor)
        for identifier,kind in ((authority_id,"authority"),(ruling_id,"ruling")):
            authorize(uow,actor,identifier); version_row(uow,identifier,kind)
        members=[]; deps={authority_id,ruling_id}; alignments=[]
        for identifier in sorted(set(profile_ids),key=str):
            authorize(uow,actor,identifier)
            row=uow.session.execute(select(mapping_profiles).where(mapping_profiles.c.id==identifier)).mappings().first()
            if row is None:raise ApiError("NOT_FOUND",404,"Mapping profile not found")
            profile=SourceService(uow).get_mapping(actor,row["source_id"],identifier)
            SourceService(uow).read(actor,profile.source_id,"source.manage")
            members.append({"source_id":str(profile.source_id),"profile_id":str(identifier),"revision":profile.revision,
                "digest":profile.digest,"origin":json_value(revision_ref(uow,identifier))})
            deps.update((profile.source_id,identifier))
            if profile.confirmed_identity_rule_id:
                version_row(uow,profile.confirmed_identity_rule_id,"alignment.member")
                deps.add(profile.confirmed_identity_rule_id); alignments.append(str(profile.confirmed_identity_rule_id))
        if not members or len({m["source_id"] for m in members}) != len(members):
            raise ApiError("INVALID_MAPPING_AGGREGATE",422,"One exact profile per source required")
        value={"contract":"source-profile-aggregate-v1","members":sorted(members,key=lambda m:m["source_id"]),"alignment_ids":sorted(set(alignments))}
        identifier=uuid4(); add_resource(uow,identifier,"schema.configuration")
        event=append_revision(uow,actor,identifier,0,value,"ingestion.rule.created")
        bind_dependencies(uow,identifier,definition_source_closure(uow,deps),event.event_id)
        uow.session.execute(insert(rule_versions).values(id=identifier,kind="aggregate",payload=value))
        return create_execution_bundle(uow,actor,old.schema_release_id,identifier,authority_id,ruling_id,
            old.inference_version_id,old.shapes_digest)

    def impact(self, actor, command):
        uow=self.uow
        global_permission(uow,actor,"schema.edit"); lock_ledger(uow); no_prior_adoption(uow)
        with permission_context(uow): target=get_execution_bundle(actor,command.target_bundle_id)
        value=aggregate(uow,target)
        source_ids={UUID(m["source_id"]) for m in value["members"]}
        if command.rollback_of or command.selection.resource_ids or set(command.selection.source_ids)!=source_ids:
            raise ApiError("HISTORICAL_IMPACT_REQUIRED",409,"Early impact requires the complete configuration source selection")
        identifier=uuid4(); add_resource(uow,identifier,"schema.rule_impact")
        payload={"command":command.model_dump(mode="json"),"configuration":value,
            "input_watermark":uow.session.execute(select(platform_watermarks.c.event_position)).scalar_one()}
        event=append_revision(uow,actor,identifier,0,payload,"ingestion.rule.impact")
        bind_dependencies(uow,identifier,definition_source_closure(uow,{target.id}),event.event_id)
        uow.session.execute(insert(rule_impacts).values(id=identifier,payload=payload))
        service=ApprovalService(uow); change=service.preview(actor,"ingestion.rules.publish",[identifier],{"impact_id":str(identifier)})
        approval=service.submit(actor,change.kind,list(change.target_ids),change.change_digest,change.baseline,{"command":change.command})
        return RuleImpact(id=identifier,revision=1,target_bundle=target,selection=command.selection,input_snapshots=(),
            input_watermark=payload["input_watermark"],baseline=change.baseline,differences=(),preserved_ruling_ids=(),
            reevaluate_ruling_ids=(),new_conflict_ids=(),change_digest=change.change_digest,approval_id=approval.id,rollback_of=None)

    def get_impact(self,actor,identifier):
        from lzo.governance.approvals import approvals
        authorize(self.uow,actor,identifier)
        payload=self.uow.session.execute(select(rule_impacts.c.payload).where(rule_impacts.c.id==identifier)).scalar_one_or_none()
        if payload is None:raise ApiError("NOT_FOUND",404,"Impact not found")
        command=ImpactCommand.model_validate(payload["command"])
        approval_id=self.uow.session.execute(select(approvals.c.id).where(approvals.c.kind=="ingestion.rules.publish",
            approvals.c.descriptor["command"]["impact_id"].astext==str(identifier)).order_by(approvals.c.position).limit(1)).scalar_one()
        approval=ApprovalService(self.uow).get(actor,approval_id)
        with permission_context(self.uow):target=get_execution_bundle(actor,command.target_bundle_id)
        return RuleImpact(id=identifier,revision=1,target_bundle=target,selection=command.selection,input_snapshots=(),
            input_watermark=payload["input_watermark"],baseline=approval.baseline,differences=(),preserved_ruling_ids=(),
            reevaluate_ruling_ids=(),new_conflict_ids=(),change_digest=approval.change_digest,approval_id=approval.id,rollback_of=None)

    def publish(self, uow, actor, command):
        authorize(uow,actor,command.rule_version_id)
        version_row(uow,command.rule_version_id)
        if command.expected_revision != 1: raise Conflict("REVISION_CONFLICT","Immutable rule revision differs")
        change=ApprovalService(uow).preview(actor,"ingestion.rules.publish",[command.impact_id],{"impact_id":str(command.impact_id)})
        ApprovalService(uow).require_executable(actor,command.approval_id,change.change_digest,change.baseline)
        selected=uow.session.execute(select(catalog).with_for_update()).mappings().one()
        payload=uow.session.execute(select(rule_impacts.c.payload).where(rule_impacts.c.id==command.impact_id)).scalar_one()
        with permission_context(uow): target=get_execution_bundle(actor,UUID(payload["command"]["target_bundle_id"]))
        if command.rule_version_id not in definition_source_closure(uow,{target.id}):
            raise Conflict("RULE_NOT_IN_BUNDLE","Selected rule does not belong to publication")
        refs=set(change.target_ids)-{selected["resource_id"]}
        event=append_revision(uow,actor,selected["resource_id"],selected["revision"],
            {"bundle_id":str(target.id),"approval_id":str(command.approval_id),"impact_id":str(command.impact_id),
             "dependency_revisions":json_value(tuple(revision_ref(uow,i) for i in sorted(refs,key=str)))},"ingestion.rules.published")
        uow.session.execute(update(catalog).values(revision=event.revision,active_bundle_id=target.id))
        from lzo.ontology.drafts import bind_history_dependencies
        bind_history_dependencies(uow,event.event_id,refs)
        ProjectionService(uow,None).schedule(actor,event)
        return event


register_policy("ingestion.rules.publish",_descriptor)
for name in ("ingestion.rule.created","ingestion.rule.impact"):
    register_nonsemantic(name,"ingestion-v1")
from lzo.semantic.inference import _profile_projection
register_projection("ingestion.rules.published",_profile_projection,version="ingestion-v1",dirty_kind="rules",action="schema.publish")
