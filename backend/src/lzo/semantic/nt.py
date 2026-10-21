"""Pinned RDFLib NT grammar with linear, strict UTF-8 universal line framing."""
from io import BytesIO, TextIOWrapper
from rdflib import Graph
from rdflib.plugins.parsers.ntriples import W3CNTriplesParser, NTGraphSink


class _Lines(W3CNTriplesParser):
    def readline(self):
        line = self.file.readline()
        if line == "":
            return None
        return line[:-1] if line.endswith("\n") else line


def parse_nt(data: bytes) -> Graph:
    graph = Graph()
    with TextIOWrapper(BytesIO(data), encoding="utf-8", errors="strict", newline=None) as source:
        _Lines(NTGraphSink(graph)).parse(source)
    return graph


def parse_records(payload):
    """Fixed internal child transport; never a public RDF representation."""
    import json
    from rdflib import BNode, URIRef
    graph = parse_nt(payload["nt"].encode("utf-8"))
    records, retained = [], 32
    def node(value):
        if isinstance(value, URIRef):
            return {"kind": "iri", "value": str(value)}
        if isinstance(value, BNode):
            return {"kind": "bnode", "value": str(value)}
        return {"kind": "literal", "value": str(value),
                "datatype": str(value.datatype) if value.datatype else None, "language": value.language}
    for triple in graph:
        record = [node(value) for value in triple]
        retained += len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 2
        if retained > payload["output_bytes"]:
            return {"error": "SPARQL_BUDGET_EXCEEDED"}
        records.append(record)
    return {"triples": records}
