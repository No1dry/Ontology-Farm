"""Exact logical RDF sets, independent of TDB2 numeric value canonicalization."""
from dataclasses import dataclass, asdict
import base64
import binascii
import hashlib
import json
import re
from typing import Literal as Kind

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

INTERNAL = "urn:lzo:internal:"
SKOLEM = "urn:lzo:skolem:"
INTEGER_TYPES = set("""integer long int short byte nonPositiveInteger negativeInteger
nonNegativeInteger positiveInteger unsignedLong unsignedInt unsignedShort unsignedByte""".split())


def _lexical(value, datatype):
    if datatype is None:
        return
    if datatype.startswith(str(XSD)):
        name = datatype[len(str(XSD)):]
        if name in INTEGER_TYPES:
            valid = re.fullmatch(r"[+-]?[0-9]+", value)
        elif name == "decimal":
            valid = re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value)
        elif name in ("float", "double"):
            valid = re.fullmatch(r"(?:[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|-?INF|NaN)", value)
        elif name == "dateTime":
            valid = re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", value)
        elif name == "date":
            valid = re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})?", value)
        elif name == "time":
            valid = re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", value)
        elif name == "boolean":
            valid = value in ("true", "false", "1", "0")
        elif name == "base64Binary":
            # XML whitespace is legal between base64 symbols. Validate a
            # temporary view; the original RDF lexical spelling is retained.
            compact = re.sub(r"[ \t\r\n]", "", value)
            valid = re.fullmatch(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?", compact)
            if valid:
                try:
                    decoded = base64.b64decode(compact, validate=True)
                    # XML Schema also requires zero unused bits in a padded
                    # final unit; Python's decoder alone does not check them.
                    valid = base64.b64encode(decoded).decode("ascii") == compact
                except (ValueError, binascii.Error):
                    valid = False
        elif name in ("string", "anyURI", "duration", "dayTimeDuration", "yearMonthDuration", "hexBinary"):
            valid = True
        else:
            raise ValueError("Unsupported XSD lexical datatype profile")
        if not valid:
            raise ValueError("Invalid literal lexical form")
    elif datatype.startswith(str(RDF)):
        raise ValueError("Unsupported structured RDF literal profile")


def iri(value: str, *, internal=False) -> URIRef:
    if (not isinstance(value, str) or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
            or re.search(r'[\x00-\x20<>"{}|^`\\\ud800-\udfff]', value)
            or (value.startswith(INTERNAL) and not internal)):
        raise ValueError("Expected a public absolute IRI")
    return URIRef(value)


@dataclass(frozen=True)
class RdfTerm:
    kind: Kind["iri", "literal"]
    value: str
    datatype_iri: str | None = None
    language: str | None = None

    def __post_init__(self):
        if self.kind not in ("iri", "literal") or not isinstance(self.value, str):
            raise ValueError("Invalid RDF term")
        if self.kind == "iri":
            iri(self.value)
            if self.datatype_iri is not None or self.language is not None:
                raise ValueError("IRI cannot have literal qualifiers")
            return
        if "\x00" in self.value or any(0xD800 <= ord(c) <= 0xDFFF for c in self.value):
            raise ValueError("Invalid literal Unicode")
        if self.language is not None:
            if self.datatype_iri is not None or not re.fullmatch(r"[a-zA-Z]+(?:-[a-zA-Z0-9]+)*", self.language):
                raise ValueError("Invalid literal language/datatype combination")
            object.__setattr__(self, "language", self.language.lower())
        elif self.datatype_iri in (None, str(XSD.string)):
            # RDF 1.1 plain strings and explicit xsd:string are one logical term.
            object.__setattr__(self, "datatype_iri", str(XSD.string))
        else:
            iri(self.datatype_iri)
            if self.datatype_iri in (str(XSD.token), str(XSD.normalizedString), str(RDF.langString)):
                raise ValueError("Unsupported lexical datatype profile")
        _lexical(self.value, self.datatype_iri)
        term = Literal(self.value, lang=self.language, datatype=self.datatype_iri, normalize=False)
        if term.ill_typed or str(term) != self.value:
            raise ValueError("Invalid or lossy literal lexical form")

    def node(self):
        return iri(self.value) if self.kind == "iri" else Literal(
            self.value, datatype=self.datatype_iri, lang=self.language, normalize=False)


@dataclass(frozen=True)
class Triple:
    subject_iri: str
    predicate_iri: str
    object: RdfTerm

    def __post_init__(self):
        iri(self.subject_iri)
        iri(self.predicate_iri)
        if not isinstance(self.object, RdfTerm):
            raise ValueError("Triple object must be an RDF term")

    def nodes(self):
        return iri(self.subject_iri), iri(self.predicate_iri), self.object.node()


def term_from_node(node) -> RdfTerm:
    if isinstance(node, URIRef):
        return RdfTerm("iri", str(node))
    if isinstance(node, Literal):
        return RdfTerm("literal", str(node), str(node.datatype) if node.datatype else None, node.language)
    raise ValueError("Blank nodes require document-scoped canonical skolemization")


def triple_key(triple: Triple) -> str:
    return json.dumps(asdict(triple), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ordered(triples) -> tuple[Triple, ...]:
    return tuple(sorted(set(triples), key=triple_key))


def logical_digest(triples) -> str:
    content = "\n".join(triple_key(t) for t in ordered(triples))
    return hashlib.sha256(("lzo-rdf-set-v1\n" + content).encode()).hexdigest()


def canonical_query(query, *, init_ns=None, owned=False):
    """Normalize literal nodes in parsed query algebra, never regex query text.

    S05 must apply its authorization and query safety profile separately.
    owned=True asserts exclusive ownership of a freshly prepared child query;
    no shared/cached caller AST may use it. It avoids RDFLib's unsupported
    CompValue deepcopy without changing the existing default caller behavior.
    """
    import copy
    from rdflib.plugins.sparql import prepareQuery
    prepared = prepareQuery(query, initNs=init_ns) if isinstance(query, str) else (query if owned else copy.deepcopy(query))
    def normalize(value):
        if isinstance(value, Literal):
            return term_from_node(value).node()
        if isinstance(value, dict):
            for key, item in list(value.items()):
                value[key] = normalize(item)
            # RDFLib stores translated EXISTS graphs as an Expr attribute,
            # shadowing the original (now emptied) mapping entry.
            if getattr(value, "name", None) in {"Builtin_EXISTS", "Builtin_NOTEXISTS"} and "graph" in vars(value):
                value.graph = normalize(vars(value)["graph"])
        elif isinstance(value, list):
            return [normalize(item) for item in value]
        elif isinstance(value, tuple):
            return tuple(normalize(item) for item in value)
        return value
    normalize(prepared.algebra)
    return prepared


class LogicalGraph(Graph):
    def query(self, query_object, *args, **kwargs):
        return super().query(canonical_query(query_object, init_ns=kwargs.get("initNs")), *args, **kwargs)


def logical_graph(triples) -> Graph:
    graph = LogicalGraph()
    for triple in triples:
        graph.add(triple.nodes())
    return graph
