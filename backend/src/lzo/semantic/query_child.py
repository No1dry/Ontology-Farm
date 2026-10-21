"""Fixed, offline SPARQL evaluation entry point. No user plugin/loader selection."""
import sys
import os

# Fixed bootstrap, before query parsing/input or evaluator imports. The Windows
# supervisor attaches its owned Job Object before sending any input.
if sys.platform.startswith("linux"):
    try:
        import resource
        memory = int(os.environ["LZO_QUERY_CHILD_MEMORY_BYTES"])
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ImportError, KeyError, OSError, ValueError):
        sys.stdout.write('{"error":"SPARQL_CAPABILITY_UNAVAILABLE"}')
        sys.exit(0)


def confinement(event, args):
    if (event.startswith("socket.") or event in {"urllib.Request", "subprocess.Popen",
            "os.system", "os.exec", "os.posix_spawn"}):
        raise PermissionError("Query child external access denied")


# Installed before any SPARQL plugin import. Ordinary Python network and child
# process APIs are denied; this is not a kernel-level network isolation claim.
sys.addaudithook(confinement)

import json

# NT acquisition needs no SPARQL evaluator imports. Both fixed operations run
# after the same confinement/bootstrap and under the same owned supervisor.
_payload = None
if __name__ == "__main__":
    try:
        _payload = json.load(sys.stdin)
        if _payload.get("operation") == "parse_nt":
            from lzo.semantic.nt import parse_records
            try:
                _result = parse_records(_payload)
            except (MemoryError, RecursionError):
                _result = {"error": "SPARQL_BUDGET_EXCEEDED"}
            except Exception:
                _result = {"error": "RDF_REPRESENTATION_INVALID"}
            sys.stdout.write(json.dumps(_result, ensure_ascii=False))
            sys.exit(0)
    except (MemoryError, RecursionError):
        sys.stdout.write('{"error":"SPARQL_BUDGET_EXCEEDED"}')
        sys.exit(0)

from dataclasses import asdict
from uuid import uuid4
from rdflib import BNode, Dataset, URIRef, Variable
from rdflib.paths import Path
from rdflib.plugins.stores.memory import Memory
from pyparsing import ParseResults
import rdflib.plugins.sparql as sparql

sparql.SPARQL_DEFAULT_GRAPH_UNION = False
sparql.SPARQL_LOAD_GRAPHS = False
if sparql.CUSTOM_EVALS:
    raise RuntimeError("Unexpected query evaluator plugin")

from rdflib.plugins.sparql import operators
from rdflib.plugins.sparql.parser import parseQuery, parseUpdate
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.evaluate import evalQuery, evalPart, _fillTemplate
from lzo.semantic.rdf import canonical_query, term_from_node

CASTS = {URIRef("http://www.w3.org/2001/XMLSchema#" + s)
         for s in ("string", "dateTime", "float", "double", "decimal", "integer", "boolean")}
if set(operators._CUSTOM_FUNCTIONS) != CASTS:
    raise RuntimeError("Unexpected query function plugin")


class Rejected(Exception):
    def __init__(self, code):
        self.code = code


def fail(code="SPARQL_BUDGET_EXCEEDED"):
    raise Rejected(code)


class Work(Memory):
    def __init__(self, limit):
        super().__init__()
        self.remaining, self.enabled = limit, False

    def charge(self):
        if self.enabled:
            self.remaining -= 1
            if self.remaining < 0:
                fail()

    def triples(self, pattern, context=None):
        self.charge()  # unsuccessful probes consume work too
        for value in super().triples(pattern, context):
            self.charge()
            yield value

    def contexts(self, triple=None):
        self.charge()
        for context in super().contexts(triple):
            self.charge()
            yield context


def inspect(value, limit):
    stack, count = [(value, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > limit or depth > 64:
            fail()
        name = getattr(node, "name", None)
        if name in {"ServiceGraphPattern", "DatasetClause", "Update"}:
            fail("SPARQL_OPERATION_FORBIDDEN")
        if name == "Function" and isinstance(node["iri"], URIRef) and node["iri"] not in CASTS:
            fail("SPARQL_OPERATION_FORBIDDEN")
        if isinstance(node, dict):
            children = list(node.values())
            if name in {"Builtin_EXISTS", "Builtin_NOTEXISTS"} and "graph" in vars(node):
                children.append(vars(node)["graph"])
        elif isinstance(node, (tuple, list, ParseResults)):
            children = node
        elif isinstance(node, Path):
            children = vars(node).values()
        else:
            continue
        stack.extend((child, depth+1) for child in children)


def prepare(text, limit):
    try:
        parsed = parseQuery(text)
    except Exception:
        try:
            parseUpdate(text)
        except Exception:
            fail("SPARQL_INVALID_QUERY")
        fail("SPARQL_OPERATION_FORBIDDEN")
    inspect(parsed, limit)
    star = parsed[1].name == "DescribeQuery" and "var" not in parsed[1]
    explicit_describe = parsed[1].name == "DescribeQuery" and not star
    if explicit_describe:
        # Pinned translate treats a missing SELECT projection as wildcard and
        # unions child subquery columns into DESCRIBE's explicit projection.
        variables = [v for v in parsed[1]["var"] if isinstance(v, Variable)]
        if variables:
            parsed[1]["projection"] = [CompValue("vars", var=v) for v in variables]
    if star:
        # Use the pinned SELECT wildcard scope implementation (including subquery
        # projections), then restore only the trusted graph-result root dispatch.
        parsed[1].name = "SelectQuery"
        parsed[1]["modifier"] = "DISTINCT"
    prepared = translateQuery(parsed)
    if star:
        prepared.algebra.name = "DescribeQuery"
    elif explicit_describe:
        prepared.algebra["PV"] = list(parsed[1]["var"])
    inspect(prepared.algebra, limit)
    return canonical_query(prepared, owned=True)


def execute(payload):
    from lzo.semantic.files import dataset_from_nquads
    query = prepare(payload["query"], payload["ast_limit"])
    store = Work(payload["work_limit"])
    dataset = Dataset(store=store, default_union=False)
    parsed = dataset_from_nquads(payload["nquads"])
    for s, p, o, g in parsed.quads():
        target = dataset.default_graph if g == parsed.default_graph.identifier else dataset.graph(g)
        target.add((s, p, term_from_node(o).node() if not isinstance(o, BNode) else o))
    del parsed
    store.enabled = True
    prefix, bnodes = str(uuid4()), {}
    retained_bytes = 256

    def term(node):
        if isinstance(node, BNode):
            if node not in bnodes:
                bnodes[node] = f"{prefix}:{len(bnodes)}"
            return {"kind": "bnode", "value": bnodes[node]}
        return asdict(term_from_node(node))

    def account(value):
        nonlocal retained_bytes
        retained_bytes += len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 2
        if retained_bytes > payload["output_bytes"] - 128:
            fail()
        return value

    truncated = False
    graph_rows, seen = [], set()

    def add(triple):
        nonlocal truncated
        if triple in seen:
            return True
        if len(graph_rows) >= payload["max_rows"]:
            truncated = True
            return False
        seen.add(triple)
        s, p, o = triple
        graph_rows.append(account({"subject_iri": str(s) if isinstance(s, URIRef) else None,
            "subject_bnode": term(s) if isinstance(s, BNode) else None,
            "predicate_iri": str(p), "object": term(o)}))
        return True

    def graph_root(ctx, root):
        if root.name == "ConstructQuery":
            template = root.template
            if template is None:
                template = root.p.p.triples
            for binding in evalPart(ctx, root.p):
                for triple in _fillTemplate(template, binding):
                    if not add(triple):
                        return {"type_": "CONSTRUCT"}
            return {"type_": "CONSTRUCT"}
        if root.name != "DescribeQuery":
            raise NotImplementedError()
        described = set()

        def describe(resource):
            if resource in described:
                return True
            described.add(resource)
            store.charge()
            for triple in ctx.graph.triples((resource, None, None)):
                if not add(triple):
                    return False
                if isinstance(triple[2], BNode) and not describe(triple[2]):
                    return False
            return True

        for resource in root.PV:
            if isinstance(resource, URIRef) and not describe(resource):
                return {"type_": "DESCRIBE"}
        if root.p is not None:
            for binding in evalPart(ctx, root.p):
                for var in root.PV:
                    if isinstance(var, Variable) and var in binding and isinstance(binding[var], (URIRef, BNode)):
                        if not describe(binding[var]):
                            return {"type_": "DESCRIBE"}
        return {"type_": "DESCRIBE"}

    sparql.CUSTOM_EVALS["lzo.bounded.graph.roots"] = graph_root
    result = evalQuery(dataset, query)
    response = {"kind": "graph", "variables": [], "rows": [], "boolean": None,
                "graph": graph_rows, "truncated": truncated}
    if result["type_"] == "ASK":
        response.update(kind="ask", boolean=result["askAnswer"])
    elif result["type_"] == "SELECT":
        rows = []
        for binding in result["bindings"]:
            if len(rows) >= payload["max_rows"]:
                response["truncated"] = True
                break
            rows.append(account({str(key): term(value) for key, value in binding.items()}))
        response.update(kind="select", variables=[str(v) for v in result["vars_"]], rows=rows)
    return response


def main():
    try:
        payload = _payload if _payload is not None else json.load(sys.stdin)
        result = execute(payload)
    except Rejected as error:
        result = {"error": error.code}
    except (MemoryError, RecursionError):
        result = {"error": "SPARQL_BUDGET_EXCEEDED"}
    except Exception:
        result = {"error": "SPARQL_EXECUTION_INCOMPLETE"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
