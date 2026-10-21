"""Current authorized logical RDF, assembled before any public evaluation.

The remote store remains authoritative for RDF terms. PG eligibility removes
expired/withdrawn facts; absent/corrupt remote state never falls back to PG RDF.
Operational component inventories stay private within this assembly boundary.
"""
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from rdflib import Dataset, URIRef
from rdflib.namespace import RDF
from sqlalchemy import select, func, text

from lzo.auth.permissions import (_current_uow, authorized_resource_ids, authorized_statement,
    permission_context, require_permission, validate_actor)
from lzo.contracts import ResourceScope
from lzo.errors import ApiError
from lzo.governance.models import platform_watermarks
from lzo.ontology.drafts import authorize, definition_source_closure, settings_for
from lzo.ontology.models import releases
from lzo.ontology.releases import current_execution_bundle
from lzo.ontology.schemas import SchemaDocument
from lzo.semantic import facts, proofs
from lzo.semantic.fuseki import FusekiRepository, graph_iri
from lzo.semantic.inference import InferenceInput, InferenceResult, TypePremiseRef, derived_assertion_id
from lzo.semantic.models import objects, projection_heads
from lzo.semantic.rdf import RdfTerm, Triple

ROLES = ("schema", "accepted", "candidate", "derived", "provenance")


def logical_graph_iri(role):
    if role not in ROLES:
        raise ValueError("Unknown logical role")
    return "urn:lzo:query:" + role


@dataclass
class AuthorizedDataset:
    dataset: Dataset
    authorization_version: int
    input_watermark: int
    bundle_id: UUID
    evaluated_at: datetime
    event_position: int
    next_boundary: datetime | None = None
    references: set[UUID] = field(default_factory=set)
    object_rows: dict = field(default_factory=dict)
    assertions: dict = field(default_factory=dict)
    proof_views: dict = field(default_factory=dict)
    triple_references: dict = field(default_factory=dict)


def _permitted(command):
    try:
        return command()
    except ApiError as error:
        if error.status in (403, 404):
            return None
        raise


def revalidate(uow, actor, snapshot):
    if hasattr(uow, "query_read_budget"):
        uow.query_read_budget.checkpoint()
    validate_actor(uow, actor)
    position = uow.session.execute(select(platform_watermarks.c.event_position)).scalar_one()
    now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    if (position != snapshot.event_position or current_execution_bundle(uow, actor).id != snapshot.bundle_id
            or snapshot.next_boundary is not None and now >= snapshot.next_boundary):
        raise ApiError("QUERY_SNAPSHOT_CHANGED", 409, "Semantic inputs changed during the query")


def build_authorized_dataset(actor, mode="current") -> AuthorizedDataset:
    uow = _current_uow.get()
    if mode not in {"current", "review"}:
        raise ApiError("QUERY_MODE_INVALID", 422, "Unknown query mode")
    validate_actor(uow, actor)
    settings = settings_for(uow)
    from lzo.semantic.query_read import ReadBudget
    budget = getattr(uow, "query_read_budget", None) or ReadBudget(settings)
    uow.query_read_budget = budget
    budget.checkpoint()
    stamp = uow.session.execute(select(platform_watermarks)).mappings().one()
    now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    uow.facts_as_of = now
    bundle = current_execution_bundle(uow, actor)
    schema_refs = definition_source_closure(uow, {bundle.id, bundle.schema_release_id})
    for identifier in schema_refs:
        authorize(uow, actor, identifier)
    allowed = set(authorized_resource_ids(actor, "view", uow=uow))
    candidate_allowed = set(uow.session.execute(authorized_statement(actor, "candidate.read")).scalars())
    if mode == "review" and not candidate_allowed:
        raise ApiError("FORBIDDEN", 403, "Candidate review permission is required")
    repository = FusekiRepository(settings.fuseki_url, read_budget=budget, parse_nt=budget.parse_nt)
    schema = repository.read_graph(graph_iri(bundle.schema_release_id, "schema"))
    document = SchemaDocument.model_validate(uow.session.execute(select(releases.c.document).where(
        releases.c.id == bundle.schema_release_id)).scalar_one())
    vocabulary = {c.iri for c in document.classes} | {p.iri for p in document.properties} | {r.iri for r in document.relations}
    vocabulary.update(v.value for p in document.properties for v in p.enum_values if v.kind == "iri")
    snapshot = AuthorizedDataset(Dataset(default_union=False), actor.authorization_version,
        stamp["input_watermark"], bundle.id, now, stamp["event_position"], references=set(schema_refs))
    object_ids, quad_count = {}, 0

    def add(role, triple, references=()):
        nonlocal quad_count
        budget.checkpoint()
        nodes = triple.nodes()
        new_role = (role, triple) not in snapshot.triple_references
        new_default = role in {"accepted", "derived"} and nodes not in snapshot.dataset.default_graph
        if quad_count + new_role + new_default > settings.query_max_quads:
            raise ApiError("SPARQL_BUDGET_EXCEEDED", 422, "Authorized dataset exceeds budget")
        quad_count += new_role + new_default
        selected_refs = set(references) | schema_refs
        for value in (triple.subject_iri, triple.object.value if triple.object.kind == "iri" else None):
            if value in object_ids:
                selected_refs.add(object_ids[value])
        snapshot.triple_references.setdefault((role, triple), set()).update(selected_refs)
        if new_role:
            snapshot.dataset.graph(URIRef(logical_graph_iri(role))).add(nodes)
        if new_default:
            snapshot.dataset.default_graph.add(nodes)

    for triple in schema:
        add("schema", triple, schema_refs)
    declarations, visible_types = {}, []
    # Narrow declaration visibility preserves a's type when a->hidden-b is
    # excluded. Full-object/history DTOs retain their independent ALL policy.
    rows = uow.session.execute(select(objects).where(objects.c.id.in_(allowed))
        .order_by(objects.c.id).execution_options(yield_per=200)).mappings()
    for row in rows:
        budget.checkpoint()
        if row["payload"]["state"] == "inactive":
            continue
        declaration = _permitted(lambda: proofs.get_type_declaration_evidence(uow, actor, row["id"]))
        snapshot.object_rows[row["id"]] = dict(row)
        if declaration is not None and declaration.lifecycle_state!="inactive":
            declarations[row["id"]] = declaration
    visible_iris = {r["iri"] for r in snapshot.object_rows.values()}
    object_ids.update({row["iri"]:identifier for identifier,row in snapshot.object_rows.items()})
    for identifier, row in snapshot.object_rows.items():
        budget.checkpoint()
        projected = uow.session.execute(select(projection_heads).where(
            projection_heads.c.resource_id == identifier)).mappings().first()
        generation = repository.current_revision(identifier)
        if (projected is None or projected["revision"] != row["revision"] or generation is None
                or generation["revision"] != row["revision"] or generation["digest"] != projected["digest"]):
            raise ApiError("QUERY_PROJECTION_PENDING", 409, "Current semantic projection is incomplete")
        logical = {role: set(repository.read_graph(graph_iri(identifier, role)))
                   for role in ("accepted", "provenance", *(["candidate"] if mode == "review" and identifier in candidate_allowed else []))}
        declaration = declarations.get(identifier)
        snapshot.references.add(identifier)
        if declaration:snapshot.references.update(r.resource_id for r in declaration.dependency_refs)
        for cls in declaration.class_iris if declaration else ():
            triple = Triple(row["iri"], str(RDF.type), RdfTerm("iri", cls))
            if triple not in logical["accepted"]:
                raise ApiError("QUERY_PROJECTION_PENDING", 409, "Current semantic projection is incomplete")
            add("accepted", triple, {identifier, *(r.resource_id for r in declaration.dependency_refs)})
            visible_types.append(TypePremiseRef(declaration_revision=declaration.declaration_revision, class_iri=cls))
        from lzo.semantic.source_rows import selected_geometry
        selected=selected_geometry(uow,identifier)
        geometry={"revision":selected["revision"],"payload":selected} if selected else None
        if geometry is not None:
            from lzo.semantic.schemas import GeometryInput
            value = GeometryInput.model_validate({k: v for k, v in geometry["payload"].items()
                if k in GeometryInput.model_fields})
            refs = _permitted(lambda: facts.support_references(uow, actor, value.source_support))
            if refs is not None and value.decision_ref is not None:
                if value.decision_ref.resource_id not in allowed:
                    refs = None
                else:
                    facts.verify_reference(uow, actor, value.decision_ref)
                    refs.add(value.decision_ref.resource_id)
            if refs is not None:
                pointer = Triple(row["iri"], "urn:lzo:geometry", RdfTerm("iri",
                    f"urn:lzo:geometry:{identifier}:{geometry['revision']}"))
                if pointer not in logical["accepted"]:
                    raise ApiError("QUERY_PROJECTION_PENDING", 409, "Current geometry projection is incomplete")
                add("accepted", pointer, refs | {identifier})
                snapshot.references.update(refs)
        for raw in facts.current_assertions(uow, identifier):
            value = facts.assertion_view(uow, raw)
            role = value.state
            if role not in logical:
                continue
            if role == "accepted":
                for boundary in (value.valid_from, value.valid_to):
                    if boundary is not None and boundary > now:
                        snapshot.next_boundary = min(snapshot.next_boundary, boundary) if snapshot.next_boundary else boundary
                if value.valid_from > now or value.valid_to is not None and now >= value.valid_to:
                    continue
            if value.object.kind == "iri" and value.object.value not in visible_iris | vocabulary:
                continue
            support_values, support_refs = [], set()
            for support in value.supports:
                refs = _permitted(lambda: facts.support_references(uow, actor, support))
                if refs is not None:
                    support_values.append(support)
                    support_refs.update(refs)
            if not support_values:
                continue
            if value.decision_ref is not None:
                if value.decision_ref.resource_id not in allowed:
                    continue
                facts.verify_reference(uow, actor, value.decision_ref)
                support_refs.add(value.decision_ref.resource_id)
            triple = Triple(row["iri"], value.predicate_iri, value.object)
            if triple not in logical[role]:
                raise ApiError("QUERY_PROJECTION_PENDING", 409, "Current semantic projection is incomplete")
            add(role, triple, support_refs | {identifier})
            value = value.model_copy(update={"supports": tuple(support_values)})
            snapshot.assertions[value.id] = value
            snapshot.references.update(support_refs)
            for support in support_values:
                provenance = Triple(f"urn:lzo:assertion:{value.id}", "urn:lzo:support",
                    RdfTerm("iri", f"urn:lzo:support:{support.support_id}"))
                if provenance not in logical["provenance"]:
                    raise ApiError("QUERY_PROJECTION_PENDING", 409, "Current provenance is incomplete")
                add("provenance", provenance, support_refs | {identifier})
    manifest = proofs.current_metadata(uow, actor, now=now)
    if manifest is not None:
        from lzo.semantic.inference_jobs import components
        for identifier, entry in manifest["components"].items():
            # No component reference authorization: proofs are selected from
            # current authorized roots, not a hidden canonical branch's ACL.
            if entry["next_boundary"] is not None:
                boundary = datetime.fromisoformat(entry["next_boundary"])
                snapshot.next_boundary = min(snapshot.next_boundary, boundary) if snapshot.next_boundary else boundary
            payload = uow.session.execute(select(components.c.payload).where(components.c.id == UUID(identifier))).scalar_one()
            inputs, result = InferenceInput.model_validate(payload["input"]), InferenceResult.model_validate(payload["result"])
            visible = {a.id for a in inputs.assertions if a.id in snapshot.assertions
                       and snapshot.assertions[a.id].state == "accepted"
                       and {s.support_id for s in a.supports} & {s.support_id for s in snapshot.assertions[a.id].supports}}
            selected = []
            for conclusion in result.conclusions:
                budget.checkpoint()
                if conclusion.subject_iri not in visible_iris | vocabulary:
                    continue
                if conclusion.object.kind == "iri" and conclusion.object.value not in visible_iris | vocabulary:
                    continue
                expansion = proofs.select_witnesses(inputs, result, conclusion,
                    visible_assertion_ids=visible, visible_type_refs=visible_types, max_nodes=settings.query_max_proof_nodes)
                if expansion.truncated and not expansion.alternatives:
                    raise ApiError("SPARQL_BUDGET_EXCEEDED", 422, "Proof visibility search did not complete")
                if expansion.alternatives:
                    selected.append((conclusion, expansion))
            if selected:
                logical = repository.read_graph(graph_iri(UUID(identifier), "derived"))
                if logical != result.conclusions:
                    raise ApiError("INFERENCE_REPRESENTATION_INVALID", 409, "Derived generation is inconsistent")
                for conclusion, expansion in selected:
                    references = set(schema_refs)
                    for node in expansion.nodes:
                        for witness in node.witnesses:
                            if witness.kind == "assertion":
                                a = snapshot.assertions[witness.assertion_id]
                                references.update(snapshot.triple_references[("accepted",
                                    Triple(facts.object_iri(a.resource_id), a.predicate_iri, a.object))])
                            elif witness.kind == "type":
                                d = declarations[witness.type_ref.declaration_revision.resource_id]
                                references.update({d.declaration_revision.resource_id, *(r.resource_id for r in d.dependency_refs)})
                    add("derived", conclusion, references)
                    snapshot.proof_views[derived_assertion_id(conclusion)] = (conclusion, expansion)
    revalidate(uow, actor, snapshot)
    return snapshot
