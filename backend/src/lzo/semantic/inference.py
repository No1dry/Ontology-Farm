"""Finite, deterministic least fixed point over exact logical RDF terms.

Only the six declared rule families execute. Proof anchors refer to the first
deterministically discovered proof of a premise, avoiding infinite enumeration
of cyclic paths. Every distinct grounded rule application is still retained.
No prior derived graph is ever used as a base root.
"""
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from itertools import product
import json
import time
from typing import Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from pydantic import Field
from rdflib.namespace import OWL, RDF, RDFS

from lzo.contracts import ExecutionBundle, RevisionRef
from lzo.semantic.rdf import RdfTerm, Triple, ordered, triple_key
from lzo.semantic.schemas import AssertionView, DataNature, Model


class TypePremiseRef(Model):
    declaration_revision: RevisionRef
    class_iri: str


class TypeDeclarationEvidence(Model):
    declaration_revision: RevisionRef
    class_iris: tuple[str, ...]
    dependency_refs: tuple[RevisionRef, ...]
    data_nature: DataNature
    declaration_bundle_id: UUID
    lifecycle_state: Literal["draft", "active", "inactive"]
    lifecycle_revision: RevisionRef


class InferenceScope(Model):
    mode: Literal["global", "affected"]
    members: tuple[RevisionRef, ...]
    trigger_revisions: tuple[RevisionRef, ...]
    evaluation_time: datetime
    next_boundary: datetime | None
    authorization_version: int
    reference_ids: tuple[UUID, ...]
    base_generation_id: UUID | None
    inventory_stamp: str
    covered_input_event_ids: tuple[UUID, ...]
    input_watermark: int
    bundle_id: UUID
    input_manifest_digest: str | None = None

    def digest(self):
        return sha256(self.model_dump_json().encode()).hexdigest()


class SchemaAxiomEvidence(Model):
    iri: str
    triple: Triple
    release_id: UUID | None = None
    artifact_sha256: str | None = None


class InferenceInput(Model):
    bundle: ExecutionBundle
    input_watermark: int = Field(ge=0)
    assertions: tuple[AssertionView, ...]
    schema_triples: tuple[Triple, ...]
    type_declarations: tuple[TypeDeclarationEvidence, ...] = ()
    evaluation_time: datetime | None = None
    scope: InferenceScope | None = None
    schema_axioms: tuple[SchemaAxiomEvidence, ...] = ()


class InferenceBudget(Model):
    max_triples: int = Field(ge=0)
    max_proofs: int = Field(ge=0)
    timeout_ms: int = Field(ge=0)


class ProofNode(Model):
    id: UUID
    conclusion: Triple
    rule_iri: str
    premise_assertion_ids: tuple[UUID, ...]
    premise_proof_ids: tuple[UUID, ...]
    schema_axiom_iris: tuple[str, ...]
    bundle_id: UUID
    active: bool
    premise_type_refs: tuple[TypePremiseRef, ...] = ()
    # Each entry is an AND premise; roots/proofs having that exact conclusion
    # are OR witnesses. IDs above pin one real witness, never an alias node.
    # Authorized expansion may choose another witness from the same immutable
    # run when the canonical one is hidden. Cyclic expansion uses visited IDs.
    premise_conclusions: tuple[Triple, ...] = ()


class InferenceResult(Model):
    conclusions: tuple[Triple, ...]
    proofs: tuple[ProofNode, ...]
    input_watermark: int
    bundle_id: UUID
    complete: bool
    scope_digest: str | None = None


def derived_assertion_id(conclusion: Triple) -> UUID:
    return uuid5(NAMESPACE_URL, "urn:lzo:derived-assertion:v1:" + triple_key(conclusion))


def axiom_iri(triple: Triple) -> str:
    return "urn:lzo:logical-axiom:" + sha256(triple_key(triple).encode()).hexdigest()


@dataclass(frozen=True)
class _Premise:
    kind: str
    identity: str
    axioms: tuple[str, ...] = ()


class _Exhausted(Exception):
    pass


@dataclass
class _TripleBudget:
    """Private run-wide exact union; never serialized as inference evidence."""
    max_triples: int
    visited: set[Triple] = field(default_factory=set)

    def add(self, triple):
        if triple not in self.visited:
            if len(self.visited) >= self.max_triples:
                raise _Exhausted
            self.visited.add(triple)


def derive(inputs: InferenceInput, budget: InferenceBudget, *,
           _triple_budget: _TripleBudget | None = None) -> InferenceResult:
    if inputs.scope is not None and (inputs.scope.input_watermark != inputs.input_watermark
            or inputs.scope.bundle_id != inputs.bundle.id):
        raise ValueError("Inference scope differs from input identity")
    started = time.monotonic()
    roots: dict[Triple, list[_Premise]] = {}
    type_refs = {}
    proofs: dict[UUID, ProofNode] = {}
    anchors: dict[Triple, _Premise] = {}
    inferred = set()
    known = set()
    accounting = _triple_budget if _triple_budget is not None else _TripleBudget(budget.max_triples)

    def check():
        if (len(known) > budget.max_triples or len(proofs) > budget.max_proofs
                or (time.monotonic() - started) * 1000 >= budget.timeout_ms):
            raise _Exhausted

    def root(t, premise):
        check()
        accounting.add(t)
        roots.setdefault(t, []).append(premise)
        known.add(t)

    def emit(conclusion, rule, premises):
        check()
        options = [roots.get(t) or [anchors[t]] for t in premises]
        for evidence in product(*options):
            assertions = tuple(sorted({UUID(p.identity) for p in evidence if p.kind == "assertion"}, key=str))
            previous = tuple(sorted({UUID(p.identity) for p in evidence if p.kind == "proof"}, key=str))
            types = tuple(type_refs[i] for i in sorted({p.identity for p in evidence if p.kind == "type"}))
            axioms = tuple(sorted({a for p in evidence for a in p.axioms}))
            payload = {"conclusion": triple_key(conclusion), "rule": rule,
                "assertions": [str(a) for a in assertions], "proofs": [str(p) for p in previous],
                "types": [t.model_dump(mode="json") for t in types], "axioms": axioms,
                "bundle": str(inputs.bundle.id)}
            identifier = uuid5(NAMESPACE_URL, json.dumps(payload, sort_keys=True, separators=(",", ":")))
            if identifier not in proofs:
                accounting.add(conclusion)
                proofs[identifier] = ProofNode(id=identifier, conclusion=conclusion,
                    rule_iri="urn:lzo:rule:" + rule, premise_assertion_ids=assertions,
                    premise_proof_ids=previous, premise_type_refs=types, schema_axiom_iris=axioms,
                    premise_conclusions=ordered(premises),
                    bundle_id=inputs.bundle.id, active=True)
                anchors.setdefault(conclusion, _Premise("proof", str(identifier), axioms))
                inferred.add(conclusion)
                known.add(conclusion)
                check()

    def iri_triple(s, p, o):
        return Triple(s, str(p), RdfTerm("iri", str(o)))

    try:
        registered = {a.triple: a.iri for a in inputs.schema_axioms}
        if inputs.scope is not None and inputs.schema_triples and not registered:
            raise ValueError("Schema axiom evidence is required for a persisted scope")
        if inputs.scope is not None:
            for axiom in inputs.schema_axioms:
                if (axiom.release_id != inputs.bundle.schema_release_id
                        or axiom.artifact_sha256 is None or len(axiom.artifact_sha256) != 64
                        or axiom.iri != "urn:lzo:axiom:" + sha256(
                            (axiom.artifact_sha256 + "\n" + triple_key(axiom.triple)).encode()).hexdigest()):
                    raise ValueError("Schema axiom evidence is not bound to its registered release artifact")
        if inputs.schema_axioms and (set(registered) != set(inputs.schema_triples)
                or len(registered) != len(inputs.schema_axioms)):
            raise ValueError("Schema axiom evidence is incomplete or ambiguous")
        for t in ordered(inputs.schema_triples):
            identity = registered.get(t) or axiom_iri(t)
            root(t, _Premise("axiom", identity, (identity,)))
        for a in sorted(inputs.assertions, key=lambda a: str(a.id)):
            if a.state != "accepted" or not a.supports:
                continue
            if inputs.evaluation_time is not None and not (
                    a.valid_from <= inputs.evaluation_time
                    and (a.valid_to is None or inputs.evaluation_time < a.valid_to)):
                continue
            root(Triple(f"urn:lzo:object:{a.resource_id}", a.predicate_iri, a.object),
                 _Premise("assertion", str(a.id)))
        for declaration in sorted(inputs.type_declarations,
                                  key=lambda d: str(d.declaration_revision.resource_id)):
            if declaration.lifecycle_state == "inactive":
                continue
            for cls in sorted(declaration.class_iris):
                ref = TypePremiseRef(declaration_revision=declaration.declaration_revision, class_iri=cls)
                identity = ref.model_dump_json()
                type_refs[identity] = ref
                root(iri_triple(f"urn:lzo:object:{ref.declaration_revision.resource_id}", RDF.type, cls),
                     _Premise("type", identity))
        check()
        while True:
            before = len(known)
            triples = ordered(known)
            by_predicate = {}
            for t in triples:
                by_predicate.setdefault(t.predicate_iri, []).append(t)
            subclasses = by_predicate.get(str(RDFS.subClassOf), ())
            subproperties = by_predicate.get(str(RDFS.subPropertyOf), ())
            for ax in subclasses:
                check()
                if ax.object.kind != "iri":
                    continue
                for other in subclasses:
                    check()
                    if other.subject_iri == ax.object.value and other.object.kind == "iri":
                        emit(iri_triple(ax.subject_iri, RDFS.subClassOf, other.object.value),
                             "subclass", (ax, other))
                for value in by_predicate.get(str(RDF.type), ()):
                    if value.object == RdfTerm("iri", ax.subject_iri):
                        emit(iri_triple(value.subject_iri, RDF.type, ax.object.value),
                             "type", (value, ax))
            for ax in subproperties:
                check()
                if ax.object.kind != "iri":
                    continue
                for value in by_predicate.get(ax.subject_iri, ()):
                    emit(Triple(value.subject_iri, ax.object.value, value.object),
                         "subproperty", (value, ax))
            for ax in by_predicate.get(str(OWL.inverseOf), ()):
                check()
                if ax.object.kind != "iri":
                    continue
                for forward, inverse in ((ax.subject_iri, ax.object.value),
                                         (ax.object.value, ax.subject_iri)):
                    for value in by_predicate.get(forward, ()):
                        if value.object.kind == "iri":
                            emit(iri_triple(value.object.value, inverse, value.subject_iri),
                                 "inverse", (value, ax))
            for ax in by_predicate.get(str(RDF.type), ()):
                check()
                if ax.object == RdfTerm("iri", str(OWL.SymmetricProperty)):
                    for value in by_predicate.get(ax.subject_iri, ()):
                        if value.object.kind == "iri":
                            emit(iri_triple(value.object.value, ax.subject_iri, value.subject_iri),
                                 "symmetric", (value, ax))
                elif ax.object == RdfTerm("iri", str(OWL.TransitiveProperty)):
                    values = by_predicate.get(ax.subject_iri, ())
                    for left in values:
                        for right in values:
                            check()
                            if left.object.kind == "iri" and left.object.value == right.subject_iri:
                                emit(Triple(left.subject_iri, ax.subject_iri, right.object),
                                     "transitive", (left, right, ax))
            check()
            if len(known) == before:
                break
    except _Exhausted:
        return InferenceResult(conclusions=(), proofs=(), input_watermark=inputs.input_watermark,
                               bundle_id=inputs.bundle.id, complete=False,
                               scope_digest=inputs.scope.digest() if inputs.scope else None)
    return InferenceResult(conclusions=ordered(inferred),
        proofs=tuple(sorted(proofs.values(), key=lambda p: str(p.id))),
        input_watermark=inputs.input_watermark, bundle_id=inputs.bundle.id, complete=True,
        scope_digest=inputs.scope.digest() if inputs.scope else None)


SUPPORTED_PROFILE = {"profile": "finite_explicit_axioms_v1", "enabled": True,
    "families": ["subclass", "type", "subproperty", "inverse", "symmetric", "transitive"]}


def _profile_descriptor(uow, targets, command):
    from sqlalchemy import select
    from lzo.errors import ApiError
    from lzo.governance.risk import ChangeDescriptor
    from lzo.ontology.models import catalog
    from lzo.ontology.drafts import definition_source_closure, head
    selected = uow.session.execute(select(catalog)).mappings().one()
    if command != {"operation": "inference.activate", "profile": SUPPORTED_PROFILE}:
        raise ApiError("INFERENCE_PROFILE_UNSUPPORTED", 422, "Unsupported inference configuration")
    if selected["active_bundle_id"] is None:
        raise ApiError("BUNDLE_NOT_ACTIVE", 409, "No published execution bundle")
    refs = definition_source_closure(uow, {selected["active_bundle_id"]})
    refs.add(selected["resource_id"])
    return ChangeDescriptor(kind="inference.activate",
        target_ids=(selected["resource_id"], *sorted(refs - {selected["resource_id"]}, key=str)),
        command=command, baseline={str(i): head(uow, i)["revision"] for i in refs},
        impact={"active_bundle_id": str(selected["active_bundle_id"]),
                "profile": SUPPORTED_PROFILE}, submit_action="schema.edit",
        review_action="schema.review", execute_action="schema.publish",
        operation_key="inference.activate", global_security_class="schema")


def preview_inference_profile(uow, actor):
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.models import catalog
    from sqlalchemy import select
    target = uow.session.execute(select(catalog.c.resource_id)).scalar_one()
    return ApprovalService(uow).preview(actor, "inference.activate", [target],
        {"operation": "inference.activate", "profile": SUPPORTED_PROFILE})


def activate_inference_profile(uow, actor, approval_id):
    """Independent F06 approval activates a new version, never edits genesis."""
    from uuid import uuid4
    from sqlalchemy import select, update
    from lzo.auth.permissions import permission_context
    from lzo.governance.approvals import ApprovalService
    from lzo.governance.revisions import append_revision
    from lzo.ontology.drafts import add_resource, bind_dependencies
    from lzo.ontology.models import catalog
    from lzo.ontology.releases import current_execution_bundle, create_execution_bundle
    from lzo.semantic.projection import ProjectionService
    with permission_context(uow):
        change = preview_inference_profile(uow, actor)
        ApprovalService(uow).require_executable(actor, approval_id, change.change_digest, change.baseline)
        selected = uow.session.execute(select(catalog).with_for_update()).mappings().one()
        old = current_execution_bundle(uow, actor)
        identifier = uuid4()
        add_resource(uow, identifier, "schema.configuration")
        config = append_revision(uow, actor, identifier, 0,
            {**SUPPORTED_PROFILE, "approval_id": str(approval_id)}, "inference.profile.created")
        # The definition config depends on its prior version; approval remains
        # history evidence and does not expose unrelated approval content.
        bind_dependencies(uow, identifier, {old.inference_version_id}, config.event_id)
        new = create_execution_bundle(uow, actor, old.schema_release_id, old.mapping_version_id,
            old.authority_version_id, old.ruling_policy_version_id, identifier, old.shapes_digest)
        refs = set(change.target_ids) | {identifier, new.id}
        from lzo.semantic.facts import revision_ref, json_value
        pins = tuple(revision_ref(uow, i) for i in sorted(refs - {selected["resource_id"]}, key=str))
        event = append_revision(uow, actor, selected["resource_id"], selected["revision"],
            {"bundle_id": str(new.id), "release_id": str(new.schema_release_id),
             "approval_id": str(approval_id), "dependency_revisions": json_value(pins)},
            "inference.profile.activated")
        uow.session.execute(update(catalog).values(
            revision=event.revision, active_bundle_id=new.id))
        ProjectionService(uow, None).schedule(actor, event)
        return new


def _profile_projection(uow, row):
    from lzo.semantic.projection import ProjectionContent
    from lzo.semantic.proofs import _ref
    pins = tuple(_ref(v) for v in row["payload"]["dependency_revisions"])
    return ProjectionContent({}, (row["resource_id"], *(p.resource_id for p in pins)), pins)


from lzo.governance.risk import register_policy
from lzo.semantic.projection import register_nonsemantic, register_projection
register_policy("inference.activate", _profile_descriptor)
register_nonsemantic("inference.profile.created", "inference-v1")
register_projection("inference.profile.activated", _profile_projection, version="inference-v1",
                    dirty_kind="rules", action="schema.publish")
