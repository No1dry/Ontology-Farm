"""Authorized query DTOs and fixed child orchestration."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from rdflib.plugins.serializers.nt import _nt_row

from lzo.errors import ApiError
from lzo.semantic.schemas import Model
from lzo.semantic.rdf import RdfTerm
from lzo.semantic.query_process import run_child


class QueryRequest(Model):
    query: str = Field(min_length=1, max_length=65536)
    mode: Literal["current", "review"] = "current"
    timeout_ms: int = Field(default=10000, ge=1)
    max_rows: int = Field(default=200, ge=1)


class QueryBlankNode(Model):
    kind: Literal["bnode"]
    value: str


QueryTerm = RdfTerm | QueryBlankNode


class QueryTriple(Model):
    subject_iri: str | None = None
    subject_bnode: QueryBlankNode | None = None
    predicate_iri: str
    object: QueryTerm


class QueryResult(Model):
    kind: Literal["select", "ask", "graph"]
    variables: tuple[str, ...]
    rows: tuple[dict[str, QueryTerm], ...]
    boolean: bool | None
    graph: tuple[QueryTriple, ...]
    input_watermark: int
    authorization_version: int
    truncated: bool


def evaluate_dataset(dataset, query, *, timeout_ms=10000, max_rows=200,
                     work_limit=1000000, ast_limit=10000, memory_mb=512,
                     input_bytes=16*1024*1024, output_bytes=2*1024*1024, cancelled=None, read_budget=None):
    """Internal fixed local evaluator; caller must supply an authorized dataset."""
    if min(timeout_ms, max_rows, work_limit, ast_limit, memory_mb, input_bytes, output_bytes) < 1:
        raise ValueError("Query budgets must be positive")
    lines, size = [], len(query.encode("utf-8"))
    for s, p, o, g in dataset.quads():
        if read_budget is not None:
            read_budget.checkpoint()
        line = _nt_row((s, p, o)).rstrip()
        if g != dataset.default_graph.identifier:
            line = line[:-1] + g.n3() + " ."
        size += len(line.encode("utf-8")) + 1
        if size > input_bytes:
            raise ApiError("SPARQL_BUDGET_EXCEEDED", 422, "Query input exceeds budget")
        lines.append(line)
    payload = {"nquads": "\n".join(lines), "query": query, "max_rows": max_rows,
               "work_limit": work_limit, "ast_limit": ast_limit, "output_bytes": output_bytes}
    return run_child(payload, timeout_ms=min(timeout_ms, read_budget.remaining_ms()) if read_budget else timeout_ms, memory_mb=memory_mb,
        input_bytes=input_bytes, output_bytes=output_bytes, cancelled=cancelled)


def execute_query(actor, request: QueryRequest, *, cancelled=None, session_check=None) -> QueryResult:
    from lzo.auth.permissions import _current_uow, permission_context, current_version
    from lzo.contracts import ActorContext
    from lzo.db import PgUnitOfWork
    from lzo.ontology.drafts import settings_for
    from lzo.semantic.authorized_dataset import build_authorized_dataset, revalidate
    caller = _current_uow.get()
    settings = settings_for(caller)
    from lzo.semantic.query_read import ReadBudget
    budget = ReadBudget(settings, timeout_ms=request.timeout_ms, cancelled=cancelled)
    for attempt in range(2):
        try:
            with PgUnitOfWork(caller.engine) as uow, permission_context(uow):
                uow.ontology_settings = settings
                uow.query_read_budget = budget
                if session_check is not None:
                    session_check(uow)
                if attempt:
                    actor = ActorContext(actor.subject_id, actor.request_id, current_version(uow))
                snapshot = build_authorized_dataset(actor, request.mode)
            result = evaluate_dataset(snapshot.dataset, request.query,
                timeout_ms=budget.remaining_ms(),
                max_rows=min(request.max_rows, settings.query_max_rows),
                work_limit=settings.query_work_limit, ast_limit=settings.query_ast_limit,
                memory_mb=settings.query_memory_mb, input_bytes=settings.query_input_bytes,
                output_bytes=settings.query_output_bytes, cancelled=cancelled, read_budget=budget)
            with PgUnitOfWork(caller.engine) as uow, permission_context(uow):
                uow.query_read_budget = budget
                if session_check is not None:
                    session_check(uow)
                revalidate(uow, actor, snapshot)
                return QueryResult(**result, input_watermark=snapshot.input_watermark,
                                   authorization_version=snapshot.authorization_version)
        except ApiError as error:
            if error.code not in {"QUERY_SNAPSHOT_CHANGED", "AUTHORIZATION_CHANGED"} or attempt:
                raise


class DerivedLinkView(Model):
    id: UUID
    source_id: UUID
    predicate_iri: str
    target_id: UUID
    state: Literal["derived"] = "derived"
    proof_assertion_id: UUID
    bundle_id: UUID
    input_watermark: int


from lzo.semantic.schemas import ObjectView, LinkView


class GraphNeighborhood(Model):
    nodes: tuple[ObjectView, ...]
    edges: tuple[LinkView | DerivedLinkView, ...]
    next_cursor: str | None
    input_watermark: int


def neighborhood(actor, resource_id: UUID, hops: int, cursor: str | None = None) -> GraphNeighborhood:
    import base64
    import hashlib
    import json
    from lzo.auth.permissions import _current_uow
    from lzo.ontology.drafts import settings_for
    from lzo.semantic.authorized_dataset import build_authorized_dataset, revalidate
    from rdflib import URIRef
    from rdflib.namespace import RDF
    from lzo.semantic.rdf import term_from_node
    uow = _current_uow.get()
    settings = settings_for(uow)
    if not 0 <= hops <= settings.query_max_hops:
        raise ApiError("INVALID_HOPS", 422, "Neighborhood depth exceeds budget")
    snapshot = build_authorized_dataset(actor)
    if resource_id not in snapshot.object_rows:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    iris = {row["iri"]: identifier for identifier, row in snapshot.object_rows.items()}
    edges = []
    for assertion in snapshot.assertions.values():
        if assertion.state != "accepted" or assertion.object.kind != "iri" or assertion.object.value not in iris:
            continue
        edges.append(LinkView(id=assertion.id, source_id=assertion.resource_id,
            predicate_iri=assertion.predicate_iri, target_id=iris[assertion.object.value], assertion=assertion))
    for identifier, (triple, _) in snapshot.proof_views.items():
        if triple.subject_iri in iris and triple.object.kind == "iri" and triple.object.value in iris:
            edges.append(DerivedLinkView(id=identifier, source_id=iris[triple.subject_iri],
                predicate_iri=triple.predicate_iri, target_id=iris[triple.object.value],
                proof_assertion_id=identifier, bundle_id=snapshot.bundle_id,
                input_watermark=snapshot.input_watermark))
    reached, frontier, work = {resource_id}, {resource_id}, 0
    for _ in range(hops):
        found = set()
        for edge in edges:
            work += 1
            if work > settings.query_work_limit:
                raise ApiError("SPARQL_BUDGET_EXCEEDED", 422, "Neighborhood work exceeds budget")
            if edge.source_id in frontier or edge.target_id in frontier:
                found.update((edge.source_id, edge.target_id))
        frontier = found - reached
        reached.update(found)
    edges = sorted((e for e in edges if e.source_id in reached and e.target_id in reached), key=lambda e: e.id.int)
    # Time alone can change this ordered sequence without a new ledger event.
    # Bind offsets to public authorized edge identities, not private boundaries.
    sequence = hashlib.sha256()
    for edge in edges:
        identity = [str(edge.id), str(edge.source_id), edge.predicate_iri, str(edge.target_id),
                    "derived" if isinstance(edge, DerivedLinkView) else "accepted"]
        sequence.update(json.dumps(identity, separators=(",", ":")).encode("utf-8"))
    stamp = [str(resource_id), hops, snapshot.authorization_version, snapshot.event_position,
             str(snapshot.bundle_id), sequence.hexdigest()]
    offset = 0
    if cursor is not None:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor))
            if value["stamp"] != stamp or type(value["offset"]) is not int or not 0 <= value["offset"] <= len(edges):
                raise ValueError()
            offset = value["offset"]
        except (ValueError, TypeError, KeyError):
            raise ApiError("INVALID_CURSOR", 422, "Neighborhood cursor is obsolete or invalid") from None
    page = edges[offset:offset+settings.list_page_size]
    ids = {resource_id} | {e.source_id for e in page} | {e.target_id for e in page}
    nodes = []
    for identifier in sorted(ids, key=str):
        row = snapshot.object_rows[identifier]
        props = {}
        for s, p, o in snapshot.dataset.default_graph.triples((URIRef(row["iri"]), None, None)):
            if str(p) != str(RDF.type):
                props.setdefault(str(p), []).append(term_from_node(o))
        # Validation reports are separate ALL-context evidence; this narrow view
        # exposes only authorized properties and no hidden validation context.
        nodes.append(ObjectView(id=identifier, iri=row["iri"], revision=row["revision"], **row["payload"],
            properties=props, projection_state="synced", bundle_id=snapshot.bundle_id,
            validation_state="pending", validation_report=None, candidate_validation_report=None,
            evaluation_time=snapshot.evaluated_at, next_transition_at=None))
    following = offset + len(page)
    next_cursor = base64.urlsafe_b64encode(json.dumps({"stamp": stamp,"offset":following}).encode()).decode() if following < len(edges) else None
    revalidate(uow, actor, snapshot)
    return GraphNeighborhood(nodes=tuple(nodes), edges=tuple(page), next_cursor=next_cursor,
                             input_watermark=snapshot.input_watermark)
