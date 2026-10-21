"""Internal evidence readers. Public proof routes await S05 filtering."""
from uuid import UUID

from sqlalchemy import select

from lzo.auth.permissions import permission_context
from lzo.contracts import RevisionRef
from lzo.errors import ApiError
from lzo.governance.models import revisions
from lzo.semantic import facts
from lzo.semantic.inference import TypeDeclarationEvidence, TypePremiseRef, axiom_iri
from lzo.semantic.models import type_declarations
from lzo.semantic.rdf import Triple, RdfTerm
from lzo.semantic.schemas import Model
from typing import Literal


def _ref(value):
    return RevisionRef(UUID(value["resource_id"]), value["revision"], UUID(value["event_id"]))


def get_type_declaration_evidence(uow, actor, resource_id) -> TypeDeclarationEvidence:
    """Read the narrow real declaration, never fabricate assertion/support roots.

    confirmed_types supplies the current-right and exact-reference checks. The
    immutable creation read selects only type provenance fields; full creation
    history remains behind HistoryService's ALL-reference visibility boundary.
    """
    with permission_context(uow):
        classes, lifecycle, _ = facts.confirmed_types(uow, actor, resource_id)
        declaration = uow.session.execute(select(type_declarations).where(
            type_declarations.c.resource_id == resource_id)).mappings().one()
        ref = _ref(declaration["revision_ref"])
        creation = uow.session.execute(select(
            revisions.c.event_type,
            revisions.c.payload["object"]["class_iris"].label("classes"),
            revisions.c.payload["object"]["data_nature"].astext.label("nature"),
            revisions.c.payload["bundle_id"].astext.label("bundle_id")).where(
                revisions.c.resource_id == ref.resource_id, revisions.c.revision == ref.revision,
                revisions.c.event_id == ref.event_id)).mappings().one_or_none()
        if (creation is None or ref.resource_id != resource_id
                or creation["event_type"] != "object.created"
                or sorted(creation["classes"] or []) != sorted(classes)):
            raise ApiError("TYPE_EVIDENCE_UNAVAILABLE", 409, "Confirmed type evidence is unavailable")
        try:
            return TypeDeclarationEvidence(declaration_revision=ref, class_iris=tuple(classes),
                dependency_refs=tuple(_ref(v) for v in declaration["dependency_refs"]),
                data_nature=creation["nature"], declaration_bundle_id=UUID(creation["bundle_id"]),
                lifecycle_state=lifecycle, lifecycle_revision=facts.revision_ref(uow, resource_id))
        except (ValueError, TypeError):
            raise ApiError("TYPE_EVIDENCE_UNAVAILABLE", 409, "Confirmed type evidence is unavailable") from None


class SelectedPremise(Model):
    conclusion: Triple
    kind: Literal["assertion", "type", "proof", "axiom"]
    assertion_id: UUID | None = None
    type_ref: TypePremiseRef | None = None
    proof_id: UUID | None = None
    schema_axiom_iri: str | None = None


class SelectedProof(Model):
    """Displayed witnesses for a stored proof; canonical edges are unchanged."""
    id: UUID
    conclusion: Triple
    rule_iri: str
    witnesses: tuple[SelectedPremise, ...]


class ProofExpansion(Model):
    alternatives: tuple[SelectedProof, ...]
    nodes: tuple[SelectedProof, ...]
    data_natures: tuple[str, ...]
    truncated: bool


def select_witnesses(inputs, result, conclusion, *, visible_assertion_ids,
                     visible_type_refs, max_nodes=200) -> ProofExpansion:
    """Internal bounded expansion after S05/current-root visibility selection.

    Premise groups are AND, matching roots/proofs are OR. No hidden canonical
    branch is copied into this displayed view. Schema input authorization is a
    prerequisite; callers supply only currently authorized root identities.
    """
    from rdflib.namespace import RDF
    available = {}
    natures = {}
    for a in inputs.assertions:
        if a.id not in visible_assertion_ids or a.state != "accepted" or not a.supports:
            continue
        t = Triple(facts.object_iri(a.resource_id), a.predicate_iri, a.object)
        available.setdefault(t, []).append(SelectedPremise(conclusion=t, kind="assertion", assertion_id=a.id))
        natures[str(a.id)] = a.data_nature
    allowed_types = {p.model_dump_json() for p in visible_type_refs}
    for d in inputs.type_declarations:
        for cls in d.class_iris:
            p = TypePremiseRef(declaration_revision=d.declaration_revision, class_iri=cls)
            if p.model_dump_json() in allowed_types:
                t = Triple(facts.object_iri(d.declaration_revision.resource_id), str(RDF.type), RdfTerm("iri", cls))
                available.setdefault(t, []).append(SelectedPremise(conclusion=t, kind="type", type_ref=p))
                natures[p.model_dump_json()] = d.data_nature
    for t in inputs.schema_triples:
        identity = next((a.iri for a in inputs.schema_axioms if a.triple == t), axiom_iri(t))
        available.setdefault(t, []).append(SelectedPremise(conclusion=t, kind="axiom", schema_axiom_iri=identity))
    by_conclusion = {}
    for p in result.proofs:
        by_conclusion.setdefault(p.conclusion, []).append(p)
    selected, used_natures = {}, set()
    remaining, truncated = max_nodes, False

    def choose(proof, visiting):
        nonlocal remaining, truncated
        if proof.id in visiting:
            return None
        if proof.id in selected:
            return selected[proof.id]
        if remaining <= 0:
            truncated = True
            return None
        remaining -= 1
        witnesses = []
        for premise in proof.premise_conclusions:
            roots = available.get(premise, ())
            if roots:
                chosen = sorted(roots, key=lambda r: r.model_dump_json())[0]
            else:
                chosen = None
                for other in by_conclusion.get(premise, ()):
                    value = choose(other, visiting | {proof.id})
                    if value is not None:
                        chosen = SelectedPremise(conclusion=premise, kind="proof", proof_id=value.id)
                        break
            if chosen is None:
                return None
            witnesses.append(chosen)
        value = SelectedProof(id=proof.id, conclusion=proof.conclusion,
            rule_iri=proof.rule_iri, witnesses=tuple(witnesses))
        selected[value.id] = value
        return value

    alternatives = tuple(v for p in by_conclusion.get(conclusion, ()) if (v := choose(p, set())) is not None)
    # Include only nodes reachable from successful alternatives, never failed
    # search branches (which may have selected unrelated authorized roots).
    reachable = set()
    def visit(node):
        if node.id in reachable:
            return
        reachable.add(node.id)
        for witness in node.witnesses:
            if witness.kind == "proof":
                visit(selected[witness.proof_id])
            elif witness.kind == "assertion":
                used_natures.add(natures[str(witness.assertion_id)])
            elif witness.kind == "type":
                used_natures.add(natures[witness.type_ref.model_dump_json()])
    for alternative in alternatives:
        visit(alternative)
    return ProofExpansion(alternatives=alternatives,
        nodes=tuple(selected[i] for i in sorted(reachable, key=str)),
        data_natures=tuple(sorted(used_natures)), truncated=truncated)


class ProofView(Model):
    assertion_id: UUID
    conclusion: Triple
    alternatives: tuple["ProofNode", ...]
    bundle_id: UUID
    input_watermark: int
    truncated: bool


from lzo.semantic.inference import ProofNode
ProofView.model_rebuild()


def current_metadata(uow, actor, *, now=None):
    """One private current-eligibility gate for ALL and actor-filtered readers.

    Returning metadata is not permission to expose its component membership.
    Current public readers must additionally select currently authorized roots.
    """
    from datetime import datetime
    from sqlalchemy import func
    from lzo.ontology.releases import current_execution_bundle
    from lzo.semantic.inference_jobs import _manifest, _inventory
    from lzo.semantic.projection import inference_is_current
    with permission_context(uow):
        # A pending governed withdrawal cannot keep a materially invalidated
        # justification current merely because its old RDF still exists.
        from lzo.semantic import facts
        if (facts._support_currentness_port is not None and
                facts._support_currentness_port.has_invalidated_supports(uow)):
            return None
        if not inference_is_current(uow):
            return None
        _, manifest = _manifest(uow)
        if manifest is None or manifest["state"] != "current":
            return None
        bundle = current_execution_bundle(uow, actor)
        if manifest["bundle_id"] != str(bundle.id):
            return None
        inventory = _inventory(uow)
        now = now or uow.session.execute(select(func.clock_timestamp())).scalar_one()
        members = []
        for entry in manifest["components"].values():
            members.extend(entry["members"])
            if (entry["bundle_id"] != str(bundle.id)
                    or any(inventory["heads"].get(key) != rev for key, rev in entry["heads"].items())
                    or entry["next_boundary"] is not None and now >= datetime.fromisoformat(entry["next_boundary"])):
                return None
        if set(members) != set(inventory["heads"]) or len(members) != len(set(members)):
            return None
        return manifest


def current_conclusions(uow, actor, settings):
    """Internal ALL-authorized current read seam, not a public S05 query route."""
    from lzo.ontology.drafts import authorize
    from lzo.semantic.inference_jobs import components
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    from lzo.semantic.inference import InferenceResult
    from lzo.semantic.rdf import ordered
    with permission_context(uow):
        manifest = current_metadata(uow, actor)
        if manifest is None:
            return ()
        repository, output = FusekiRepository(settings.fuseki_url), []
        for identifier in manifest["components"]:
            value = uow.session.execute(select(components.c.payload).where(
                components.c.id == UUID(identifier))).scalar_one()
            for ref in value["references"]:
                authorize(uow, actor, UUID(ref))
            result = InferenceResult.model_validate(value["result"])
            logical = repository.read_graph(graph_iri(UUID(identifier), "derived"))
            if logical != result.conclusions:
                raise ApiError("INFERENCE_REPRESENTATION_INVALID", 409, "Derived generation is inconsistent")
            output.extend(logical)
        return ordered(output)


def historical_proof(uow, actor, assertion_id, component_id, *, limit=200):
    """Historical existence does not require today's premise eligibility."""
    from lzo.governance.history import HistoryService
    from lzo.semantic.inference import InferenceResult
    from lzo.semantic.rdf import Triple, RdfTerm
    record = HistoryService(uow).get(actor, component_id, 1)
    value = record.payload
    serialized = value["assertion_ids"].get(str(assertion_id))
    if serialized is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    conclusion = Triple(serialized["subject_iri"], serialized["predicate_iri"], RdfTerm(**serialized["object"]))
    result = InferenceResult.model_validate(value["result"])
    alternatives = tuple(p for p in result.proofs if p.conclusion == conclusion)
    return ProofView(assertion_id=assertion_id, conclusion=conclusion,
        alternatives=alternatives[:limit], bundle_id=result.bundle_id,
        input_watermark=result.input_watermark, truncated=len(alternatives) > limit)


class CurrentProofView(Model):
    assertion_id: UUID
    conclusion: Triple
    alternatives: tuple[SelectedProof, ...]
    nodes: tuple[SelectedProof, ...]
    data_natures: tuple[str, ...]
    bundle_id: UUID
    input_watermark: int
    truncated: bool


def current_proof(actor, assertion_id):
    from lzo.auth.permissions import _current_uow
    from lzo.semantic.authorized_dataset import build_authorized_dataset, revalidate
    from lzo.semantic.query_read import ReadBudget
    from lzo.ontology.drafts import settings_for
    uow = _current_uow.get()
    budget = getattr(uow, "query_read_budget", None) or ReadBudget(settings_for(uow))
    uow.query_read_budget = budget
    budget.checkpoint()
    metadata = current_metadata(uow, actor)
    budget.checkpoint()
    if metadata is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    snapshot = build_authorized_dataset(actor)
    selected = snapshot.proof_views.get(assertion_id)
    if selected is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    conclusion, expansion = selected
    revalidate(uow, actor, snapshot)
    return CurrentProofView(assertion_id=assertion_id, conclusion=conclusion,
        **expansion.model_dump(), bundle_id=snapshot.bundle_id, input_watermark=snapshot.input_watermark)
