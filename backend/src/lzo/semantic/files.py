"""Artifact-backed, offline, bounded semantic parsing and canonicalization."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Thread
from typing import Literal as Format
import psutil

from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.namespace import OWL
from rdflib.plugins.serializers.jsonld import from_rdf

from lzo.artifacts.store import ArtifactStore
from lzo.config import Settings
from lzo.contracts import BlobRef
from lzo.semantic.rdf import INTERNAL, SKOLEM, RdfTerm, iri, term_from_node

_store = ContextVar("semantic_artifact_store", default=None)
MAX_BYTES = int(os.environ.get("LZO_SEMANTIC_MAX_BYTES", 2 * 1024 * 1024))
MAX_QUADS = int(os.environ.get("LZO_SEMANTIC_MAX_QUADS", 20000))
MAX_BNODES = int(os.environ.get("LZO_SEMANTIC_MAX_BNODES", 256))
ALGORITHM = "URDNA2015/PyLD-3.3.0"


class SemanticExecutionIncomplete(Exception):
    """Known inability to execute, never a constraint result or generic error."""


class SemanticBudgetExceeded(SemanticExecutionIncomplete, ValueError):
    pass


class SemanticRecursionIncomplete(SemanticExecutionIncomplete, ValueError):
    pass


class SemanticTransportUnavailable(SemanticExecutionIncomplete, RuntimeError):
    pass


class SemanticDependencyUnavailable(SemanticExecutionIncomplete, ImportError):
    pass


@dataclass(frozen=True)
class ParsedSemanticDocument:
    dataset: Dataset
    artifact: BlobRef
    format: str


@dataclass(frozen=True)
class SkolemizedDocument:
    dataset: Dataset
    mapping: dict[str, str]
    canonical_digest: str
    algorithm: str
    source: BlobRef


@contextmanager
def semantic_file_context(store: ArtifactStore):
    token = _store.set(store)
    try:
        yield
    finally:
        _store.reset(token)


def deny_network():
    """Process-local audit policy installed once before any third-party import."""
    def audit(event, args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                     "urllib.Request"}:
            raise ValueError("Semantic execution cannot access the network")
    sys.addaudithook(audit)


def bounded_json(module, payload, *, timeout_seconds=None, memory_mb=None):
    timeout_seconds = timeout_seconds or float(os.environ.get("LZO_SEMANTIC_TIMEOUT_SECONDS", 20))
    memory_mb = memory_mb or int(os.environ.get("LZO_SEMANTIC_MEMORY_MB", 768))
    if min(MAX_BYTES, MAX_QUADS, MAX_BNODES, timeout_seconds, memory_mb) <= 0:
        raise ValueError("Semantic budgets must be positive")
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode()) > MAX_BYTES * 6:
        raise SemanticBudgetExceeded("Semantic input exceeds byte budget")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    environment["PYTHONIOENCODING"] = "utf-8"
    # Monitoring a child owned by this call bounds expensive parser/SHACL work
    # on Windows and Linux without modifying process-global library hooks.
    try:
        child = subprocess.Popen([sys.executable, "-m", module], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        raise SemanticTransportUnavailable("Semantic subprocess unavailable") from None
    with child as process:
        output, communication_errors = [], []
        def communicate():
            try:
                output.append(process.communicate(encoded))
            except Exception as error:
                communication_errors.append(error)
        reader = Thread(target=communicate, daemon=True)
        reader.start()
        started = time.monotonic()
        monitored = psutil.Process(process.pid)
        failure = None
        while process.poll() is None:
            if time.monotonic() - started > timeout_seconds:
                failure = "time"
            try:
                if monitored.memory_info().rss > memory_mb * 1024 * 1024:
                    failure = "memory"
            except psutil.NoSuchProcess:
                break
            if failure:
                process.kill()
                break
            reader.join(min(0.02, timeout_seconds))
        reader.join()
        if failure:
            raise SemanticBudgetExceeded(f"Semantic execution exceeded {failure} budget")
        if communication_errors or not output:
            raise SemanticTransportUnavailable("Semantic subprocess communication failed")
        stdout, stderr = output[0]
        if len(stdout.encode()) > MAX_BYTES * 8:
            raise SemanticBudgetExceeded("Semantic output exceeds byte budget")
        returncode = process.returncode
    if returncode:
        # Only our bounded error code/message is exposed, never third-party logs.
        try:
            error = json.loads(stdout)
        except (ValueError, TypeError):
            raise SemanticTransportUnavailable("Semantic subprocess failed") from None
        if error.get("type") == "ImportError":
            raise SemanticDependencyUnavailable("Required semantic dependency is unavailable")
        if error.get("type") in {"MemoryError", "SemanticBudgetExceeded"}:
            raise SemanticBudgetExceeded("Semantic execution exceeded resource budget")
        if error.get("type") == "SemanticRecursionIncomplete":
            raise SemanticRecursionIncomplete("Recursive shape evaluation exceeds the supported profile")
        raise ValueError(error.get("error", "Semantic execution failed"))
    try:
        return json.loads(stdout)
    except (ValueError, TypeError):
        raise SemanticTransportUnavailable("Semantic subprocess response is unavailable") from None


def nquads(dataset):
    # Literal.n3 may emit multiline Turtle, which is not N-Quads. Let the real
    # N-Triples serializer encode each graph, retaining shared bnode labels.
    output = []
    for graph in dataset.graphs():
        suffix = "" if graph.identifier == dataset.default_graph.identifier else " " + graph.identifier.n3()
        output.extend(line[:-2] + suffix + " .\n"
                      for line in graph.serialize(format="nt").splitlines() if line)
    return "".join(output)


def dataset_from_nquads(source):
    """Retain canonical labels; RDFLib's N-Quads parser remaps them randomly."""
    from pyld import jsonld
    parsed = jsonld.JsonLdProcessor.parse_nquads(source)
    result = Dataset()
    def node(value):
        if value["type"] == "IRI":
            return URIRef(value["value"])
        if value["type"] == "blank node":
            return BNode(value["value"][2:])
        return Literal(value["value"], lang=value.get("language"),
                       datatype=None if value.get("language") else value.get("datatype"), normalize=False)
    for graph_name, triples in parsed.items():
        target = result.default_graph if graph_name == "@default" else result.graph(
            BNode(graph_name[2:]) if graph_name.startswith("_:") else URIRef(graph_name))
        for triple in triples:
            target.add(tuple(node(triple[position]) for position in ("subject", "predicate", "object")))
    return result


def _contexts(value):
    if isinstance(value, list):
        for part in value:
            _contexts(part)
    elif isinstance(value, dict):
        for key, part in value.items():
            if key == "@import":
                raise ValueError("Remote/unbundled JSON-LD context profile is unsupported")
            if key == "@context":
                contexts = part if isinstance(part, list) else [part]
                if any(isinstance(context, str) for context in contexts):
                    raise ValueError("Remote/unbundled JSON-LD context profile is unsupported")
            _contexts(part)


def parse_text(raw: str, format: str, *, allow_platform_skolem=False) -> Dataset:
    """Internal child-process parser; Turtle does not resolve OWL imports."""
    if format not in ("turtle", "json-ld"):
        raise ValueError("Unsupported semantic format")
    if len(raw.encode()) > MAX_BYTES:
        raise SemanticBudgetExceeded("Semantic input exceeds byte budget")
    if format == "json-ld":
        from pyld import jsonld
        document = json.loads(raw)
        if not isinstance(document, (dict, list)):
            raise ValueError("JSON-LD must be an inline object or array")
        _contexts(document)
        def reject_loader(*args, **kwargs):
            raise ValueError("External document loading denied")
        expanded = jsonld.expand(document, {"base": "urn:lzo:document:",
                                           "documentLoader": reject_loader})
        def check_graphs(value):
            if isinstance(value, list):
                for item in value:
                    check_graphs(item)
            elif isinstance(value, dict):
                if "@graph" in value and value.get("@id") == "urn:x-rdflib:default":
                    raise ValueError("Reserved dataset graph name")
                for item in value.values():
                    check_graphs(item)
        # Check expanded IDs before RDFLib's Dataset can conflate a named
        # graph with its implementation sentinel, including compact aliases.
        check_graphs(expanded)
    dataset = Dataset()
    target = dataset.default_graph if format == "turtle" else dataset
    target.parse(data=raw, format=format, publicID="urn:lzo:document:")
    count, nodes = 0, set()
    for s, p, o, g in dataset.quads():
        count += 1
        if count > MAX_QUADS:
            raise SemanticBudgetExceeded("Semantic quad budget exceeded")
        if p == OWL.imports:
            raise ValueError("Unbundled OWL imports profile is unsupported")
        for node in (s, p, o, g):
            if isinstance(node, BNode):
                nodes.add(node)
            elif isinstance(node, URIRef):
                iri(str(node))
                if str(node).startswith(SKOLEM) and not allow_platform_skolem:
                    raise ValueError("Imported skolem instructions are reserved")
            elif isinstance(node, Literal):
                term_from_node(node)
        if len(nodes) > MAX_BNODES:
            raise SemanticBudgetExceeded("Semantic blank-node budget exceeded")
    return canonical_strings(dataset)


def canonical_strings(dataset):
    # Canonicalize RDF 1.1 string representation and language-tag case before
    # hashing or query evaluation. No lexical rewrite of numeric terms.
    result = Dataset()
    for s, p, o, g in dataset.quads():
        if isinstance(o, Literal):
            o = term_from_node(o).node()
        target = result.default_graph if g == dataset.default_graph.identifier else result.graph(g)
        target.add((s, p, o))
    return result


def parse_semantic_document(blob: BlobRef, format: Format["turtle", "json-ld"]) -> ParsedSemanticDocument:
    store = _store.get()
    if store is None:
        settings = Settings()
        store = ArtifactStore(settings.file_root, max_upload_bytes=settings.artifact_max_upload_bytes)
    if blob.size_bytes > MAX_BYTES:
        raise SemanticBudgetExceeded("Semantic input exceeds byte budget")
    with store.open(blob) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if hashlib.sha256(raw).hexdigest() != blob.sha256:
        raise ValueError("Semantic artifact digest mismatch")
    result = bounded_json(__name__, {"operation": "parse", "raw": raw.decode("utf-8"), "format": format})
    dataset = dataset_from_nquads(result["nquads"])
    return ParsedSemanticDocument(dataset, blob, format)


def _canonical(nq):
    from pyld import jsonld
    def reject_loader(*args, **kwargs):
        raise ValueError("External document loading denied")
    return jsonld.normalize(nq, {"algorithm": "URDNA2015",
        "inputFormat": "application/n-quads", "format": "application/n-quads",
        "documentLoader": reject_loader})


def skolemize(document: ParsedSemanticDocument) -> SkolemizedDocument:
    result = bounded_json(__name__, {"operation": "canonical", "nquads": nquads(document.dataset)})
    canonical = result["nquads"]
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    dataset = dataset_from_nquads(canonical)
    nodes = {str(node) for quad in dataset.quads() for node in quad if isinstance(node, BNode)}
    # version_id is the stable independent document/import scope, not physical
    # byte deduplication identity. Original artifact bytes remain unchanged.
    mapping = {node: f"{SKOLEM}{document.artifact.version_id}:{digest}:{node}" for node in nodes}
    output = Dataset()
    for s, p, o, g in dataset.quads():
        s, p, o, g = (URIRef(mapping[str(node)]) if isinstance(node, BNode) else node
                      for node in (s, p, o, g))
        target = output.default_graph if g == dataset.default_graph.identifier else output.graph(g)
        target.add((s, p, o))
    return SkolemizedDocument(output, mapping, digest, ALGORITHM, document.artifact)


def reverse_skolem(document: SkolemizedDocument) -> Dataset:
    reverse = {value: BNode(key) for key, value in document.mapping.items()}
    result = Dataset()
    for s, p, o, g in document.dataset.quads():
        s, p, o, g = (reverse.get(str(node), node) if isinstance(node, URIRef) else node
                      for node in (s, p, o, g))
        target = result.default_graph if g == document.dataset.default_graph.identifier else result.graph(g)
        target.add((s, p, o))
    return result


def export_jsonld(dataset: Dataset) -> str:
    # RDFLib 7.6 Dataset.serialize wraps this option in a truthy tuple.
    # Use the genuine public serializer function whose false option is honored.
    return json.dumps(from_rdf(dataset, use_native_types=False), ensure_ascii=False)


def main():
    deny_network()
    try:
        request = json.load(sys.stdin)
        if request["operation"] == "parse":
            result = nquads(parse_text(request["raw"], request["format"]))
        elif request["operation"] == "canonical":
            result = _canonical(request["nquads"])
        else:
            raise ValueError("Unknown semantic operation")
        print(json.dumps({"nquads": result}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"type": "ImportError" if isinstance(error, ImportError) else type(error).__name__,
                          "error": str(error) if isinstance(error, ValueError) else "Invalid semantic document"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
