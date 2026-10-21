"""Genuine Semantica validation with an exact RDF report companion pass."""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import inspect
import json
import sys

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from lzo.contracts import ExecutionBundle
from lzo.semantic.files import bounded_json, deny_network, parse_text, nquads, _canonical, dataset_from_nquads
from lzo.semantic.files import SemanticBudgetExceeded, SemanticDependencyUnavailable, SemanticRecursionIncomplete
from lzo.semantic.rdf import RdfTerm, SKOLEM, term_from_node, iri
from lzo.semantic.validation import ValidationIssue, ValidationReport

SH = Namespace("http://www.w3.org/ns/shacl#")
# SHACL Core only. Complex paths and arbitrary extension predicates have no
# exact path_iri contract yet and fail explicitly, rather than being ignored.
CORE = set("""NodeShape PropertyShape targetClass targetNode targetSubjectsOf targetObjectsOf
property path class datatype nodeKind minCount maxCount minExclusive minInclusive maxExclusive
maxInclusive minLength maxLength pattern flags languageIn uniqueLang equals disjoint lessThan
lessThanOrEquals not and or xone node qualifiedValueShape qualifiedMinCount qualifiedMaxCount
qualifiedValueShapesDisjoint closed ignoredProperties hasValue in severity message name description
order group deactivated Violation Warning Info IRI BlankNode Literal BlankNodeOrIRI
BlankNodeOrLiteral IRIOrLiteral""".split())


def inspect_implicit_targets(graph):
    # The shared published profile never supported shape-side class hierarchy.
    # Upstream implicit-class targeting handles only one hop; reject clearly.
    if any(graph.triples((None, RDFS.subClassOf, None))):
        raise ValueError("UNSUPPORTED_IMPLICIT_CLASS_TARGET: use explicit sh:targetClass")


def root_key(node):
    """Identity after original _prepare; never a recanonicalized invocation view."""
    return "_:" + str(node) if isinstance(node, BNode) else str(node)


def shape_roots(shapes):
    roots = {subject for target in (SH.targetNode, SH.targetClass, SH.targetSubjectsOf, SH.targetObjectsOf)
             for subject in shapes.subjects(target, None)}
    return roots | set(shapes.subjects(RDF.type, RDFS.Class))


def focused_shapes(data, shapes, focus_iris, root_selection=None):
    """Root target intersection; preserve nested constraints and original IDs."""
    view = Graph()
    for triple in shapes:
        view.add(triple)
    focus = {iri(value) for value in focus_iris}
    targets = (SH.targetNode, SH.targetClass, SH.targetSubjectsOf, SH.targetObjectsOf)
    roots = shape_roots(shapes)
    if root_selection is not None:
        known = {root_key(root) for root in roots}
        if (not set(root_selection) <= set(focus_iris) or
                any(not isinstance(keys, (list, tuple)) or not keys or len(set(keys)) != len(keys)
                    or not set(keys) <= known for keys in root_selection.values())):
            raise ValueError("Invalid trusted root selection")
    for shape in roots:
        matching = set(shapes.objects(shape, SH.targetNode))
        classes = set(shapes.objects(shape, SH.targetClass))
        if (shape, RDF.type, RDFS.Class) in shapes:
            classes.add(shape)
        for node in focus:
            confirmed = set(data.objects(node, RDF.type))
            closure, frontier = set(confirmed), set(confirmed)
            while frontier:
                parents = {parent for cls in frontier for parent in data.objects(cls, RDFS.subClassOf)}
                frontier = parents - closure
                closure.update(parents)
            if classes & closure:
                matching.add(node)
            if any(any(data.triples((node, predicate, None))) for predicate in shapes.objects(shape, SH.targetSubjectsOf)):
                matching.add(node)
            if any(any(data.triples((None, predicate, node))) for predicate in shapes.objects(shape, SH.targetObjectsOf)):
                matching.add(node)
        if root_selection is not None and any(
                root_key(shape) in keys and iri(node) not in matching
                for node, keys in root_selection.items()):
            raise ValueError("Invalid trusted root selection: root is not applicable")
        for target in targets:
            view.remove((shape, target, None))
        view.remove((shape, RDF.type, RDFS.Class))
        for node in matching & focus:
            if root_selection is None or str(node) not in root_selection or root_key(shape) in root_selection[str(node)]:
                view.add((shape, SH.targetNode, node))
    return view


def _prepare(raw, scope, *, shapes=False):
    # This adapter consumes trusted reconstructed logical graphs. Artifact
    # ingestion keeps the stricter reserved-skolem check.
    dataset = parse_text(raw, "turtle", allow_platform_skolem=True)
    canonical = _canonical(nquads(dataset))
    graph = dataset_from_nquads(canonical).default_graph
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if shapes:
        inspect_implicit_targets(graph)
        for s, p, o in graph:
            for node in (p, o):
                if isinstance(node, URIRef) and str(node).startswith(str(SH)) and str(node)[len(str(SH)):] not in CORE:
                    raise ValueError("Executable or unsupported SHACL profile")
            if p == SH.path and not isinstance(o, URIRef):
                raise ValueError("Complex SHACL paths exceed the supported profile")
    # Scope labels without changing node kind, including anonymous shapes and
    # RDF-list members. Skolem IRIs exist only in the returned report mapping.
    nodes = {node for triple in graph for node in triple if isinstance(node, BNode)}
    scope_key = hashlib.sha256(scope.encode()).hexdigest()
    mapping = {node: BNode(f"b{scope_key}{digest}{node}") for node in nodes}
    locations = {mapped: URIRef(f"{SKOLEM}{scope}:{digest}:{node}") for node, mapped in mapping.items()}
    output = Graph()
    for s, p, o in graph:
        output.add((mapping.get(s, s), p, mapping.get(o, o)))
    return output, locations


def _expanded_jsonld(graph):
    """Explicit JSON-LD nodes retain labels, lists and exact literal strings.

    Turtle/N-Triples parsers allocate fresh blank-node IDs. JSON-LD's explicit
    @id lets the public Semantica parser and companion use the same scoped
    labels, without rewriting blank nodes to IRIs or compacting RDF lists.
    """
    def identifier(node):
        return "_:" + str(node) if isinstance(node, BNode) else str(node)
    subjects = {}
    for subject, predicate, value in graph:
        record = subjects.setdefault(identifier(subject), {"@id": identifier(subject)})
        if isinstance(value, (URIRef, BNode)):
            encoded = {"@id": identifier(value)}
        else:
            encoded = {"@value": str(value)}
            if value.language:
                encoded["@language"] = value.language
            elif value.datatype:
                encoded["@type"] = str(value.datatype)
        record.setdefault(str(predicate), []).append(encoded)
    return json.dumps(list(subjects.values()), ensure_ascii=False)


def _identity(focus, path, shape, constraint, severity, value):
    return (focus, path or "", shape or "", constraint, severity, value if value is not None else "")


@contextmanager
def _finite_evaluation(enabled):
    """Child-local finite profile; constraints and target resolution stay genuine."""
    if not enabled:
        yield
        return
    import pyshacl
    from pyshacl.shape import Shape
    from pyshacl.constraints.constraint_component import ConstraintComponent
    original_validate = Shape.validate
    original_triggers = ConstraintComponent.recursion_triggers
    fingerprints = (
        (original_validate, "e7e61df1a42f5bf307b1c6280074eacb77eaa38cf60112a02c247bce359d80f0"),
        (original_triggers, "5c1095f4858bbc492cf7b5bc82802dabf30d4ed75b0fdbed0fc2279dc3c9199e"),
    )
    if pyshacl.__version__ != "0.30.1" or any(
            hashlib.sha256(inspect.getsource(method).encode()).hexdigest() != expected
            for method, expected in fingerprints):
        raise SemanticDependencyUnavailable("Finite validation engine compatibility mismatch")
    active = set()
    steps = 0

    def validate(shape, executor, target_graph, focus=None, _evaluation_path=None):
        nonlocal steps
        if shape.deactivated:
            return True, []
        if focus is None:
            resolver = shape.focus_nodes_sparql if executor.sparql_mode else shape.focus_nodes
            focuses = list(resolver(target_graph, debug=executor.debug))
        else:
            focuses = [focus] if isinstance(focus, (URIRef, BNode, Literal)) else list(focus)
        if executor.focus_nodes:
            focuses = [node for node in focuses if isinstance(node, URIRef) and node in executor.focus_nodes]
        conforms, reports = True, []
        for node in focuses:
            key = (shape.node, node)
            if key in active:
                raise SemanticRecursionIncomplete("Recursive shape evaluation exceeds the supported profile")
            if _evaluation_path is not None and len(_evaluation_path) // 2 >= executor.max_validation_depth:
                raise SemanticBudgetExceeded("Semantic validation depth budget exceeded")
            steps += 1
            if steps > 20000:
                raise SemanticBudgetExceeded("Semantic validation step budget exceeded")
            active.add(key)
            try:
                valid, results = original_validate(shape, executor, target_graph, focus=node,
                    _evaluation_path=list(_evaluation_path) if _evaluation_path is not None else None)
            finally:
                active.remove(key)
            conforms = conforms and valid
            reports.extend(results)
            if not conforms and executor.abort_on_first:
                break
        return conforms, reports

    def recursion_triggers(component, _evaluation_path, trigger_depth=3):
        # Retain upstream path-invariant checks, but its shape-only heuristic
        # cannot truncate a finite chain before the per-data-node guard runs.
        original_triggers(component, _evaluation_path, trigger_depth)
        return None

    Shape.validate = validate
    ConstraintComponent.recursion_triggers = recursion_triggers
    try:
        yield
    finally:
        Shape.validate = original_validate
        ConstraintComponent.recursion_triggers = original_triggers


def execute_validation(payload):
    # Public imports are intentionally ordinary and mandatory.
    from semantica.ontology import run_shacl_validation
    import pyshacl
    data, data_locations = _prepare(payload["data"], "validation-data")
    shapes, shape_locations = _prepare(payload["shapes"], payload["scope"], shapes=True)
    locations = data_locations | shape_locations
    if payload.get("focus_iris") is not None:
        shapes = focused_shapes(data, shapes, payload["focus_iris"], payload.get("root_selection"))
    with _finite_evaluation(payload.get("finite_evaluation", False)):
        genuine = run_shacl_validation(_expanded_jsonld(data), _expanded_jsonld(shapes),
                                      data_graph_format="json-ld", shacl_format="json-ld")
        conforms, report, text = pyshacl.validate(data, shacl_graph=shapes, inference="none",
                                                 abort_on_first=False)
    if not isinstance(report, Graph) or bool(conforms) != genuine.conforms:
        raise ValueError("Semantica report mismatch")
    issues, companion_identities = [], []
    for node in report.subjects(RDF.type, SH.ValidationResult):
        focus = report.value(node, SH.focusNode)
        path = report.value(node, SH.resultPath)
        shape = report.value(node, SH.sourceShape)
        constraint = report.value(node, SH.sourceConstraintComponent)
        severity = str(report.value(node, SH.resultSeverity)).removeprefix(str(SH))
        value = report.value(node, SH.value)
        if not isinstance(focus, (URIRef, BNode)) or (path is not None and not isinstance(path, URIRef)):
            raise ValueError("Validation location exceeds the supported IRI profile")
        def report_node(node):
            if isinstance(node, BNode):
                if node not in locations:
                    raise ValueError("Unscoped validation report node")
                return locations[node]
            return node
        if shape is not None and not isinstance(shape, (URIRef, BNode)):
            raise ValueError("Invalid validation shape")
        if severity not in ("Violation", "Warning", "Info"):
            raise ValueError("Unsupported validation severity profile")
        issues.append(ValidationIssue(str(report_node(focus)), str(path) if path else None,
            str(report_node(shape)) if shape else None, str(constraint), severity,
            str(report.value(node, SH.resultMessage) or ""),
            term_from_node(report_node(value)) if value is not None else None))
        companion_identities.append(_identity(str(focus), str(path) if path else None,
            str(shape) if shape else None, str(constraint).removeprefix(str(SH)),
            severity, str(value) if value is not None else None))
    genuine_identities = [_identity(v.focus_node, v.result_path, v.shape, v.constraint, v.severity, v.value)
                          for v in (*genuine.violations, *genuine.warnings, *genuine.infos)]
    if sorted(genuine_identities) != sorted(companion_identities):
        raise ValueError("Semantica report mismatch")
    return {"conforms": genuine.conforms,
            "issues": [asdict(issue) for issue in sorted(issues, key=lambda issue: repr(issue))]}


class SemanticaAdapter:
    def __init__(self, *, timeout_seconds=20):
        self.timeout_seconds = timeout_seconds

    def validate(self, data_ttl: str, shapes_ttl: str, bundle: ExecutionBundle, *,
                 focus_iris: tuple[str, ...] | None = None,
                 root_selection: dict[str, tuple[str, ...]] | None = None,
                 finite_evaluation: bool = False) -> ValidationReport:
        if type(finite_evaluation) is not bool:
            raise ValueError("Invalid trusted finite evaluation mode")
        if bundle.shapes_digest and hashlib.sha256(shapes_ttl.encode()).hexdigest() != bundle.shapes_digest:
            raise ValueError("Execution bundle shapes digest mismatch")
        if focus_iris is not None:
            if not focus_iris or len(focus_iris) > 10000 or len(set(focus_iris)) != len(focus_iris):
                raise ValueError("Invalid focused validation scope")
            for value in focus_iris:
                iri(value)
        elif root_selection is not None:
            raise ValueError("Invalid trusted root selection without focus")
        result = bounded_json(__name__, {"data": data_ttl, "shapes": shapes_ttl,
            "scope": str(bundle.schema_release_id), "focus_iris": focus_iris,
            "root_selection": root_selection, "finite_evaluation": finite_evaluation},
            timeout_seconds=self.timeout_seconds)
        issues = []
        for item in result["issues"]:
            item["value"] = RdfTerm(**item["value"]) if item["value"] is not None else None
            issues.append(ValidationIssue(**item))
        return ValidationReport(result["conforms"], tuple(issues), None, bundle.id,
            focus_iris, "focused" if focus_iris is not None else "dataset", bundle.shapes_digest or None,
            root_selection)


def main():
    deny_network()
    try:
        print(json.dumps(execute_validation(json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"type": "ImportError" if isinstance(error, ImportError) else type(error).__name__,
                          "error": str(error) if isinstance(error, ValueError) else "Semantic validation failed"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
