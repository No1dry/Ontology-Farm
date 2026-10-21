"""Scoped cumulative acquisition deadline; each NT parse has an owned hard cap.

Parent SQL, hashing and HTTP internals remain cooperatively checked operations.
This is not OS-hard preemption of the whole dataset build.
"""
import time
from rdflib import Graph, URIRef, BNode, Literal
from lzo.errors import ApiError
from lzo.semantic.query_process import run_child


class ReadBudget:
    def __init__(self, settings, *, timeout_ms=None, cancelled=None):
        self.settings = settings
        self.deadline = time.monotonic() + min(timeout_ms or settings.query_timeout_ms,
            settings.query_timeout_ms) / 1000
        self.cancelled, self.acquired = cancelled, 0

    def checkpoint(self):
        if self.cancelled is not None and self.cancelled():
            raise ApiError("SPARQL_CANCELLED", 422, "Semantic acquisition did not complete")
        if time.monotonic() >= self.deadline:
            raise ApiError("SPARQL_TIMEOUT", 422, "Semantic acquisition did not complete")

    def remaining_ms(self):
        self.checkpoint()
        return max(1, int((self.deadline-time.monotonic())*1000))

    def retain(self, size, response_size):
        self.checkpoint()
        if (response_size + size > self.settings.query_read_response_bytes
                or self.acquired + size > self.settings.query_read_total_bytes):
            raise ApiError("SPARQL_BUDGET_EXCEEDED", 422, "Semantic acquisition exceeds budget")
        self.acquired += size

    def parse_nt(self, data):
        from lzo.semantic.fuseki import RepresentationError
        self.checkpoint()
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RepresentationError("Invalid remote NT encoding") from None
        try:
            records = run_child({"operation": "parse_nt", "nt": text,
                "output_bytes": self.settings.query_parse_output_bytes}, timeout_ms=self.remaining_ms(),
                memory_mb=self.settings.query_memory_mb,
                input_bytes=self.settings.query_read_response_bytes*6+1024,
                output_bytes=self.settings.query_parse_output_bytes, cancelled=self.cancelled)
        except ApiError as error:
            if error.code == "RDF_REPRESENTATION_INVALID":
                raise RepresentationError("Invalid remote NT representation") from None
            raise
        graph = Graph()
        def node(value):
            if value["kind"] == "iri":
                return URIRef(value["value"])
            if value["kind"] == "bnode":
                return BNode(value["value"])
            return Literal(value["value"], datatype=value["datatype"], lang=value["language"], normalize=False)
        for record in records["triples"]:
            self.checkpoint()
            graph.add(tuple(node(value) for value in record))
        self.checkpoint()
        return graph
