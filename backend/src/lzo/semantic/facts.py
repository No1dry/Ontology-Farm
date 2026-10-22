"""Transactional dynamic facts with exact terms, current authority and one outbox."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID
from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, RDFS, SH

from lzo.artifacts.models import artifact_versions
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import (_current_uow, authorized_statement, permission_context,
    require_permission, validate_actor)
from lzo.contracts import BlobRef, Page, ResourceScope, RevisionRef
from lzo.errors import ApiError, Conflict, Forbidden
from lzo.governance.models import resource_heads, revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.ontology.drafts import (artifact_reference, authorize, bind_history_dependencies,
    definition_source_closure, head, settings_for, store_for)
from lzo.ontology.models import catalog, genesis, installations, releases
from lzo.ontology.releases import get_execution_bundle
from lzo.ontology.schemas import SchemaDocument
from lzo.semantic.models import objects, assertions, supports, object_dependencies, projection_events, type_declarations
from lzo.semantic.projection import ProjectionContent, ProjectionService, register_projection
from lzo.semantic.rdf import RdfTerm, Triple, logical_graph, triple_key
from lzo.semantic.schemas import (AssertionInput, AssertionView, CreateObjectInput, FactPatch,
    LinkView, ObjectView, SourceSupport, WriteReceipt)
from lzo.semantic.semantic_profile import ancestors
from lzo.semantic.semantica_adapter import SemanticaAdapter
from lzo.semantic.files import SemanticExecutionIncomplete, SemanticRecursionIncomplete
from lzo.semantic import fact_validation
from lzo.semantic.validation import ValidationIssue, ValidationReport

POLICY = "urn:lzo:authority:"
_decision_port = None
_authority_port = None
_support_currentness_port = None


def register_decision_port(port):
    """Trusted process startup only; there is no request-selected evaluator."""
    global _decision_port
    _decision_port = port


def register_authority_port(port):
    global _authority_port
    _authority_port = port


def register_support_currentness_port(port):
    """Trusted accepted-support eligibility, separate from new-command approval."""
    global _support_currentness_port
    _support_currentness_port = port


def support_is_current(uow, assertion, support):
    ref = assertion["payload"].get("decision_ref")
    if not ref or _support_currentness_port is None:
        return True
    result = _support_currentness_port.currentness(uow,
        RevisionRef(UUID(ref["resource_id"]),ref["revision"],UUID(ref["event_id"])),support["id"])
    # None is a different registered decision producer's historical namespace;
    # I02 never claims to interpret or admit those decisions.
    return result is None or result.eligible


def object_iri(identifier):
    return f"urn:lzo:object:{identifier}"


def evaluation_time(uow):
    if not hasattr(uow, "facts_as_of"):
        uow.facts_as_of = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    return uow.facts_as_of


def is_effective(payload, as_of):
    start = datetime.fromisoformat(payload["valid_from"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(payload["valid_to"].replace("Z", "+00:00")) if payload["valid_to"] else None
    return start <= as_of and (end is None or as_of < end)


def effective_inputs(values, as_of):
    return tuple(a for a in values if a.state == "accepted" and a.valid_from <= as_of and
                 (a.valid_to is None or as_of < a.valid_to))


def json_value(value):
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(value)


def revision_ref(uow, identifier):
    row = head(uow, identifier)
    return RevisionRef(identifier, row["revision"], row["event_id"])


def verify_reference(uow, actor, ref):
    authorize(uow, actor, ref.resource_id)
    found = uow.session.execute(select(revisions.c.event_type).where(
        revisions.c.resource_id == ref.resource_id, revisions.c.revision == ref.revision,
        revisions.c.event_id == ref.event_id)).first()
    if found is None:
        raise ApiError("INVALID_REVISION_REFERENCE", 422, "Evidence revision does not exist")
    if not hasattr(uow, "facts_explicit_pins"):
        uow.facts_explicit_pins = set()
    uow.facts_explicit_pins.add(ref)
    return found[0]


def visible_objects(actor):
    hidden = exists(select(object_dependencies.c.dependency_id).where(
        object_dependencies.c.resource_id == objects.c.id,
        object_dependencies.c.dependency_id.not_in(authorized_statement(actor, "view"))))
    return select(objects).where(objects.c.id.in_(authorized_statement(actor, "view")), ~hidden)


def get_object_row(uow, actor, identifier, action="view"):
    validate_actor(uow, actor)
    row = uow.session.execute(visible_objects(actor).where(objects.c.id == identifier)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    if not hasattr(uow, "facts_full_ids"):
        uow.facts_full_ids = set()
    uow.facts_full_ids.add(identifier)
    if action != "view":
        require_permission(actor, action, ResourceScope(identifier, None, (), "data"), uow=uow)
    return row


def confirmed_types(uow, actor, identifier):
    """Authorized type evidence only; never exposes full creation/history payload."""
    validate_actor(uow, actor)
    visible = uow.session.execute(select(objects).where(objects.c.id == identifier,
        objects.c.id.in_(authorized_statement(actor, "view")))).mappings().first()
    if visible is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    declaration = uow.session.execute(select(type_declarations).where(
        type_declarations.c.resource_id == identifier)).mappings().first()
    if declaration is None:
        raise ApiError("TYPE_EVIDENCE_UNAVAILABLE", 409, "Confirmed type evidence is unavailable")
    for value in (*declaration["dependency_refs"], declaration["revision_ref"]):
        verify_reference(uow, actor, RevisionRef(UUID(value["resource_id"]), value["revision"], UUID(value["event_id"])))
    if not hasattr(uow, "facts_type_only_ids"):
        uow.facts_type_only_ids = set()
    uow.facts_type_only_ids.add(identifier)
    if not hasattr(uow, "facts_type_read_records"):
        uow.facts_type_read_records = {}
    uow.facts_type_read_records[str(identifier)] = json_value(dict(declaration))
    lifecycle=visible["payload"]["state"]
    if _support_currentness_port is not None and hasattr(_support_currentness_port,"type_currentness"):
        for raw in declaration["dependency_refs"]:
            result=_support_currentness_port.type_currentness(uow,
                RevisionRef(UUID(raw["resource_id"]),raw["revision"],UUID(raw["event_id"])),identifier)
            if result is not None and not result.eligible:lifecycle="inactive"
    return declaration["class_iris"], lifecycle, {
        UUID(v["resource_id"]) for v in declaration["dependency_refs"]} | {identifier}


def effective_bundle(uow, actor, bundle):
    """Always compare caller's entire bundle with stored active publication."""
    with permission_context(uow):
        actual = get_execution_bundle(actor, bundle.id)
    if actual != bundle:
        raise Conflict("BUNDLE_MISMATCH", "Bundle differs from immutable snapshot")
    lock_ledger(uow)
    from lzo.ontology.releases import current_execution_bundle
    if current_execution_bundle(uow, actor) != bundle:
        raise Conflict("BUNDLE_NOT_ACTIVE", "Writes require the current execution bundle")
    active = uow.session.execute(select(catalog.c.active_release_id)).scalar_one_or_none()
    state = uow.session.execute(select(installations.c.state).where(
        installations.c.release_id == bundle.schema_release_id)).scalar_one_or_none()
    if active != bundle.schema_release_id or state != "published":
        raise Conflict("BUNDLE_NOT_ACTIVE", "Writes require the active published snapshot")
    for identifier in (bundle.authority_version_id, bundle.ruling_policy_version_id,
                       bundle.inference_version_id, bundle.mapping_version_id):
        if identifier is not None:
            authorize(uow, actor, identifier)
            if head(uow, identifier)["revision"] != 1:
                raise Conflict("BUNDLE_VERSION_MUTABLE", "Bundle configuration changed")
    release = uow.session.execute(select(releases).where(
        releases.c.id == bundle.schema_release_id)).mappings().one()
    return SchemaDocument.model_validate(release["document"])


def current_classes(uow,actor,identifier):
    """Eligible class roots, separate from durable identity and declaration history."""
    classes,lifecycle,_=confirmed_types(uow,actor,identifier)
    return tuple(classes) if lifecycle!='inactive' else ()


def bundle_shapes(uow, actor, bundle):
    row = uow.session.execute(select(artifact_versions).where(
        artifact_versions.c.resource_id == bundle.schema_release_id,
        artifact_versions.c.filename == "shapes.ttl",
        artifact_versions.c.sha256 == bundle.shapes_digest)).mappings().one()
    blob = BlobRef(row["sha256"], row["size_bytes"], row["media_type"], row["id"])
    artifact_reference(uow, actor, blob)
    with store_for(uow).open(blob) as stream:
        content = stream.read()
    if hashlib.sha256(content).hexdigest() != bundle.shapes_digest:
        raise Conflict("SHAPES_DIGEST_MISMATCH", "Published shape bytes changed")
    return content.decode("utf-8")


def policy_kind(uow, actor, predicate, document, bundle):
    if _authority_port is not None:
        kind = _authority_port.resolve(uow, actor, predicate, bundle)
    else:
        kind = genesis_policy_kind(uow, actor, predicate, document, bundle)
    if kind not in {POLICY + k for k in ("ontology_owned", "source_backed", "derived", "read_only")}:
        raise Forbidden("AUTHORITY_POLICY_UNAVAILABLE", "Unknown authority policy")
    return kind


def genesis_policy_kind(uow, actor, predicate, document, bundle):
        default_id = uow.session.execute(select(genesis.c.version_id).where(
            genesis.c.kind == "authority")).scalar_one_or_none()
        config = uow.session.execute(select(revisions.c.payload).where(
            revisions.c.resource_id == bundle.authority_version_id,
            revisions.c.revision == 1)).scalar_one_or_none()
        if default_id != bundle.authority_version_id or not config or config.get("platform_owned") != "authorized_manual":
            raise Forbidden("AUTHORITY_POLICY_UNAVAILABLE", "No trusted authority resolver")
        prop = next((p for p in document.properties if p.iri == predicate), None)
        if prop is not None:
            kind = prop.owner_policy_iri
        elif predicate == "urn:lzo:geometry" or any(r.iri == predicate for r in document.relations):
            kind = POLICY + "ontology_owned"
        else:
            raise Forbidden("FIELD_NOT_WRITABLE", "Field is not an ordinary writable definition")
        return kind


def support_references(uow, actor, support):
    refs = set()
    for identifier in (support.source_id, support.mapping_version_id):
        if identifier:
            authorize(uow, actor, identifier)
            refs.add(identifier)
    for ref in support.evidence_refs:
        event_type = verify_reference(uow, actor, ref)
        refs.add(ref.resource_id)
        if event_type == 'telemetry.batch.accepted':
            from lzo.telemetry.service import TelemetryService
            # Current linked authority follows the selected acceptance event,
            # not whichever audit revision happens to be its resource head.
            refs.update(TelemetryService(uow).reference_dependencies(actor, ref))
    if support.snapshot:
        refs.update((support.snapshot.version_id, artifact_reference(uow, actor, support.snapshot)))
    if not hasattr(uow, "facts_full_ids"):
        uow.facts_full_ids = set()
    uow.facts_full_ids.update(refs)
    return refs


def authorize_effect(uow, actor, document, bundle, effect, additions=(), withdrawals=(),
                     geometry_support=None, decision_ref=None):
    from lzo.semantic.source_rows import complete_effect
    effect=complete_effect(uow,effect)
    refs, external = set(), False
    records = [(a.predicate_iri, a.support, a.state) for a in additions]
    records += [(a["predicate_iri"], SourceSupport.model_validate(s), a["state"])
                for a, s in withdrawals]
    if geometry_support is not None:
        records.append(("urn:lzo:geometry", geometry_support, "accepted"))
    row_context=getattr(uow,"_semantic_source_row",None)
    if row_context:
        if row_context["row"].geometry:
            records.append(("urn:lzo:geometry",row_context["row"].geometry.source_support,"accepted"))
        records.extend(("urn:lzo:geometry",SourceSupport.model_validate(w["payload"]["source_support"]),"accepted")
            for w in row_context["withdrawal"])
    for predicate, support, state in records:
        refs.update(support_references(uow, actor, support))
        kind = policy_kind(uow, actor, predicate, document, bundle)
        if kind in (POLICY+"derived", POLICY+"read_only"):
            raise Forbidden("FIELD_NOT_WRITABLE", "Field is controlled by another producer")
        if state != "accepted":
            continue
        sourced = support.source_id is not None or support.mapping_version_id is not None
        if not sourced and kind != POLICY+"ontology_owned":
            raise Forbidden("FIELD_NOT_WRITABLE", "Field is not platform owned")
        external |= sourced
    if external or decision_ref is not None:
        if decision_ref is None or _decision_port is None:
            raise Forbidden("ACCEPTED_DECISION_REQUIRED", "Trusted acceptance decision required")
        verify_reference(uow, actor, decision_ref)
        from lzo.semantic.merge import effect_evidence
        result = effect_evidence(uow, actor, effect, bundle)
        if result is None:
            result = _decision_port.verify(uow, actor, decision_ref, effect, bundle)
        if not result.references or decision_ref not in result.references:
            raise Forbidden("ACCEPTED_DECISION_INVALID", "Incomplete decision evidence")
        for ref in result.references:
            verify_reference(uow, actor, ref)
            refs.add(ref.resource_id)
        type_refs = result.type_references if result.type_references is not None else result.references
        if not set(type_refs) <= set(result.references):
            raise Forbidden("ACCEPTED_DECISION_INVALID", "Type evidence is outside verified decision evidence")
        uow.facts_type_decision_pins = type_refs
    return refs


def current_assertions(uow, identifier, *, eligible_only=True):
    rows = uow.session.execute(select(assertions).where(assertions.c.resource_id == identifier)
                               .order_by(assertions.c.id)).mappings().all()
    result = []
    for row in rows:
        active = uow.session.execute(select(supports).where(supports.c.assertion_id == row["id"],
            supports.c.active.is_(True)).order_by(supports.c.id)).mappings().all()
        if eligible_only and row["payload"]["state"]=="accepted":
            active=[s for s in active if support_is_current(uow,row,s)]
        if active:
            result.append(dict(row) | {"supports": [dict(s) for s in active]})
    return result


def assertion_inputs(rows):
    return tuple(AssertionInput(**{k: v for k, v in row["payload"].items() if k != "decision_ref"}, support=s["payload"])
                 for row in rows for s in row["supports"])


def check_graph(uow, actor, identifier, classes, values, document, bundle, *,
                as_of=None, changed=(), changed_predicates=(), permit_incomplete=False):
    as_of = as_of or evaluation_time(uow)
    # Type-only evidence is per invocation, not inherited from a preceding
    # accepted, candidate or future-window validation in this command.
    uow.facts_type_read_records = {}
    declared = {c.iri for c in document.classes}
    if not set(classes) <= declared:
        raise ApiError("UNKNOWN_CLASS", 422, "Object class is not published")
    graph = logical_graph(tuple(Triple(object_iri(identifier), str(RDF.type), RdfTerm("iri", c))
                                for c in classes))
    row_context=getattr(uow,"_semantic_source_row",None)
    if row_context and row_context["identifier"]==identifier:
        geometry=row_context["replacement"] or row_context["selection"]
        if geometry and ("valid_from" not in geometry or is_effective(geometry,as_of)):
            graph.add((URIRef(object_iri(identifier)),URIRef("urn:lzo:geometry"),
                URIRef(f"urn:lzo:geometry:{identifier}:{row_context['expected']+1}")))
    for cls in document.classes:
        for parent in cls.parents:
            graph.add((URIRef(cls.iri), RDFS.subClassOf, URIRef(parent)))
    closure = set(classes)
    for cls in classes:
        closure.update(ancestors(document, cls))
    relations = {r.iri: r for r in document.relations}
    properties = {p.iri: p for p in document.properties}
    issues, refs = [], set()
    for fact in values:
        if fact.resource_id != identifier:
            raise ApiError("SUBJECT_MISMATCH", 422, "Assertion subject differs")
        relation = relations.get(fact.predicate_iri)
        prop = properties.get(fact.predicate_iri)
        if relation is None and prop is None:
            raise ApiError("UNKNOWN_PREDICATE", 422, "Predicate is not published")
        incompatible = False
        if relation:
            incompatible = fact.object.kind != "iri"
            target = None
            if not incompatible:
                target = (identifier if fact.object.value == object_iri(identifier) else
                    uow.session.execute(select(objects.c.id).where(objects.c.iri == fact.object.value)).scalar_one_or_none())
                if target == identifier:
                    target_classes = closure
                elif target:
                    classes, state, type_refs = confirmed_types(uow, actor, target)
                    if state == "inactive" and fact.state == "accepted":
                        raise Conflict("LINK_TARGET_INACTIVE", "Relationship target is inactive")
                    refs.update(type_refs)
                    target_classes = set(classes)
                    for cls in target_classes:
                        graph.add((URIRef(fact.object.value), RDF.type, URIRef(cls)))
                    for cls in tuple(target_classes):
                        target_classes.update(ancestors(document, cls))
                else:
                    raise ApiError("UNREGISTERED_IRI", 422, "Relationship target is not registered")
                incompatible = (not set(relation.domain_iris) <= closure or
                                not set(relation.range_iris) <= target_classes)
        elif fact.object.kind != "literal":
            # Published enum terms are the explicit supported vocabulary.
            if fact.object not in prop.enum_values:
                raise ApiError("UNREGISTERED_IRI", 422, "IRI is not a supported vocabulary value")
        if prop and ((fact.object.kind == "literal" and (str(RDF.langString) if fact.object.language else fact.object.datatype_iri) != prop.datatype_iri)
                     or (prop.enum_values and fact.object not in prop.enum_values)):
            issues.append(ValidationIssue(object_iri(identifier), fact.predicate_iri, None,
                str(SH.DatatypeConstraintComponent), "Violation", "Value violates its published property definition",
                fact.object))
        if incompatible:
            issues.append(ValidationIssue(object_iri(identifier), fact.predicate_iri, None,
                "urn:lzo:constraint:link-endpoint", "Violation", "Relationship endpoints are incompatible",
                fact.object))
        graph.add(Triple(object_iri(identifier), fact.predicate_iri, fact.object).nodes())
    shape_text = bundle_shapes(uow, actor, bundle)
    try:
        context, focus, selection, evidence = fact_validation.prepare(
            uow, actor, identifier, graph, shape_text, bundle, values, as_of,
            changed_predicates, changed)
    except ApiError:
        if not permit_incomplete:
            raise
        return None, refs
    refs.update(context.business_refs)
    try:
        report = SemanticaAdapter().validate(graph.serialize(format="turtle"), shape_text, bundle,
                                            focus_iris=focus, root_selection=selection,
                                            finite_evaluation=True)
    except SemanticExecutionIncomplete as error:
        if permit_incomplete:
            return None, refs
        code = ("VALIDATION_CONTEXT_INCOMPLETE" if isinstance(error, SemanticRecursionIncomplete)
                else "VALIDATION_EXECUTION_INCOMPLETE")
        raise ApiError(code, 422,
                       "Validation execution could not complete") from None
    all_issues = (*issues, *report.violations)
    result = ValidationReport(not all_issues, all_issues, report.report_artifact, bundle.id,
        report.focus_iris, report.invocation_scope, report.original_shapes_digest,
        report.root_selection, as_of)
    if not hasattr(uow, "facts_validation_results"):
        uow.facts_validation_results = {}
        uow.facts_validation_extra_refs = set()
        uow.facts_validation_business_refs = set()
    uow.facts_validation_results[id(result)] = {
        **evidence, "reference_ids": sorted(map(str, refs | context.reverse_refs | {bundle.id}))}
    uow.facts_validation_extra_refs.update(context.reverse_refs - context.business_refs)
    uow.facts_validation_business_refs.update(refs)
    return result, refs


def enforce_report(report, state):
    failures = [issue for issue in report.violations if
        state == "active" or issue.constraint_iri != str(SH.MinCountConstraintComponent)]
    if failures:
        code = "LINK_ENDPOINT_INCOMPATIBLE" if any(i.constraint_iri ==
            "urn:lzo:constraint:link-endpoint" for i in failures) else "FACT_VALIDATION_FAILED"
        raise ApiError(code, 422, "Facts do not conform to published constraints")


def validate_object(actor, create, bundle):
    uow = _current_uow.get()
    document = effective_bundle(uow, actor, bundle)
    for fact in create.assertions:
        support_references(uow, actor, fact.support)
    return check_graph(uow, actor, create.resource_id, create.class_iris,
                       create.assertions, document, bundle, changed=create.assertions,
                       changed_predicates={a.predicate_iri for a in create.assertions} | {str(RDF.type)})[0]


def validate_facts(actor, facts, bundle):
    uow = _current_uow.get()
    document = effective_bundle(uow, actor, bundle)
    for fact in facts:
        support_references(uow, actor, fact.support)
    issues, focus, unrestricted, selection = [], set(), set(), {}
    as_of = evaluation_time(uow)
    for identifier in sorted({a.resource_id for a in facts}, key=str):
        row = get_object_row(uow, actor, identifier)
        additions = tuple(a for a in facts if a.resource_id == identifier)
        combined = (*effective_inputs(assertion_inputs(current_assertions(uow, identifier)), evaluation_time(uow)),
                    *additions)
        report, _ = check_graph(uow, actor, identifier, current_classes(uow,actor,identifier), combined, document, bundle,
            changed=additions, changed_predicates={a.predicate_iri for a in additions})
        if (report.invocation_scope != "focused" or report.original_shapes_digest != bundle.shapes_digest
                or report.evaluation_time != as_of):
            raise RuntimeError("Inconsistent focused validation basis")
        issues.extend(report.violations)
        focus.update(report.focus_iris)
        chosen = report.root_selection or {}
        unrestricted.update(set(report.focus_iris) - set(chosen))
        for node, roots in chosen.items():
            selection.setdefault(node, set()).update(roots)
    selected = {node: tuple(sorted(roots)) for node, roots in sorted(selection.items())
                if node not in unrestricted}
    return ValidationReport(not issues, tuple(issues), None, bundle.id,
        tuple(sorted(focus)), "focused", bundle.shapes_digest, selected or None, as_of)


def _pins(uow, actor, identifier, refs):
    previous = uow.session.execute(select(revisions.c.payload).join(resource_heads,
        resource_heads.c.event_id == revisions.c.event_id).where(resource_heads.c.resource_id == identifier)
        ).scalar_one_or_none()
    old_refs = {UUID(p["resource_id"]) for p in previous.get("dependency_revisions", ())} if previous else set()
    type_only = getattr(uow, "facts_type_only_ids", set()) - getattr(uow, "facts_full_ids", set())
    new = refs - type_only - old_refs - {identifier}
    # Object business dependencies are already flattened. Their historical
    # validation-only reports must not become somebody else's fact provenance.
    object_ids = set(uow.session.execute(select(objects.c.id).where(
        objects.c.id.in_(refs-type_only-{identifier}))).scalars())
    object_refs = set(uow.session.execute(select(object_dependencies.c.dependency_id).where(
        object_dependencies.c.resource_id.in_(object_ids))).scalars())
    refs = (definition_source_closure(uow, new-object_ids) | object_refs | refs) - {identifier}
    for ref in refs:
        authorize(uow, actor, ref)
    pinned = {ref for ref in getattr(uow, "facts_explicit_pins", ()) if ref.resource_id in refs}
    if previous:
        pinned.update(RevisionRef(UUID(p["resource_id"]), p["revision"], UUID(p["event_id"]))
                      for p in previous.get("dependency_revisions", ()) if UUID(p["resource_id"]) in refs)
    covered = {ref.resource_id for ref in pinned}
    pinned.update(revision_ref(uow, ref) for ref in refs-covered)
    return tuple(sorted(pinned, key=lambda p: (str(p.resource_id), p.revision)))


def _snapshot(uow, row, rows, bundle, pins, geometry=None,*,classes):
    accepted, candidate, provenance = [], [], []
    if row["payload"]["state"] != "inactive":
        accepted.extend(Triple(row["iri"], str(RDF.type), RdfTerm("iri", c))
                        for c in classes)
        for assertion in rows:
            data = assertion["payload"]
            triple = Triple(row["iri"], data["predicate_iri"], RdfTerm(**data["object"]))
            if data["state"] == "candidate":
                candidate.append(triple)
            elif is_effective(data, evaluation_time(uow)):
                accepted.append(triple)
            assertion_iri = f"urn:lzo:assertion:{assertion['id']}"
            for support in assertion["supports"]:
                provenance.append(Triple(assertion_iri, "urn:lzo:support",
                    RdfTerm("iri", f"urn:lzo:support:{support['id']}")))
        if geometry is not None:
            accepted.append(Triple(row["iri"], "urn:lzo:geometry",
                RdfTerm("iri", f"urn:lzo:geometry:{row['id']}:{geometry['revision']}")))
    return {"object": row["payload"], "assertions": json_value(rows), "bundle_id": str(bundle.id),
        "graphs": {name: [asdict(t) for t in values] for name, values in
                   (("accepted", accepted), ("candidate", candidate), ("provenance", provenance))},
        "dependency_revisions": json_value(pins), "geometry": geometry}


def _projection(uow, row):
    payload = row["payload"]
    pins = tuple(RevisionRef(UUID(r["resource_id"]), r["revision"], UUID(r["event_id"]))
                 for r in payload["dependency_revisions"])
    return ProjectionContent({role: tuple(Triple(t["subject_iri"], t["predicate_iri"],
        RdfTerm(**t["object"])) for t in triples) for role, triples in payload["graphs"].items()},
        (row["resource_id"], *dict.fromkeys(p.resource_id for p in pins)), pins)


def record_state(uow, actor, row, expected, bundle, refs, event_type, effect, reports=None, geometry=None,
                 candidate_report=None, permit_incomplete=False):
    from sqlalchemy import text
    # Preserve previous geometry through ordinary fact patches.
    row_context=getattr(uow,"_semantic_source_row",None)
    if row_context:
        from lzo.semantic.source_rows import complete_effect
        effect=complete_effect(uow,effect)
        geometry=row_context["selection"]
        if geometry is not None:geometry={**geometry,"revision":expected+1}
    elif geometry is None:
        from lzo.semantic.source_rows import selected_geometry
        geometry=selected_geometry(uow,row["id"])
    rows = current_assertions(uow, row["id"])
    # The first snapshot precedes declaration insertion, after create/decision
    # validation. Every later snapshot reads current authorized type evidence.
    classes=tuple(row['payload']['class_iris']) if expected==0 and event_type=='object.created' else current_classes(uow,actor,row['id'])
    if expected==0 and event_type=='object.created' and _support_currentness_port is not None and hasattr(_support_currentness_port,'type_currentness'):
        for pin in getattr(uow,'facts_type_decision_pins',()):
            eligibility=_support_currentness_port.type_currentness(uow,pin,row['id'])
            if eligibility is not None and not eligibility.eligible:classes=()
    if candidate_report is None and any(a["payload"]["state"] == "candidate" for a in rows):
        document = effective_bundle(uow, actor, bundle)
        values = assertion_inputs(rows)
        candidate_report, candidate_refs = check_graph(uow, actor, row["id"], classes,
            (*effective_inputs(values, evaluation_time(uow)), *(a for a in values if a.state == "candidate")),
            document, bundle, changed=tuple(a for a in values if a.state == "candidate"),
            permit_incomplete=permit_incomplete or reports is None)
        refs.update(candidate_refs)
    refs.update(getattr(uow, "facts_validation_business_refs", set()))
    refs.update(uow.session.execute(select(object_dependencies.c.dependency_id).where(
        object_dependencies.c.resource_id == row["id"])).scalars())
    refs.add(bundle.id)
    business_pins = _pins(uow, actor, row["id"], refs)
    pins = _pins(uow, actor, row["id"], refs | getattr(uow, "facts_validation_extra_refs", set()))
    payload = _snapshot(uow, row, rows, bundle, pins, geometry,classes=classes)
    boundaries = [value for a in assertion_inputs(rows) if a.state == "accepted"
        for value in (a.valid_from, a.valid_to) if value is not None and value > evaluation_time(uow)]
    from lzo.semantic.models import geometry_supports
    geometry_rows=list(uow.session.execute(select(geometry_supports.c.payload).where(
        geometry_supports.c.resource_id==row['id'],geometry_supports.c.active.is_(True),
        geometry_supports.c.id.not_in(row_context['row'].withdraw_geometry_support_ids if row_context else ()))) .scalars())
    if row_context and row_context['replacement']:geometry_rows.append(row_context['replacement'])
    boundaries.extend(datetime.fromisoformat(value) for item in geometry_rows
        for value in (item.get('valid_from'),item.get('valid_to'))
        if value is not None and datetime.fromisoformat(value)>evaluation_time(uow))
    next_at = min(boundaries) if boundaries and row["payload"]["state"] != "inactive" else None
    command = effect.get("row",effect.get("create", effect.get("patch", effect.get("geometry", {}))))
    basis = ("temporal_materialization" if event_type == "object.validity_changed" else
             "independent_approval" if event_type == "object.deactivated" else
             "accepted_decision" if command.get("decision_ref") else "authorized_manual")
    payload.update(effect=effect, validation=json_value(reports), candidate_validation=json_value(candidate_report),
                   evaluation_time=evaluation_time(uow).isoformat(), next_transition_at=json_value(next_at),
                   authorization={"actor_id": str(actor.subject_id), "action": "edit", "basis": basis})
    for name, report in (("validation_evidence", reports), ("candidate_validation_evidence", candidate_report)):
        evidence = getattr(uow, "facts_validation_results", {}).get(id(report))
        if evidence is not None:
            evidence_pins = _pins(uow, actor, row["id"], {UUID(value) for value in evidence["reference_ids"]})
            evidence = {**evidence, "reference_ids": sorted({str(pin.resource_id) for pin in evidence_pins})}
            evidence["incoming_stamp"] = fact_validation.incoming_stamp(
                uow, row["id"], evidence["target_predicates"], evaluation_time(uow))
        payload[name] = evidence
    ref = append_revision(uow, actor, row["id"], expected, payload, event_type)
    from lzo.gis.service import schedule_geometry
    schedule_geometry(uow, ref, payload)
    uow.session.execute(update(objects).where(objects.c.id == row["id"]).values(
        revision=ref.revision, payload=row["payload"], bundle_id=bundle.id,
        next_transition_at=next_at, transition_origin_event_id=ref.event_id))
    for pin in business_pins:
        uow.session.execute(pg_insert(object_dependencies).values(
            resource_id=row["id"], dependency_id=pin.resource_id).on_conflict_do_nothing())
    ProjectionService(uow, None).schedule(actor, ref)
    if next_at is not None:
        from lzo.worker.service import enqueue_at
        enqueue_at(uow, actor, "facts.validity", {"resource_id": str(row["id"]),
            "origin_event_id": str(ref.event_id), "due_at": next_at.isoformat(),
            "reference_ids": [str(row["id"]), *dict.fromkeys(str(p.resource_id) for p in pins)]}, bundle.id, next_at)
    return WriteReceipt(revision=ref, assertion_ids=tuple(a["id"] for a in rows), projection_state="pending")


def _insert_assertions(uow, additions, bundle, ref, decision_ref):
    for fact in additions:
        payload = fact.model_dump(mode="json", exclude={"support"})
        payload["decision_ref"] = json_value(decision_ref)
        old = uow.session.execute(select(assertions).where(assertions.c.id == fact.assertion_id)).mappings().first()
        if old:
            compare = {k: v for k, v in old["payload"].items() if k != "decision_ref"}
            if compare != {k: v for k, v in payload.items() if k != "decision_ref"}:
                raise Conflict("ASSERTION_ID_CONFLICT", "Assertion identity already has another conclusion")
        else:
            if (uow.session.execute(select(resource_heads.c.resource_id).where(
                    resource_heads.c.resource_id == fact.assertion_id)).first() or
                    uow.session.execute(select(supports.c.id).where(supports.c.id == fact.assertion_id)).first()):
                raise Conflict("ASSERTION_ID_CONFLICT", "Identity belongs to another resource kind")
            uow.session.execute(insert(assertions).values(id=fact.assertion_id,
                resource_id=fact.resource_id, payload=payload, revision_ref=json_value(ref), bundle_id=bundle.id))
        if (uow.session.execute(select(supports.c.id).where(supports.c.id == fact.support.support_id)).first()
                or uow.session.execute(select(assertions.c.id).where(assertions.c.id == fact.support.support_id)).first()
                or uow.session.execute(select(resource_heads.c.resource_id).where(
                    resource_heads.c.resource_id == fact.support.support_id)).first()):
            raise Conflict("SUPPORT_ID_CONFLICT", "Support identity already exists")
        uow.session.execute(insert(supports).values(id=fact.support.support_id,
            assertion_id=fact.assertion_id, payload=fact.support.model_dump(mode="json"), active=True,
            recorded_at=func.clock_timestamp()))


def _pending_ref(uow, identifier, expected):
    # F02 assigns event_id. This composite key resolves to that immutable event
    # after the caller appends it; no invented event UUID is stored.
    return {"resource_id": str(identifier), "revision": expected + 1}


def require_create_permission(uow,actor,identifier,owner_scope_id):
    """Authorize the prospective S03 scope without inserting a placeholder."""
    from sqlalchemy import or_
    from lzo.auth.models import grants,roles
    from lzo.auth.permissions import grant_predicate
    validate_actor(uow,actor)
    permitted=uow.session.execute(select(grants.c.id).select_from(grants.join(roles)).where(
        grant_predicate(actor,'edit'),grants.c.security_class=='data',or_(grants.c.global_scope.is_(True),
            grants.c.resource_id==identifier,(grants.c.scope_id==owner_scope_id) if owner_scope_id is not None else False)).limit(1)).first()
    if permitted is None:raise Forbidden('FORBIDDEN','Operation forbidden')


def register_object(uow, actor, create, bundle):
    with permission_context(uow):
        fact_validation.begin(uow)
        uow.facts_type_decision_pins = ()
        validate_actor(uow, actor)
        document = effective_bundle(uow, actor, bundle)
        require_create_permission(uow,actor,create.resource_id,create.owner_scope_id)
        if (uow.session.execute(select(resource_heads.c.resource_id).where(
                resource_heads.c.resource_id == create.resource_id)).first()
                or uow.session.execute(select(assertions.c.id).where(assertions.c.id == create.resource_id)).first()
                or uow.session.execute(select(supports.c.id).where(supports.c.id == create.resource_id)).first()):
            raise Conflict("OBJECT_ID_CONFLICT", "Global resource identity already exists")
        # Creation scope is explicit; related scopes never participate in grants.
        uow.session.execute(insert(resource_scopes).values(resource_id=create.resource_id,
            scope_id=create.owner_scope_id, owner_scope_id=create.owner_scope_id,
            related_scope_ids=[str(x) for x in create.related_scope_ids],
            resource_kind="object", security_class="data"))
        require_permission(actor, "edit", ResourceScope(create.resource_id, None, (), "data"), uow=uow)
        report = validate_object(actor, create, bundle)
        accepted = effective_inputs(create.assertions, evaluation_time(uow))
        refs = set(uow.session.execute(select(objects.c.id).where(objects.c.iri.in_(
            [a.object.value for a in create.assertions if a.object.kind == "iri"]))).scalars())
        accepted_report = report
        if len(accepted) != len(create.assertions):
            accepted_report, _ = check_graph(uow, actor, create.resource_id, create.class_iris,
                accepted, document, bundle, changed=accepted,
                changed_predicates={a.predicate_iri for a in accepted} | {str(RDF.type)})
        enforce_report(accepted_report, create.state)
        for at in sorted({a.valid_from for a in create.assertions if
                          a.state == "accepted" and a.valid_from > evaluation_time(uow)}):
            future = effective_inputs(create.assertions, at)
            future_report, future_refs = check_graph(uow, actor, create.resource_id, create.class_iris,
                future, document, bundle, as_of=at, changed=future,
                changed_predicates={a.predicate_iri for a in future} | {str(RDF.type)})
            enforce_report(future_report, create.state)
            refs.update(future_refs)
        effect = {"operation": "object.create", "create": create.model_dump(mode="json")}
        refs.update(authorize_effect(uow, actor, document, bundle, effect, create.assertions,
                                    decision_ref=create.decision_ref))
        # F02 identity row is needed for the FK; revision is appended exactly once.
        uow.session.execute(insert(resource_heads).values(resource_id=create.resource_id, revision=0))
        payload = create.model_dump(mode="json", exclude={"assertions", "decision_ref", "resource_id"})
        row = {"id": create.resource_id, "iri": object_iri(create.resource_id), "payload": payload}
        uow.session.execute(insert(objects).values(**row, revision=0, bundle_id=bundle.id))
        _insert_assertions(uow, create.assertions, bundle, _pending_ref(uow, create.resource_id, 0), create.decision_ref)
        receipt = record_state(uow, actor, row, 0, bundle, refs, "object.created", effect, accepted_report,
                               candidate_report=report if any(a.state == "candidate" for a in create.assertions) else None)
        type_pins = set(getattr(uow, "facts_type_decision_pins", ()))
        type_pins.update(revision_ref(uow, value) for value in definition_source_closure(uow, {bundle.id}))
        uow.session.execute(insert(type_declarations).values(resource_id=create.resource_id,
            class_iris=list(create.class_iris), revision_ref=json_value(receipt.revision),
            dependency_refs=json_value(sorted(type_pins, key=lambda p: (str(p.resource_id), p.revision)))))
        return receipt


def apply_fact_patch(uow, actor, patch, bundle):
    with permission_context(uow):
        fact_validation.begin(uow)
        row = get_object_row(uow, actor, patch.resource_id, "edit")
        document = effective_bundle(uow, actor, bundle)
        row = get_object_row(uow, actor, patch.resource_id, "edit")
        if row["revision"] != patch.expected_revision:
            raise Conflict("REVISION_CONFLICT", "Object revision changed")
        if row["payload"]["state"] == "inactive":
            raise Conflict("OBJECT_INACTIVE", "Object is inactive")
        old = current_assertions(uow, patch.resource_id, eligible_only=False)
        withdraw_ids = set(patch.withdraw_support_ids)
        withdrawal = [(a["payload"], s["payload"]) for a in old for s in a["supports"] if s["id"] in withdraw_ids]
        if len(withdrawal) != len(withdraw_ids):
            raise ApiError("SUPPORT_NOT_FOUND", 404, "Support not found")
        effect = {"operation": "facts.patch", "patch": patch.model_dump(mode="json")}
        authority_refs = authorize_effect(uow, actor, document, bundle, effect, patch.additions,
                                         withdrawal, decision_ref=patch.decision_ref)
        remaining = tuple(a for a in assertion_inputs(current_assertions(uow, patch.resource_id))
            if a.support.support_id not in withdraw_ids)
        combined = (*remaining, *patch.additions)
        changed_predicates = {a.predicate_iri for a in patch.additions} | {a["predicate_iri"] for a, _ in withdrawal}
        row_context=getattr(uow,"_semantic_source_row",None)
        geometry_addition=bool(row_context and row_context["row"].geometry)
        permit_incomplete=bool(withdraw_ids) and not patch.additions
        if row_context:
            from lzo.semantic.source_rows import withdrawal_only
            permit_incomplete=withdrawal_only(row_context["row"])
            if geometry_addition or row_context["row"].withdraw_geometry_support_ids:
                changed_predicates.add("urn:lzo:geometry")
        classes=current_classes(uow,actor,patch.resource_id)
        accepted_report, refs = check_graph(uow, actor, patch.resource_id, classes,
            effective_inputs(combined, evaluation_time(uow)), document, bundle,
            changed=effective_inputs(patch.additions, evaluation_time(uow)), changed_predicates=changed_predicates,
            permit_incomplete=permit_incomplete)
        if any(a.state == "accepted" for a in patch.additions) or geometry_addition:
            enforce_report(accepted_report, row["payload"]["state"])
            future_times={a.valid_from for a in patch.additions if
                a.state == "accepted" and a.valid_from > evaluation_time(uow)}
            if geometry_addition and row_context["row"].geometry.valid_from>evaluation_time(uow):
                future_times.add(row_context["row"].geometry.valid_from)
            for at in sorted(future_times):
                future_report, future_refs = check_graph(uow, actor, patch.resource_id,
                    classes, effective_inputs(combined, at), document, bundle,
                    as_of=at, changed=effective_inputs(patch.additions, at), changed_predicates=changed_predicates)
                enforce_report(future_report, row["payload"]["state"])
                refs.update(future_refs)
        report = None
        if any(a.state == "candidate" for a in combined):
            report, candidate_refs = check_graph(uow, actor, patch.resource_id, classes,
                (*effective_inputs(combined, evaluation_time(uow)), *(a for a in combined if a.state == "candidate")),
                document, bundle, changed=patch.additions, changed_predicates=changed_predicates,
                permit_incomplete=permit_incomplete)
            refs.update(candidate_refs)
        refs.update(authority_refs)
        _insert_assertions(uow, patch.additions, bundle,
                           _pending_ref(uow, patch.resource_id, patch.expected_revision), patch.decision_ref)
        uow.session.execute(update(supports).where(supports.c.id.in_(withdraw_ids)).values(active=False))
        receipt = record_state(uow, actor, row, patch.expected_revision, bundle, refs, "object.facts_changed",
                               effect, accepted_report, candidate_report=report,
                               permit_incomplete=permit_incomplete)
        uow.session.execute(update(supports).where(supports.c.id.in_(withdraw_ids)).values(
            withdrawn_event_id=receipt.revision.event_id))
        return receipt


def assertion_view(uow, row):
    payload = dict(row["payload"])
    payload.pop("assertion_id")
    reference = row["revision_ref"]
    event_id = uow.session.execute(select(revisions.c.event_id).where(
        revisions.c.resource_id == UUID(reference["resource_id"]),
        revisions.c.revision == reference["revision"])).scalar_one()
    reference = RevisionRef(UUID(reference["resource_id"]), reference["revision"], event_id)
    payload["decision_ref"] = payload["decision_ref"] or reference
    return AssertionView(id=row["id"], **payload, supports=tuple(SourceSupport.model_validate(s["payload"])
        for s in row["supports"]), revision=reference, bundle_id=row["bundle_id"])


def object_view(uow, actor, row):
    properties = {}
    for a in current_assertions(uow, row["id"]):
        if (a["payload"]["state"] == "accepted" and row["payload"]["state"] != "inactive"
                and is_effective(a["payload"], evaluation_time(uow))):
            term = RdfTerm(**a["payload"]["object"])
            values = properties.setdefault(a["payload"]["predicate_iri"], [])
            if term not in values:
                values.append(term)
    state = uow.session.execute(select(projection_events.c.state).where(
        projection_events.c.resource_id == row["id"], projection_events.c.revision == row["revision"])
        ).scalar_one_or_none() or "pending"
    snapshot = uow.session.execute(select(revisions.c.payload).where(
        revisions.c.resource_id == row["id"], revisions.c.revision == row["revision"])).scalar_one()
    due = row["next_transition_at"] is not None and row["next_transition_at"] <= evaluation_time(uow)
    report = snapshot.get("validation")
    fresh = fact_validation.is_fresh(uow, actor, row, snapshot)
    candidate_fresh = fact_validation.is_fresh(uow, actor, row, snapshot, candidate=True)
    if due:
        state = "pending"
    if due or not fresh:
        report = None
    validation_state = ("pending" if report is None else "conforms" if report["conforms"] else "needs_review")
    next_at = row["next_transition_at"]
    for current, key in ((fresh, "validation_evidence"), (candidate_fresh, "candidate_validation_evidence")):
        if current and snapshot[key]["next_boundary"]:
            context_at = datetime.fromisoformat(snapshot[key]["next_boundary"])
            next_at = min(next_at, context_at) if next_at else context_at
    return ObjectView(id=row["id"], iri=row["iri"], revision=row["revision"],
        **{**row['payload'],'class_iris':current_classes(uow,actor,row['id'])},
        properties=properties, projection_state=state, bundle_id=row["bundle_id"],
        validation_state=validation_state, validation_report=report, evaluation_time=evaluation_time(uow),
        candidate_validation_report=snapshot.get("candidate_validation") if candidate_fresh and not due else None,
        next_transition_at=next_at)


def list_objects(uow, actor, cursor=None):
    validate_actor(uow, actor)
    query = visible_objects(actor)
    if cursor:
        try:
            query = query.where(objects.c.id > UUID(cursor))
        except ValueError:
            raise ApiError("INVALID_CURSOR", 422, "Invalid cursor") from None
    size = settings_for(uow).list_page_size
    rows = uow.session.execute(query.order_by(objects.c.id).limit(size+1)).mappings().all()
    return Page([object_view(uow, actor, row) for row in rows[:size]],
                str(rows[size-1]["id"]) if len(rows) > size else None)


def _deactivate_descriptor(uow, targets, command):
    from lzo.governance.risk import ChangeDescriptor
    if (set(command) != {"operation", "resource_id", "expected_revision", "reason"} or
            command["operation"] != "object.deactivate" or type(command["expected_revision"]) is not int or
            not isinstance(command["reason"], str) or not command["reason"].strip() or
            len(command["reason"]) > 4000):
        raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Invalid deactivation command")
    identifier = UUID(command["resource_id"])
    row = uow.session.execute(select(objects).where(objects.c.id == identifier)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    if row["revision"] != command["expected_revision"]:
        raise Conflict("REVISION_CONFLICT", "Object revision changed")
    refs = set(uow.session.execute(select(object_dependencies.c.dependency_id).where(
        object_dependencies.c.resource_id == identifier)).scalars())
    inbound = set()
    candidates = uow.session.execute(select(assertions).where(
        assertions.c.resource_id != identifier,
        assertions.c.payload["state"].astext == "accepted",
        assertions.c.payload["object"]["kind"].astext == "iri",
        assertions.c.payload["object"]["value"].astext == object_iri(identifier))).mappings()
    for assertion in candidates:
        payload = assertion["payload"]
        end = datetime.fromisoformat(payload["valid_to"].replace("Z", "+00:00")) if payload["valid_to"] else None
        if end is not None and end <= evaluation_time(uow):
            continue
        active_support = uow.session.execute(select(supports.c.id).where(
            supports.c.assertion_id == assertion["id"], supports.c.active.is_(True)).limit(1)).first()
        source = uow.session.execute(select(objects.c.payload).where(
            objects.c.id == assertion["resource_id"])).scalar_one()
        if active_support and source["state"] != "inactive":
            inbound.add(assertion["resource_id"])
    active_bundle = uow.session.execute(select(catalog.c.active_bundle_id)).scalar_one()
    refs = definition_source_closure(uow, refs | inbound | {row["bundle_id"], active_bundle}) - {identifier}
    baseline = {str(ref): head(uow, ref)["revision"] for ref in refs | {identifier}}
    return ChangeDescriptor(kind="object.deactivate", target_ids=(identifier, *sorted(refs, key=str)),
        command=command, baseline=baseline, impact={"blocking_inbound_ids": sorted(map(str, inbound))},
        submit_action="edit", review_action="object.review", execute_action="edit",
        operation_key=f"object.deactivate:{identifier}", operation_target_ids=(identifier,))


def deactivate_object(uow, actor, identifier, expected, request):
    from lzo.governance.approvals import ApprovalService
    fact_validation.begin(uow)
    row = get_object_row(uow, actor, identifier, "edit")
    lock_ledger(uow)
    command = {"operation": "object.deactivate", "resource_id": str(identifier),
               "expected_revision": expected, "reason": request.reason}
    service = ApprovalService(uow)
    change = service.preview(actor, "object.deactivate", [identifier], command)
    if change.impact["blocking_inbound_ids"]:
        raise Conflict("OBJECT_HAS_INBOUND_RELATIONS", "Withdraw incoming accepted supports before deactivation")
    if request.approval_id is None:
        raise Conflict("APPROVAL_REQUIRED", "Independent approval required")
    service.require_executable(actor, request.approval_id, change.change_digest, change.baseline)
    if row["payload"]["state"] == "inactive":
        raise Conflict("OBJECT_INACTIVE", "Object is already inactive")
    with permission_context(uow):
        from lzo.ontology.releases import current_execution_bundle
        bundle = current_execution_bundle(uow, actor)
        effective_bundle(uow, actor, bundle)
    changed = dict(row) | {"payload": {**row["payload"], "state": "inactive"}}
    refs = set(change.target_ids) | {request.approval_id}
    return record_state(uow, actor, changed, expected, bundle, refs, "object.deactivated", command)


def _validity_references(payload):
    return tuple(UUID(v) for v in payload["reference_ids"])


def _validity_boundary(ctx):
    with ctx.batch():
        uow = ctx.uow
        fact_validation.begin(uow)
        uow.ontology_settings = ctx.runner.settings
        identifier = UUID(ctx.payload["resource_id"])
        row = get_object_row(uow, ctx.actor, identifier, "edit")
        if (row["transition_origin_event_id"] != UUID(ctx.payload["origin_event_id"]) or
                row["next_transition_at"] is None or row["next_transition_at"] > evaluation_time(uow)):
            ctx.checkpoint({"superseded": True})
            return
        from lzo.ontology.releases import current_execution_bundle
        bundle = current_execution_bundle(uow, ctx.actor)
        document = effective_bundle(uow, ctx.actor, bundle)
        values = effective_inputs(assertion_inputs(current_assertions(uow, identifier)), evaluation_time(uow))
        report, refs = check_graph(uow, ctx.actor, identifier, current_classes(uow,ctx.actor,identifier), values, document, bundle,
            changed=values, changed_predicates={a["payload"]["predicate_iri"] for a in current_assertions(uow, identifier)},
            permit_incomplete=True)
        receipt = record_state(uow, ctx.actor, row, row["revision"], bundle, refs, "object.validity_changed",
            {"operation": "facts.validity", "origin_event_id": ctx.payload["origin_event_id"],
             "due_at": ctx.payload["due_at"]}, report, permit_incomplete=True)
        ctx.checkpoint({"revision": receipt.revision.revision, "event_id": str(receipt.revision.event_id)})


from lzo.worker.service import register_handler
from lzo.governance.risk import register_policy
register_policy("object.deactivate", _deactivate_descriptor)
register_handler("facts.validity", _validity_boundary, references=_validity_references, action="edit")
OBJECT_EVENTS=("object.created", "object.facts_changed", "object.geometry_changed", "object.deactivated",
               "object.validity_changed", "object.spatial_asset_registered", "object.location_observed")
for _event in OBJECT_EVENTS:
    register_projection(_event, _projection, version="objects-v1", dirty_kind="facts", action="edit")
