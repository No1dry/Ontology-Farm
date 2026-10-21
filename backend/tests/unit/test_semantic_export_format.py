"""Actual export encoders preserve RDF dataset identity, lexicals and graph roles."""
import hashlib
import io
import json
import zipfile
from uuid import uuid4

import pytest
from rdflib import BNode, Dataset, Literal, URIRef
from rdflib.plugins.parsers.ntriples import W3CNTriplesParser, NTGraphSink
from lzo.semantic.exports import ExportRequest, serialize_export
from lzo.semantic.files import _canonical, nquads, parse_text


@pytest.mark.parametrize('format', ['turtle','json-ld'])
def test_dataset_roundtrip_preserves_shared_blank_nodes_language_and_identical_spo_layers(format):
    original=Dataset()
    shared=BNode('one-dataset-node')
    for role in ('accepted','candidate'):
        graph=original.graph(URIRef('urn:lzo:query:'+role))
        graph.add((URIRef('urn:root'),URIRef('urn:detail'),shared))
        graph.add((shared,URIRef('urn:label'),Literal('bonjour',lang='fr')))
        graph.add((shared,URIRef('urn:value'),Literal('001',datatype=URIRef('http://www.w3.org/2001/XMLSchema#integer'),normalize=False)))
    request=ExportRequest(resource_ids=(uuid4(),),format=format,layout='dataset')
    raw,media,name=serialize_export(original,request,100000)
    if format=='json-ld':
        restored=parse_text(raw.decode(),'json-ld')
    else:
        restored=Dataset(); labels={}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            assert manifest['blank_node_scope']=='dataset'
            for entry in manifest['graphs']:
                data=archive.read(entry['file'])
                assert hashlib.sha256(data).hexdigest()==entry['sha256']
                # Files use the N-Triples Turtle subset. One shared parser bnode
                # context obeys the manifest's explicit dataset label scope.
                W3CNTriplesParser(NTGraphSink(restored.graph(URIRef(entry['iri'])))).parse(
                    io.StringIO(data.decode()),bnode_context=labels)
    assert _canonical(nquads(restored))==_canonical(nquads(original))
    assert len(list(restored.quads()))==6
