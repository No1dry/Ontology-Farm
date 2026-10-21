"""Offline semantic support inspection, typed definitions and inherited forms."""
from dataclasses import dataclass, field
import json
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD
from lzo.errors import ApiError
from lzo.semantic.files import bounded_json, dataset_from_nquads
from lzo.semantic.rdf import iri, term_from_node
from lzo.semantic.semantica_adapter import SH, CORE


@dataclass(frozen=True)
class ProfileInspection:
    allowed: bool
    codes: tuple[str, ...]
    locations: tuple[dict[str, str], ...] = ()
    graphs: dict[str, int] = field(default_factory=dict)


class SemanticProfileError(ApiError):
    """Authorized source diagnostics, intentionally returned to its current viewer."""


def parse(raw, format="turtle"):
    return dataset_from_nquads(bounded_json("lzo.semantic.files",
        {"operation": "parse", "raw": raw, "format": format})["nquads"])


def union(dataset):
    graph = Graph()
    for s, p, o, _ in dataset.quads():
        graph.add((s, p, o))
    return graph


def effective_shapes(document):
    """Compile definition constraints onto declared direct-path form fields."""
    from rdflib import Dataset
    from rdflib.collection import Collection
    from lzo.semantic.files import nquads
    graph = union(parse(document.shapes))
    properties = {p.iri: p for p in document.properties}
    relations = {p.iri: p for p in document.relations}
    for node, path in list(graph.subject_objects(SH.path)):
        definition = properties.get(str(path))
        if definition:
            existing = set(graph.objects(node, SH.datatype))
            datatype = URIRef(definition.datatype_iri)
            if existing and existing != {datatype}:
                raise ApiError("INHERITANCE_CONFLICT", 422, "Definition and Shape datatype conflict")
            graph.add((node, SH.datatype, datatype))
            if definition.enum_values:
                values = {value.node() for value in definition.enum_values}
                for original in graph.objects(node, SH["in"]):
                    values.intersection_update(graph.items(original))
                if not values:
                    raise ApiError("INHERITANCE_CONFLICT", 422, "Definition and Shape enumeration conflict")
                graph.remove((node, SH["in"], None))
                collection = BNode()
                Collection(graph, collection, sorted(values, key=lambda v: v.n3()))
                graph.add((node, SH["in"], collection))
        relation = relations.get(str(path))
        if relation:
            for target in relation.range_iris:
                graph.add((node, SH["class"], URIRef(target)))
    dataset = Dataset()
    for triple in graph:
        dataset.default_graph.add(triple)
    canonical = bounded_json("lzo.semantic.files", {"operation": "canonical", "nquads": nquads(dataset)})["nquads"]
    return canonical or "# No constraints\n"


class SemanticProfile:
    def inspect_jsonld(self, document):
        codes = set()
        def walk(value):
            if isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key == "@import" or (key == "@context" and
                            any(isinstance(x, str) for x in (item if isinstance(item, list) else [item]))):
                        codes.add("REMOTE_CONTEXT_FORBIDDEN")
                    walk(item)
        walk(document)
        return ProfileInspection(not codes, tuple(sorted(codes)))

    def inspect_text(self, raw, format, kind):
        if format == "json-ld":
            inspected = self.inspect_jsonld(json.loads(raw))
            if not inspected.allowed:
                return inspected
        try:
            dataset = parse(raw, format)
        except ValueError as error:
            code = "DEPENDENCY_UNBUNDLED" if "imports" in str(error) else "SEMANTIC_PARSE_FAILED"
            return ProfileInspection(False, (code,))
        return self.inspect_dataset(dataset, kind)

    def inspect_dataset(self, dataset, kind):
        locations, graphs, codes = [], {}, set()
        predicates = {RDF.type, RDFS.label, RDFS.comment, RDFS.subClassOf,
                      RDFS.subPropertyOf, RDFS.domain, RDFS.range, OWL.inverseOf, OWL.deprecated}
        types = {OWL.Class, RDFS.Class, OWL.ObjectProperty, OWL.DatatypeProperty,
                 OWL.SymmetricProperty, OWL.TransitiveProperty, OWL.Ontology}
        for s, p, o, context in sorted(dataset.quads(), key=lambda q: tuple(map(str, q))):
            name = "@default" if context == dataset.default_graph.identifier else str(context)
            graphs[name] = graphs.get(name, 0) + 1
            code = None
            if kind == "schema":
                if p not in predicates or (p == RDF.type and o not in types):
                    code = "UNSUPPORTED_OWL_AXIOM"
                elif p not in {RDFS.label, RDFS.comment, OWL.deprecated} and (
                        not isinstance(s, URIRef) or not isinstance(o, URIRef)):
                    code = "UNSUPPORTED_OWL_EXPRESSION"
            elif kind == "shapes":
                if p == RDFS.subClassOf:
                    code = "UNSUPPORTED_IMPLICIT_CLASS_TARGET"
                if p == SH.path and not isinstance(o, URIRef):
                    code = "COMPLEX_SHACL_PATH_UNSUPPORTED"
                if any(isinstance(x, URIRef) and str(x).startswith(str(SH)) and
                       str(x)[len(str(SH)):] not in CORE for x in (p, o)):
                    code = "UNSUPPORTED_SHACL_CONSTRAINT"
                if not code and not (p in {RDF.type, RDF.first, RDF.rest, RDFS.label, RDFS.comment}
                        or str(p).startswith(str(SH))):
                    code = "UNSUPPORTED_SHACL_EXTENSION"
            else:
                raise ValueError("Unknown semantic document kind")
            if code:
                codes.add(code)
                locations.append({"graph": name, "subject": str(s), "predicate": str(p),
                                  "object": str(o), "code": code})
        return ProfileInspection(not codes, tuple(sorted(codes)), tuple(locations), graphs)


def ancestors(document, class_iri):
    classes = {value.iri: value for value in document.classes}
    if class_iri not in classes:
        raise ApiError("CLASS_NOT_FOUND", 404, "Class not found")
    return _ancestors({k: value.parents for k, value in classes.items()}, class_iri)


def _ancestors(edges, identifier):
    result, active, complete = set(), set(), set()
    pending = [(identifier, False)]
    while pending:
        node, exiting = pending.pop()
        if exiting:
            active.remove(node)
            complete.add(node)
            continue
        if node in active:
            raise ApiError("INHERITANCE_CYCLE", 422, "Definition inheritance contains a cycle")
        if node not in edges:
            raise ApiError("DEPENDENCY_MISSING", 422, "Definition dependency is missing")
        if node in complete:
            continue
        active.add(node)
        pending.append((node, True))
        for parent in edges[node]:
            result.add(parent)
            pending.append((parent, False))
    return tuple(sorted(result))


def _ui_fields(document):
    fields = document.ui_metadata.get("fields", {})
    if not isinstance(fields, dict) or any(not isinstance(value, dict) for value in fields.values()):
        raise ApiError("SEMANTIC_DOCUMENT_INVALID", 422, "UI fields and field entries must be objects")
    return fields


def form_schema(document, class_iri, release_id=None, *, graph=None):
    from lzo.ontology.schemas import FormField, FormSchemaView
    ui_fields = _ui_fields(document)
    inherited = ancestors(document, class_iri)
    graph = graph if graph is not None else union(parse(document.shapes))
    properties = {p.iri: p for p in document.properties}
    relations = {p.iri: p for p in document.relations}
    constraints, sources = {}, {}
    for target in (*inherited, class_iri):
        for shape in graph.subjects(SH.targetClass, URIRef(target)):
            for prop in graph.objects(shape, SH.property):
                path = graph.value(prop, SH.path)
                if not isinstance(path, URIRef):
                    raise ApiError("FORM_PATH_UNSUPPORTED", 422, "Form fields need direct IRI paths")
                key = str(path)
                constraints.setdefault(key, []).append(prop)
                if target != class_iri:
                    sources.setdefault(key, set()).add(target)
    fields = []
    for key, nodes in sorted(constraints.items()):
        definition = properties.get(key) or relations.get(key)
        minimum = max([int(v) for n in nodes for v in graph.objects(n, SH.minCount)] or [0])
        maxima = [int(v) for n in nodes for v in graph.objects(n, SH.maxCount)]
        maximum = min(maxima) if maxima else None
        datatypes = {str(v) for n in nodes for v in graph.objects(n, SH.datatype)}
        if key in properties:
            datatypes.add(properties[key].datatype_iri)
        if minimum < 0 or (maximum is not None and maximum < minimum) or len(datatypes) > 1:
            raise ApiError("INHERITANCE_CONFLICT", 422, "Inherited cardinality or datatype conflict")
        ranges = {str(v) for n in nodes for v in graph.objects(n, SH["class"])}
        if key in relations:
            ranges.update(relations[key].range_iris)
        enums = [set(term_from_node(v) for v in graph.items(head))
                 for n in nodes for head in graph.objects(n, SH["in"])]
        if key in properties and properties[key].enum_values:
            enums.append(set(properties[key].enum_values))
        values = set.intersection(*enums) if enums else set()
        if enums and not values:
            raise ApiError("INHERITANCE_CONFLICT", 422, "Inherited enumeration conflict")
        ui = ui_fields.get(key, {})
        fields.append(FormField(iri=key, label=definition.label if definition else key,
            datatype_iri=next(iter(datatypes)) if datatypes else None,
            reference_classes=tuple(sorted(ranges)), required=minimum > 0, min_count=minimum,
            max_count=maximum, unit=properties[key].unit if key in properties else None,
            enum_values=tuple(sorted(values, key=repr)), read_only=bool(ui.get("read_only", False)),
            widget=ui.get("widget", "reference" if ranges else "select" if values else "text")))
    return FormSchemaView(class_iri=class_iri, release_id=release_id, fields=tuple(fields),
                          inherited_from={k: tuple(sorted(v)) for k, v in sources.items()})


def validate_document(document):
    try:
        return _validate_document(document)
    except (ValueError, TypeError) as error:
        raise ApiError("SEMANTIC_DOCUMENT_INVALID", 422, "Invalid semantic document") from error


def _validate_document(document):
    from lzo.semantic.files import MAX_BYTES
    if len(document.model_dump_json().encode()) > MAX_BYTES:
        raise ApiError("SEMANTIC_DOCUMENT_TOO_LARGE", 413, "Semantic document exceeds the configured byte budget")
    _ui_fields(document)
    definitions = (*document.classes, *document.properties, *document.relations)
    identifiers = [value.iri for value in definitions]
    if len(set(identifiers)) != len(identifiers):
        raise ApiError("IRI_CONFLICT", 422, "Definition IRIs must be unique")
    for value in identifiers:
        iri(value)
    for value in document.properties:
        iri(value.datatype_iri)
        iri(value.owner_policy_iri)
    known_properties = {p.iri for p in (*document.properties, *document.relations)}
    inheritance = {p.iri: p.superproperties for p in (*document.properties, *document.relations)}
    for value in (*document.properties, *document.relations):
        if set(value.superproperties) - known_properties:
            raise ApiError("DEPENDENCY_MISSING", 422, "Property dependency is missing")
        _ancestors(inheritance, value.iri)
    classes = {c.iri for c in document.classes}
    for relation in document.relations:
        if set((*relation.domain_iris, *relation.range_iris)) - classes:
            raise ApiError("DEPENDENCY_MISSING", 422, "Relation endpoint class is missing")
        if relation.inverse_iri and relation.inverse_iri not in {r.iri for r in document.relations}:
            raise ApiError("DEPENDENCY_MISSING", 422, "Inverse relation is missing")
    graph = parse(document.shapes)
    support = SemanticProfile().inspect_dataset(graph, "shapes")
    if not support.allowed:
        raise ApiError("SEMANTIC_PROFILE_UNSUPPORTED", 422, "Unsupported SHACL profile",
                       {"codes": support.codes, "locations": support.locations})
    graph = union(graph)
    for cls in document.classes:
        form_schema(document, cls.iri, graph=graph)
    return document
