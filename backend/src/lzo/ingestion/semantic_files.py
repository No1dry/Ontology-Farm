"""semantic-v1: canonical (subject, graph) records in the existing raw registry.

subject/graph retain the original term kind; blank values have source, original
artifact version, canonical dataset digest and canonical local ID scope. The
predicates object retains *every* lexical term. `terms.<sha256(predicate)>` is
the equivalent mapping-path view, avoiding punctuation-sensitive IRI paths.
Temporal paths select a scalar source value explicitly; receipt time is separate.
No parsed triple is an accepted fact and no external IRI is a platform identity.
"""
from dataclasses import asdict
import re
from uuid import uuid5
from rdflib import BNode, URIRef
from lzo.ingestion.schemas import ParsedFile, ParseIssue, SourceChange, SourcePosition
from lzo.ingestion.sources import canonical, digest
from lzo.semantic.files import (parse_semantic_document, semantic_file_context, skolemize,
    reverse_skolem, MAX_QUADS, SemanticExecutionIncomplete)
from lzo.semantic.rdf import term_from_node


def temporal_literal(raw,path):
    """Exact scalar accessor for configured semantic business timestamps."""
    if not re.fullmatch(r"terms\.[0-9a-f]{64}",path):
        raise ValueError("Semantic temporal path must identify a predicate digest")
    values=raw.get("terms",{}).get(path[6:])
    if (not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict)
            or values[0].get("kind")!="literal" or not isinstance(values[0].get("value"),str)):
        raise ValueError("Semantic timestamp requires exactly one literal")
    return values[0]["value"]


class SemanticFileParser:
    def __init__(self,store,binding):self.store,self.binding=store,binding

    def parse(self):
        binding=self.binding
        try:
            if binding.parser_contract!="semantic-v1":raise ValueError("Unsupported semantic binding")
            with semantic_file_context(self.store):
                parsed=parse_semantic_document(binding.blob,binding.options.format)
                document=skolemize(parsed)
            dataset=reverse_skolem(document)
            def term(value):
                if isinstance(value,BNode):
                    return {"kind":"bnode","value":f"urn:lzo:source-blank:{binding.source_key.source_id}:{binding.blob.version_id}:{document.canonical_digest}:{value}"}
                return asdict(term_from_node(value))
            groups={};count=0
            for subject,predicate,value,graph in dataset.quads():
                count+=1
                if count>MAX_QUADS:raise ValueError("Normalized quad budget exceeded")
                subject=term(subject)
                if subject["kind"]=="literal":raise ValueError("Literal subject")
                graph=None if graph==dataset.default_graph.identifier else term(graph)
                key=canonical({"subject":subject,"graph":graph})
                row=groups.setdefault(key,{"contract":"semantic-v1","subject":subject,"graph":graph,"predicates":{},
                    "canonical_digest":document.canonical_digest,"original_digest":binding.blob.sha256,"canonicalization":document.algorithm})
                values=row["predicates"].setdefault(str(predicate),{})
                normalized=term(value);values[canonical(normalized)]=normalized
                if len(values)>1024:raise ValueError("Predicate value budget exceeded")
            changes=[];size=0
            for ordinal,key in enumerate(sorted(groups),1):
                raw=groups[key]
                raw["predicates"]={p:[values[k] for k in sorted(values)] for p,values in sorted(raw["predicates"].items())}
                raw["terms"]={digest(p):values for p,values in raw["predicates"].items()}
                temporal={"valid_from":None,"valid_to":None,"observed_at":None}
                if binding.options.temporal_fields:
                    from lzo.ingestion.datetimes import source_datetime
                    raw["temporal_paths"]={}
                    for name in temporal:
                        path=getattr(binding.options.temporal_fields,name+"_path")
                        if path:
                            temporal[name]=source_datetime(temporal_literal(raw,path),binding.options.source_timezone)
                            raw["temporal_paths"][name]=path
                if temporal["valid_from"] and temporal["valid_to"] and temporal["valid_to"]<=temporal["valid_from"]:
                    raise ValueError("Invalid temporal interval")
                locator="rdf:"+digest(key)
                content=digest(raw);identity=digest({"epoch":str(binding.epoch),"key":key,"content":content})
                change=SourceChange(change_id=uuid5(binding.epoch,identity),key=binding.source_key.model_copy(update={"canonical_key":key}),
                    operation="upsert",position=SourcePosition(stream_id=str(binding.source_key.source_id),epoch=binding.epoch,
                        ordinal=ordinal,source_version=binding.blob.sha256,cursor={"locator":locator}),**temporal,
                    received_at=binding.received_at,snapshot=binding.blob,content_sha256=content,idempotency_key=identity,
                    raw_fields=raw,geometry=None,data_nature=binding.options.data_nature,locator=locator)
                size+=len(change.model_dump_json().encode())
                if size>16*1024*1024:raise ValueError("Normalized output budget exceeded")
                changes.append(change)
            return ParsedFile(changes=tuple(changes),issues=(),complete=True)
        except (ValueError,TypeError,KeyError,SemanticExecutionIncomplete):
            return ParsedFile(changes=(),issues=(ParseIssue(locator="document",field_path=None,
                code="SEMANTIC_DOCUMENT_INVALID",message="Semantic document could not be completely normalized"),),complete=False)
