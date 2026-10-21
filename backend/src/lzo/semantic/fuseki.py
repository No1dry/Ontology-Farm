"""Real Fuseki HTTP adapter with immutable exact-term companion records.

select/update are trusted physical operations. Public semantic evaluation must
use read_graph(), never repair values after native SPARQL evaluation.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import ssl

import certifi
import httpx
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from lzo.contracts import JsonObject
from lzo.semantic.rdf import (INTERNAL, RdfTerm, Triple, iri, logical_digest,
                              logical_graph, ordered, triple_key)
from lzo.semantic.nt import parse_nt as default_parse_nt

V = Namespace(INTERNAL + "v1:")
CATALOG = URIRef(INTERNAL + "catalog")
ROLES = ("schema", "shape", "candidate", "accepted", "provenance", "derived")


@lru_cache(maxsize=1)
def _verified_context():
    """Same verified trust as HTTPX trust_env=False, loaded once per process.

    All adapter transports retain the default HTTP/1 protocol configuration.
    A changed installed CA bundle takes effect when the process restarts.
    """
    return ssl.create_default_context(cafile=certifi.where())


def graph_iri(resource_id, role):
    if role not in ROLES:
        raise ValueError("Unknown semantic graph role")
    from uuid import UUID
    return f"urn:lzo:resource:{UUID(str(resource_id))}:{role}"


def generation_digest(graphs):
    if set(graphs) - set(ROLES):
        raise ValueError("Unknown semantic graph role")
    return hashlib.sha256(json.dumps({role: logical_digest(graphs.get(role, ()))
        for role in ROLES}, sort_keys=True).encode()).hexdigest()


class RepresentationError(ValueError):
    """Remote graph is not a complete, internally consistent managed revision."""


@dataclass(frozen=True)
class GraphPair:
    logical_iri: str
    physical_iri: str
    metadata_iri: str
    digest: str
    count: int


def _nt(graph):
    return graph.serialize(format="nt")


def _one(graph, subject, predicate):
    values = list(graph.objects(subject, predicate))
    if len(values) != 1:
        raise RepresentationError("Incomplete or ambiguous representation")
    return values[0]


def _manifest(generation, resource, revision, position, digest, pairs):
    graph = Graph()
    for predicate, value in ((RDF.type, V.Generation), (V.resource, resource),
                             (V.revision, Literal(revision)), (V.position, Literal(position)),
                             (V.digest, Literal(digest))):
        graph.add((generation, predicate, value))
    for role, metadata_iri in pairs.items():
        graph.add((generation, V[role], URIRef(metadata_iri)))
    return graph


class FusekiRepository:
    def __init__(self, endpoint: str, *, timeout_seconds: float = 10, read_budget=None, parse_nt=None):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout_seconds
        self.read_budget = read_budget
        self.parse_nt = parse_nt or default_parse_nt

    def _checkpoint(self):
        if self.read_budget is not None:
            self.read_budget.checkpoint()

    def _acquire(self, method, path, *, timeout, headers, **options):
        from lzo.errors import ApiError
        budget = self.read_budget
        headers = {**headers, "Accept-Encoding": "identity"}
        content = bytearray()
        try:
            with httpx.stream(method, self.endpoint + path, headers=headers,
                    timeout=min(timeout, budget.remaining_ms()/1000), trust_env=False,
                    verify=_verified_context(), **options) as response:
                if response.status_code == 404 and path == "/data":
                    budget.checkpoint()
                    return b""
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ApiError("SPARQL_ACQUISITION_INCOMPLETE", 422, "Unexpected remote content encoding")
                for chunk in response.iter_raw():
                    budget.retain(len(chunk), len(content))
                    content.extend(chunk)
            budget.checkpoint()
        except httpx.TimeoutException:
            raise ApiError("SPARQL_TIMEOUT", 422, "Semantic acquisition did not complete") from None
        except httpx.HTTPError:
            raise ApiError("SPARQL_ACQUISITION_INCOMPLETE", 422, "Semantic acquisition did not complete") from None
        return bytes(content)

    def select(self, query: str, timeout_ms: int) -> JsonObject:
        if not 0 < timeout_ms <= 120000:
            raise ValueError("Invalid query time budget")
        if self.read_budget is not None:
            timeout_ms = min(timeout_ms, self.read_budget.remaining_ms())
            body = self._acquire("POST", "/query", data={"query": query, "timeout": str(timeout_ms)},
                headers={"Accept": "application/sparql-results+json"}, timeout=min(self.timeout, timeout_ms/1000))
            try:
                result = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                raise RepresentationError("Invalid remote query response") from None
            self._checkpoint()
            return result
        response = httpx.post(self.endpoint + "/query", data={"query": query, "timeout": str(timeout_ms)},
                              headers={"Accept": "application/sparql-results+json"},
                              timeout=min(self.timeout, timeout_ms / 1000), trust_env=False,
                              verify=_verified_context())
        response.raise_for_status()
        return response.json()

    def update(self, query: str) -> None:
        response = httpx.post(self.endpoint + "/update", data={"update": query},
                              timeout=self.timeout, trust_env=False, verify=_verified_context())
        response.raise_for_status()

    def health(self) -> bool:
        try:
            return self.select("ASK {}", 3000).get("boolean") is True
        except (httpx.HTTPError, ValueError):
            return False

    def _get(self, graph_iri: str) -> Graph:
        if self.read_budget is not None:
            body = self._acquire("GET", "/data", params={"graph": graph_iri},
                headers={"Accept": "application/n-triples"}, timeout=self.timeout)
            graph = self.parse_nt(body)
            self._checkpoint()
            return graph
        response = httpx.get(self.endpoint + "/data", params={"graph": graph_iri},
                            headers={"Accept": "application/n-triples"},
                            timeout=self.timeout, trust_env=False, verify=_verified_context())
        if response.status_code == 404:
            return Graph()
        response.raise_for_status()
        return self.parse_nt(response.content)

    def _pair(self, graph_iri, triples):
        iri(graph_iri)
        triples = ordered(triples)
        digest = logical_digest(triples)
        key = hashlib.sha256((graph_iri + "\n" + digest).encode()).hexdigest()
        pair = GraphPair(graph_iri, INTERNAL + "graph:" + key,
                         INTERNAL + "metadata:" + key, digest, len(triples))
        meta = Graph()
        manifest = URIRef(pair.metadata_iri)
        for predicate, value in (
            (RDF.type, V.Manifest), (V.version, Literal("1")), (V.logical, iri(graph_iri)),
            (V.physical, URIRef(pair.physical_iri)), (V.digest, Literal(digest)),
            (V["count"], Literal(len(triples)))):
            meta.add((manifest, predicate, value))
        for triple in triples:
            statement = URIRef(INTERNAL + "statement:" + hashlib.sha256(
                (graph_iri + "\n" + triple_key(triple)).encode()).hexdigest())
            s, p, o = triple.nodes()
            for predicate, value in ((RDF.type, RDF.Statement), (RDF.subject, s),
                                     (RDF.predicate, p), (RDF.object, o), (V.member, manifest)):
                meta.add((statement, predicate, value))
            meta.add((statement, V.kind, Literal(triple.object.kind)))
            meta.add((statement, V.lexical, Literal(triple.object.value, datatype=XSD.string)))
            if triple.object.language:
                meta.add((statement, V.language, Literal(triple.object.language)))
            elif triple.object.datatype_iri:
                meta.add((statement, V.datatype, iri(triple.object.datatype_iri)))
        return pair, logical_graph(triples), meta

    def _pair_insert(self, pair, typed, meta):
        return (f"GRAPH <{pair.physical_iri}> {{ {_nt(typed)} }} "
                f"GRAPH <{pair.metadata_iri}> {{ {_nt(meta)} }}")

    def replace_graph(self, graph_iri: str, triples: tuple[Triple, ...]) -> str:
        pair, typed, meta = self._pair(graph_iri, triples)
        logical = iri(graph_iri).n3()
        if self._exists(pair):
            self.read_pair(pair)
        else:
            self.update(f"INSERT DATA {{ {self._pair_insert(pair, typed, meta)} }}")
            self.read_pair(pair)
        # Complete pair is verified before switching the public logical pointer.
        self.update(f"""DELETE WHERE {{ GRAPH <{CATALOG}> {{ {logical} <{V.current}> ?old }} }};
            INSERT DATA {{ GRAPH <{CATALOG}> {{ {logical} <{V.current}> <{pair.metadata_iri}>; <{V.managed}> "1" }} }}""")
        return pair.digest

    def _exists(self, pair):
        return self.select(f"""ASK {{
            {{ GRAPH <{pair.metadata_iri}> {{ ?s ?p ?o }} }}
            UNION {{ GRAPH <{pair.physical_iri}> {{ ?s ?p ?o }} }}
            UNION {{ GRAPH <{CATALOG}> {{ <{pair.logical_iri}> <{V.current}> <{pair.metadata_iri}> }} }}
        }}""", 10000)["boolean"]

    def resolve_graph(self, graph_iri: str) -> GraphPair | None:
        logical = iri(graph_iri)
        rows = self.select(f"""SELECT ?m ?physical ?digest ?count WHERE {{
            GRAPH <{CATALOG}> {{ {logical.n3()} <{V.current}> ?m }}
            OPTIONAL {{ GRAPH ?m {{ ?m <{V.physical}> ?physical; <{V.digest}> ?digest; <{V["count"]}> ?count }} }}
            }}""", 10000)["results"]["bindings"]
        if not rows:
            managed = self.select(f'ASK {{ GRAPH <{CATALOG}> {{ {logical.n3()} <{V.managed}> ?marker }} }}',
                                  10000)["boolean"]
            if managed or len(self._get(graph_iri)):
                raise RepresentationError("Unmanaged physical graph has no exact-term manifest")
            return None
        if len(rows) != 1 or set(rows[0]) != {"m", "physical", "digest", "count"}:
            raise RepresentationError("Missing or ambiguous graph pair")
        row = rows[0]
        return GraphPair(graph_iri, row["physical"]["value"], row["m"]["value"],
                         row["digest"]["value"], int(row["count"]["value"]))

    def read_graph(self, graph_iri: str) -> tuple[Triple, ...]:
        pair = self.resolve_graph(graph_iri)
        return () if pair is None else self.read_pair(pair)

    def read_pair(self, pair: GraphPair) -> tuple[Triple, ...]:
        meta = self._get(pair.metadata_iri)
        manifest = URIRef(pair.metadata_iri)
        expected = {RDF.type: V.Manifest, V.version: Literal("1"), V.logical: iri(pair.logical_iri),
                    V.physical: URIRef(pair.physical_iri), V.digest: Literal(pair.digest),
                    V["count"]: Literal(pair.count)}
        for predicate, value in expected.items():
            if _one(meta, manifest, predicate) != value:
                raise RepresentationError("Graph manifest mismatch")
        result = []
        for statement in meta.subjects(RDF.type, RDF.Statement):
            self._checkpoint()
            kind = str(_one(meta, statement, V.kind))
            value = str(_one(meta, statement, V.lexical))
            datatypes = list(meta.objects(statement, V.datatype))
            languages = list(meta.objects(statement, V.language))
            if len(datatypes) > 1 or len(languages) > 1:
                raise RepresentationError("Ambiguous literal qualifiers")
            try:
                term = RdfTerm(kind, value, str(datatypes[0]) if datatypes else None,
                               str(languages[0]) if languages else None)
                triple = Triple(str(_one(meta, statement, RDF.subject)),
                                str(_one(meta, statement, RDF.predicate)), term)
            except ValueError as error:
                raise RepresentationError("Invalid reconstructed term") from error
            if _one(meta, statement, V.member) != manifest:
                raise RepresentationError("Statement belongs to another revision")
            result.append(triple)
        result = ordered(result)
        rebuilt, typed, expected_meta = self._pair(pair.logical_iri, result)
        if rebuilt != pair or len(result) != pair.count:
            raise RepresentationError("Logical digest or count mismatch")
        self._verify_physical(pair.physical_iri, typed)
        self._verify_physical(pair.metadata_iri, expected_meta)
        self._checkpoint()
        return result

    def _verify_physical(self, graph_iri, expected):
        values = " ".join("(" + " ".join(n.n3() for n in triple) + ")" for triple in expected)
        if not values:
            if len(self._get(graph_iri)):
                raise RepresentationError("Empty graph has unexpected physical data")
            return
        # VALUES terms retain source lexical form while stored TDB2 terms do
        # not. Explicit same-datatype value comparison handles that boundary;
        # lexical metadata itself is xsd:string and must still match exactly.
        equal = """(sameTerm(?o, ?eo) ||
            (isLiteral(?o) && isLiteral(?eo) &&
             datatype(?o) = datatype(?eo) && lang(?o) = lang(?eo) && ?o = ?eo))"""
        mismatch = f"""ASK {{ {{
            VALUES (?es ?ep ?eo) {{ {values} }}
            FILTER NOT EXISTS {{ GRAPH <{graph_iri}> {{ ?es ?ep ?o }} FILTER({equal}) }}
          }} UNION {{
            GRAPH <{graph_iri}> {{ ?s ?p ?o }}
            FILTER NOT EXISTS {{ VALUES (?es ?ep ?eo) {{ {values} }}
                FILTER(?s = ?es && ?p = ?ep && {equal}) }}
          }} }}"""
        if self.select(mismatch, 10000)["boolean"]:
            raise RepresentationError("Physical graph does not match exact-term representation")

    def publish_revision(self, resource_id, revision, position, graphs):
        """Stage a complete generation then CAS all six pointers and input position.

        Returns (state, digest). Old or replayed writers never overwrite a newer
        resource generation, even after their PostgreSQL transaction was lost.
        """
        if type(revision) is not int or revision < 1 or type(position) is not int or position < 1:
            raise ValueError("Invalid revision position")
        digest = generation_digest(graphs)
        resource = iri(f"urn:lzo:resource:{resource_id}")
        generation = URIRef(INTERNAL + f"generation:{resource_id}:{revision}:{digest}")
        prepared = [self._pair(graph_iri(resource_id, role), graphs.get(role, ())) for role in ROLES]
        current = self.current_revision(resource_id)
        if current is not None:
            if current["revision"] > revision:
                self._verify_generation(current)
                return "superseded", digest
            if current["revision"] == revision:
                if current["digest"] != digest or current["position"] != position:
                    raise RepresentationError("Same revision has different content or position")
                self._verify_generation(current)
                return "synced", digest
        manifest = _manifest(generation, resource, revision, position, digest,
                             {role: pair.metadata_iri for role, (pair, _, _) in zip(ROLES, prepared)})
        for role, (pair, typed, meta) in zip(ROLES, prepared):
            if self._exists(pair):
                self.read_pair(pair)
        existing = self._get(str(generation))
        prepared_before = bool(existing) or self.select(f"""ASK {{ GRAPH <{CATALOG}> {{
            <{generation}> <{V.prepared}> ?marker }} }}""", 10000)["boolean"]
        if prepared_before:
            if set(existing) != set(manifest):
                raise RepresentationError("Immutable generation manifest mismatch")
        else:
            # One Jena transaction claims and creates the whole stage. A second
            # same-identity writer cannot merge a conflicting input position.
            # The durable marker also detects a subsequently deleted manifest.
            self.update("INSERT { " + " ".join(self._pair_insert(*p) for p in prepared)
                + f""" GRAPH <{generation}> {{ {_nt(manifest)} }}
                GRAPH <{CATALOG}> {{ <{generation}> <{V.prepared}> "1" }}
                }} WHERE {{
                    FILTER NOT EXISTS {{ GRAPH <{generation}> {{ ?s ?p ?o }} }}
                    FILTER NOT EXISTS {{ GRAPH <{CATALOG}> {{ <{generation}> <{V.prepared}> ?marker }} }}
                }}""")
        if set(self._get(str(generation))) != set(manifest):
            raise RepresentationError("Immutable generation manifest mismatch")
        for pair, typed, meta in prepared:
            self.read_pair(pair)
        pointers = " ".join(f'<{pair.logical_iri}> <{V.current}> <{pair.metadata_iri}>; <{V.managed}> "1" .'
                            for pair, _, _ in prepared)
        logical_values = " ".join(f"<{pair.logical_iri}>" for pair, _, _ in prepared)
        self.update(f"""DELETE {{ GRAPH <{CATALOG}> {{
                {resource.n3()} <{V.generation}> ?old .
                ?logical <{V.current}> ?previous .
            }} }} INSERT {{ GRAPH <{CATALOG}> {{
                {resource.n3()} <{V.generation}> <{generation}> .
                {pointers}
            }} }} WHERE {{
                OPTIONAL {{ GRAPH <{CATALOG}> {{ {resource.n3()} <{V.generation}> ?old }}
                           GRAPH ?old {{ ?old <{V.revision}> ?oldRevision }} }}
                FILTER (!BOUND(?old) || ?oldRevision < {revision})
                VALUES ?logical {{ {logical_values} }}
                OPTIONAL {{ GRAPH <{CATALOG}> {{ ?logical <{V.current}> ?previous }} }}
            }}""")
        current = self.current_revision(resource_id)
        if current is None or current["revision"] < revision:
            raise RepresentationError("Generation publication was not acknowledged")
        self._verify_generation(current)
        if current["revision"] == revision and (current["digest"] != digest or current["position"] != position):
            raise RepresentationError("Concurrent same-revision conflict")
        return ("synced" if current["revision"] == revision else "superseded"), digest

    def current_revision(self, resource_id):
        resource = iri(f"urn:lzo:resource:{resource_id}")
        rows = self.select(f"""SELECT ?generation WHERE {{
            GRAPH <{CATALOG}> {{ {resource.n3()} <{V.generation}> ?generation }} }}""",
            10000)["results"]["bindings"]
        if not rows:
            roles = " ".join(iri(graph_iri(resource_id, role)).n3() for role in ROLES)
            if self.select(f"""ASK {{ GRAPH <{CATALOG}> {{
                    VALUES ?role {{ {roles} }} ?role <{V.managed}> ?marker
                }} }}""", 10000)["boolean"]:
                raise RepresentationError("Managed resource has no complete generation pointer")
            return None
        if len(rows) != 1:
            raise RepresentationError("Ambiguous resource generation")
        generation = URIRef(rows[0]["generation"]["value"])
        manifest = self._get(str(generation))
        if _one(manifest, generation, RDF.type) != V.Generation or _one(manifest, generation, V.resource) != resource:
            raise RepresentationError("Invalid resource generation")
        current = {"generation": str(generation), "resource_id": resource_id,
                "revision": int(_one(manifest, generation, V.revision)),
                "position": int(_one(manifest, generation, V.position)),
                "digest": str(_one(manifest, generation, V.digest)),
                "pairs": {role: str(_one(manifest, generation, V[role])) for role in ROLES}}
        expected = _manifest(generation, resource, current["revision"], current["position"],
                             current["digest"], current["pairs"])
        if set(manifest) != set(expected):
            raise RepresentationError("Live generation manifest mismatch")
        return current

    def _verify_generation(self, current):
        graphs = {}
        for role, metadata_iri in current["pairs"].items():
            meta = self._get(metadata_iri)
            m = URIRef(metadata_iri)
            pair = GraphPair(graph_iri(current["resource_id"], role),
                str(_one(meta, m, V.physical)), metadata_iri,
                str(_one(meta, m, V.digest)), int(_one(meta, m, V["count"])))
            if self.resolve_graph(pair.logical_iri) != pair:
                raise RepresentationError("Live graph pointer differs from complete generation")
            graphs[role] = self.read_pair(pair)
        if generation_digest(graphs) != current["digest"]:
            raise RepresentationError("Incomplete generation or digest mismatch")
        return graphs
