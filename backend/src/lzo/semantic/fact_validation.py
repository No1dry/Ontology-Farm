"""Bounded shape-guided context and freshness; S01 remains the only validator.

This module reads proposed/authoritative facts. It never writes a business
revision, commits a transaction, or evaluates SHACL constraints itself.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from uuid import UUID

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS, SH
from sqlalchemy import exists, select

from lzo.errors import ApiError
from lzo.semantic.rdf import RdfTerm, Triple
from lzo.semantic.semantica_adapter import _prepare, focused_shapes, root_key, shape_roots

MAX_NODES = 10000
MAX_STEPS = 20000
MAX_DEPTH = 32


def begin(uow):
    """A caller may compose several commands in one UoW; evidence is per command."""
    uow.facts_validation_results = {}
    uow.facts_validation_extra_refs = set()
    uow.facts_validation_business_refs = set()
    uow.facts_type_read_records = {}
    uow.facts_type_only_ids = set()
    uow.facts_full_ids = set()
    uow.facts_explicit_pins = set()


def _deactivated(shapes, shape):
    value = shapes.value(shape, SH.deactivated)
    return value is not None and value.toPython() is True


def _nested(shapes, shape):
    if _deactivated(shapes, shape):
        return []
    result = list(shapes.objects(shape, SH.node)) + list(shapes.objects(shape, SH["not"]))
    result += list(shapes.objects(shape, SH.qualifiedValueShape)) + list(shapes.objects(shape, SH.property))
    disjoint = shapes.value(shape, SH.qualifiedValueShapesDisjoint)
    if (disjoint is not None and disjoint.toPython() is True and
            (shapes.value(shape, SH.qualifiedMinCount) is not None or
             shapes.value(shape, SH.qualifiedMaxCount) is not None)):
        # SHACL 4.7.3 applies sibling qualifiers to THIS property's values,
        # regardless of whether the sibling property's own path has values.
        own = set(shapes.objects(shape, SH.qualifiedValueShape))
        siblings = {qualifier for parent in shapes.subjects(SH.property, shape)
                    for prop in shapes.objects(parent, SH.property)
                    for qualifier in shapes.objects(prop, SH.qualifiedValueShape)}
        result.extend(siblings - own)
    for operator in (SH["and"], SH["or"], SH.xone):
        for head in shapes.objects(shape, operator):
            try:
                result.extend(shapes.items(head))
            except ValueError:
                raise ApiError("VALIDATION_CONTEXT_INCOMPLETE", 422, "Malformed nested shape list") from None
    return result


def incoming(uow, target, predicates, as_of, *, include_future=False, exclude=None):
    """Authoritative structural witnesses; caller authorizes before using payload."""
    from lzo.semantic import facts as f
    if not predicates:
        return []
    query = select(f.assertions, f.objects.c.payload.label("subject_metadata")).join(
        f.objects, f.objects.c.id == f.assertions.c.resource_id).where(
        f.assertions.c.payload["state"].astext == "accepted",
        f.assertions.c.payload["object"]["kind"].astext == "iri",
        f.assertions.c.payload["object"]["value"].astext == str(target),
        f.assertions.c.payload["predicate_iri"].astext.in_(list(map(str, predicates))),
        f.objects.c.payload["state"].astext != "inactive",
        exists(select(f.supports.c.id).where(f.supports.c.assertion_id == f.assertions.c.id,
                                            f.supports.c.active.is_(True))))
    if exclude is not None:
        query = query.where(f.assertions.c.resource_id != exclude)
    rows = uow.session.execute(query.limit(MAX_STEPS+1)).mappings().all()
    if len(rows) > MAX_STEPS:
        raise ApiError("VALIDATION_CONTEXT_BUDGET", 422, "Validation context budget exceeded")
    return [r for r in rows if f.is_effective(r["payload"], as_of) or
            (include_future and datetime.fromisoformat(r["payload"]["valid_from"].replace("Z", "+00:00")) > as_of)]


@dataclass
class Context:
    uow: object
    actor: object
    identifier: UUID
    graph: object
    shapes: object
    values: tuple
    as_of: datetime

    def __post_init__(self):
        from lzo.semantic import facts as f
        self.primary = URIRef(f.object_iri(self.identifier))
        self.roots = shape_roots(self.shapes)
        self.target_predicates = set(self.shapes.objects(None, SH.targetObjectsOf))
        self.loaded = {self.primary}
        self.business_refs, self.reverse_refs = set(), set()
        self.full_heads, self.type_records = {}, {}
        self.boundaries = set()
        self.prepared_roots = set()
        self.selection = defaultdict(set)
        self.steps = 0
        self.witness_predicates = set()

    def budget(self, depth=0):
        self.steps += 1
        if self.steps > MAX_STEPS or len(self.loaded) > MAX_NODES or depth > MAX_DEPTH:
            raise ApiError("VALIDATION_CONTEXT_BUDGET", 422, "Validation context budget exceeded")

    def times(self, payload):
        for key in ("valid_from", "valid_to"):
            if payload.get(key):
                value = datetime.fromisoformat(payload[key].replace("Z", "+00:00"))
                if value > self.as_of:
                    self.boundaries.add(value)

    def reference(self, identifier, business):
        (self.business_refs if business else self.reverse_refs).add(identifier)

    def row(self, identifier, business):
        from lzo.semantic import facts as f
        row = f.get_object_row(self.uow, self.actor, identifier)
        self.reference(identifier, business)
        if identifier != self.identifier:
            self.full_heads[str(identifier)] = row["revision"]
            if not hasattr(self.uow, "facts_explicit_pins"):
                self.uow.facts_explicit_pins = set()
            self.uow.facts_explicit_pins.add(f.revision_ref(self.uow, identifier))
        return row

    def object_id(self, node):
        from lzo.semantic import facts as f
        return self.uow.session.execute(select(f.objects.c.id).where(f.objects.c.iri == str(node))).scalar_one_or_none()

    def load(self, node, business):
        from lzo.semantic import facts as f
        if not isinstance(node, URIRef):
            return
        identifier = self.object_id(node)
        if identifier is None or identifier == self.identifier:
            return
        row = self.row(identifier, business)
        if node in self.loaded:
            return
        self.loaded.add(node)
        self.budget()
        if row["payload"]["state"] == "inactive":
            return
        for cls in f.current_classes(self.uow,self.actor,identifier):
            self.graph.add((node, RDF.type, URIRef(cls)))
        for assertion in f.current_assertions(self.uow, identifier):
            self.budget()
            value = assertion["payload"]
            if value["state"] == "accepted":
                self.times(value)
                if f.is_effective(value, self.as_of):
                    self.graph.add(Triple(str(node), value["predicate_iri"], RdfTerm(**value["object"])).nodes())

    def witnesses(self, node, business):
        from lzo.semantic import facts as f
        # Every declared predicate is retained, including currently absent ones.
        for assertion in incoming(self.uow, node, self.target_predicates, self.as_of,
                                   include_future=True, exclude=self.identifier):
            self.budget()
            self.row(assertion["resource_id"], business)
            value = assertion["payload"]
            self.times(value)
            if f.is_effective(value, self.as_of):
                self.graph.add(Triple(f.object_iri(assertion["resource_id"]),
                    value["predicate_iri"], RdfTerm(**value["object"])).nodes())
        # The primary overlay already replaces its persisted outgoing facts.
        if node == self.primary:
            self.witness_predicates = {str(predicate) for predicate in self.target_predicates
                if any(self.graph.triples((None, predicate, node)))}

    def applicable(self, node):
        view = focused_shapes(self.graph, self.shapes, (str(node),))
        return set(view.subjects(SH.targetNode, node))

    def structural_roots(self, node):
        """Private candidate filtering, never permission-based target omission.

        Only selected roots are subsequently loaded/authorized and enter the
        report. Actual hidden applicable roots still fail that authorization.
        """
        from lzo.semantic import facts as f
        identifier = self.object_id(node)
        if identifier is None:
            return set()
        probe = Graph()
        for triple in self.graph.triples((None, RDFS.subClassOf, None)):
            probe.add(triple)
        classes = self.uow.session.execute(select(f.type_declarations.c.class_iris).where(
            f.type_declarations.c.resource_id == identifier)).scalar_one()
        for cls in classes:
            probe.add((node, RDF.type, URIRef(cls)))
        for assertion in f.current_assertions(self.uow, identifier):
            self.budget()
            value = assertion["payload"]
            if value["state"] == "accepted" and f.is_effective(value, self.as_of):
                probe.add(Triple(str(node), value["predicate_iri"], RdfTerm(**value["object"])).nodes())
        for assertion in incoming(self.uow, node, self.target_predicates, self.as_of,
                                   exclude=self.identifier):
            self.budget()
            value = assertion["payload"]
            probe.add(Triple(f.object_iri(assertion["resource_id"]), value["predicate_iri"],
                             RdfTerm(**value["object"])).nodes())
        for triple in self.graph.triples((self.primary, None, node)):
            probe.add(triple)
        view = focused_shapes(probe, self.shapes, (str(node),))
        return set(view.subjects(SH.targetNode, node))

    def forward(self, node, roots, business):
        from lzo.semantic import facts as f
        # Potential dependencies include qualifiers which the engine may never
        # evaluate. Only the genuine execution boundary can detect recursion.
        pending = [(node, root, 0) for root in roots]
        seen = set()
        while pending:
            value_node, shape, depth = pending.pop()
            if _deactivated(self.shapes, shape):
                continue
            key = (value_node, shape)
            if key in seen:
                continue
            seen.add(key)
            self.budget(depth)
            self.load(value_node, business)
            path = self.shapes.value(shape, SH.path)
            values = tuple(self.graph.objects(value_node, path)) if path is not None else (value_node,)
            if self.shapes.value(shape, SH["class"]) is not None:
                for value in values:
                    identifier = self.object_id(value) if isinstance(value, URIRef) else None
                    if identifier is not None:
                        classes, state, refs = f.confirmed_types(self.uow, self.actor, identifier)
                        (self.business_refs if business else self.reverse_refs).update(refs)
                        for cls in classes:
                            self.graph.add((value, RDF.type, URIRef(cls)))
            for value in values:
                pending.extend((value, child, depth+1)
                               for child in _nested(self.shapes, shape))
        return seen

    def reverse_roots(self, predicates):
        """Reverse shape/node pairs, not the unrestricted relationship closure."""
        from lzo.semantic import facts as f
        parents = defaultdict(set)
        shapes = set(self.shapes.subjects())
        for shape in shapes:
            for child in _nested(self.shapes, shape):
                parents[child].add(shape)
        seeds = set()
        for shape in shapes:
            if _deactivated(self.shapes, shape):
                continue
            dependent = {self.shapes.value(shape, SH.path)}
            for comparator in (SH.equals, SH.disjoint, SH.lessThan, SH.lessThanOrEquals):
                dependent.update(self.shapes.objects(shape, comparator))
            if (dependent & predicates or self.shapes.value(shape, SH.closed) is not None
                    or (RDF.type in predicates and self.shapes.value(shape, SH["class"]) is not None)):
                seeds.add((self.primary, shape, 0))
        pending, seen, found = list(seeds), set(), defaultdict(set)
        while pending:
            node, shape, depth = pending.pop()
            if (node, shape) in seen:
                continue
            self.budget(depth)
            seen.add((node, shape))
            if shape in self.roots and node != self.primary:
                found[node].add(shape)
            for parent in parents[shape]:
                path = self.shapes.value(parent, SH.path)
                if path is None:
                    pending.append((node, parent, depth+1))
                    continue
                for edge in incoming(self.uow, node, {path}, self.as_of, exclude=self.identifier):
                    # This is structural candidate discovery only. The selected
                    # root's complete forward traversal authorizes/pins its path.
                    self.budget()
                    pending.append((URIRef(f.object_iri(edge["resource_id"])), parent, depth+1))
                if any(self.graph.triples((self.primary, path, node))):
                    pending.append((self.primary, parent, depth+1))
        return found

    def build(self, changed_predicates, changed_values):
        from lzo.semantic import facts as f
        for value in self.values:
            if value.state == "accepted":
                self.times(value.model_dump(mode="json"))
        self.witnesses(self.primary, True)
        primary_roots = self.applicable(self.primary)
        self.forward(self.primary, primary_roots, True)
        self.prepared_roots.update((self.primary, shape) for shape in primary_roots)
        for value in changed_values:
            if URIRef(value.predicate_iri) not in self.target_predicates:
                continue
            if value.object.kind != "iri":
                raise ApiError("UNSUPPORTED_AFFECTED_TARGET", 422,
                               "Affected objects-of targets require a registered entity IRI")
            node = URIRef(value.object.value)
            if node == self.primary:
                continue
            if self.object_id(node) is None:
                raise ApiError("UNSUPPORTED_AFFECTED_TARGET", 422,
                               "Affected objects-of targets require a registered entity IRI")
            self.load(node, True)
            self.witnesses(node, True)
            roots = {root for root in self.applicable(node) if
                     (root, SH.targetObjectsOf, URIRef(value.predicate_iri)) in self.shapes}
            self.selection[node].update(roots)
            self.forward(node, roots, True)
            self.prepared_roots.update((node, shape) for shape in roots)
        for node, candidates in self.reverse_roots(changed_predicates).items():
            roots = candidates & self.structural_roots(node)
            if not roots:
                continue
            self.load(node, False)
            self.witnesses(node, False)
            if not roots <= self.applicable(node):
                raise ApiError("VALIDATION_CONTEXT_INCOMPLETE", 422, "Validation root context changed")
            self.selection[node].update(roots)
            self.forward(node, roots, False)
            self.prepared_roots.update((node, shape) for shape in roots)
        # Incoming witnesses were complete before traversal. The fixed primary
        # overlay prevents nested loading from introducing an unprepared root.
        final = {(self.primary, shape) for shape in self.applicable(self.primary)}
        for node, allowed in self.selection.items():
            final.update((node, shape) for shape in self.applicable(node) & allowed)
        if not final <= self.prepared_roots:
            raise ApiError("VALIDATION_CONTEXT_INCOMPLETE", 422, "Validation root context changed")
        selected = {str(node): tuple(sorted(root_key(shape) for shape in roots))
                    for node, roots in self.selection.items() if roots}
        focus = (str(self.primary), *sorted(selected))
        # Type declarations are immutable and have their own precise provenance.
        evidence = {"full_heads": self.full_heads, "types": dict(getattr(self.uow, "facts_type_read_records", {})),
            "target_predicates": sorted(map(str, self.target_predicates)),
            "incoming_predicates": sorted(self.witness_predicates),
            "next_boundary": min(self.boundaries).isoformat() if self.boundaries else None,
            "evaluation_time": self.as_of.isoformat()}
        return focus, selected or None, evidence


def prepare(uow, actor, identifier, graph, shape_text, bundle, values, as_of, changed_predicates, changed_values):
    shapes, _ = _prepare(shape_text, str(bundle.schema_release_id), shapes=True)
    context = Context(uow, actor, identifier, graph, shapes, tuple(values), as_of)
    focus, selection, evidence = context.build(set(map(URIRef, changed_predicates)), changed_values)
    return context, focus, selection, evidence


def incoming_signature(uow, identifier, predicates, as_of):
    from lzo.semantic import facts as f
    return sorted({row["payload"]["predicate_iri"] for row in
                   incoming(uow, URIRef(f.object_iri(identifier)), set(map(URIRef, predicates)), as_of)})


def incoming_stamp(uow, identifier, predicates, as_of):
    """Opaque potential-witness stamp also detects the first future incoming edge."""
    from lzo.semantic import facts as f
    values = sorted((str(row["id"]), row["payload"]["valid_from"], row["payload"]["valid_to"] or "")
        for row in incoming(uow, URIRef(f.object_iri(identifier)), set(map(URIRef, predicates)),
                            as_of, include_future=True))
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def is_fresh(uow, actor, row, snapshot, *, candidate=False):
    """Failure returns no current report; protected evidence IDs never reach HTTP."""
    from lzo.semantic import facts as f
    from lzo.ontology.models import catalog
    evidence = snapshot.get("candidate_validation_evidence" if candidate else "validation_evidence")
    if evidence is None:
        return False
    try:
        active = uow.session.execute(select(catalog.c.active_bundle_id)).scalar_one_or_none()
        if active != row["bundle_id"]:
            return False
        for identifier in evidence["reference_ids"]:
            f.authorize(uow, actor, UUID(identifier))
        for identifier, revision in evidence["full_heads"].items():
            current = f.get_object_row(uow, actor, UUID(identifier))
            if current["revision"] != revision:
                return False
        for identifier, declaration in evidence["types"].items():
            current = uow.session.execute(select(f.type_declarations).where(
                f.type_declarations.c.resource_id == UUID(identifier))).mappings().first()
            if current is None or f.json_value(dict(current)) != declaration:
                return False
            for pin in (*current["dependency_refs"], current["revision_ref"]):
                f.authorize(uow, actor, UUID(pin["resource_id"]))
        boundary = evidence["next_boundary"]
        if boundary and datetime.fromisoformat(boundary) <= f.evaluation_time(uow):
            return False
        return (evidence["incoming_predicates"] == incoming_signature(
            uow, row["id"], evidence["target_predicates"], f.evaluation_time(uow))
            and evidence["incoming_stamp"] == incoming_stamp(
                uow, row["id"], evidence["target_predicates"], f.evaluation_time(uow)))
    except ApiError:
        return False
