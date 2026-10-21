"""Real offline semantic bytes retain graph/term/document identities."""
import io
from datetime import datetime, timezone
from importlib.util import find_spec
from uuid import uuid4
import pytest
from lzo.artifacts.store import ArtifactStore


@pytest.mark.parametrize("fmt",["turtle","json-ld"])
def test_semantic_normalization_retains_all_terms_and_source_blank_scope(tmp_path,fmt):
    assert find_spec("lzo.ingestion.semantic_files") is not None, "Authenticated semantic normalizer missing"
    from lzo.ingestion.semantic_files import SemanticFileParser
    from lzo.ingestion.schemas import SemanticFileOptions, UploadBinding, SourceKey
    import json
    raw=(b'<urn:pond> a <urn:Pond>; <urn:tag> "01.00"^^<http://www.w3.org/2001/XMLSchema#decimal>, "pond"@en, "etang"@fr; <urn:next> _:b. _:b a <urn:Pond>.'
        if fmt=="turtle" else json.dumps([{"@id":"urn:pond","@type":"urn:Pond","urn:tag":[
            {"@value":"01.00","@type":"http://www.w3.org/2001/XMLSchema#decimal"},
            {"@value":"pond","@language":"en"},{"@value":"etang","@language":"fr"}],
            "urn:next":{"@id":"_:b"}},{"@id":"_:b","@type":"urn:Pond"}]).encode())
    store=ArtifactStore(tmp_path,max_upload_bytes=1000000)
    blob=store.put(io.BytesIO(raw),"text/turtle" if fmt=="turtle" else "application/ld+json")
    options=SemanticFileOptions(format=fmt,data_nature="synthetic")
    source=uuid4()
    binding=UploadBinding(parser_contract="semantic-v1",id=uuid4(),source_key=SourceKey(source_id=source,
        source_system="test",source_instance="one",collection="rdf",canonical_key=""),source_revision=1,
        blob=blob,options=options,epoch=uuid4(),received_at=datetime.now(timezone.utc))
    first=SemanticFileParser(store,binding).parse()
    assert first.complete and len(first.changes)==2
    assert first==SemanticFileParser(store,binding).parse()
    pond=next(c for c in first.changes if c.raw_fields["subject"]["value"]=="urn:pond")
    assert pond.snapshot==blob and pond.raw_fields["graph"] is None
    terms=pond.raw_fields["predicates"]["urn:tag"]
    assert len(terms)==3 and {t["value"] for t in terms}=={"01.00","pond","etang"}
    assert next(t for t in terms if t["value"]=="pond")["language"]=="en"
    assert pond.valid_from is None and pond.observed_at is None
    blank=next(c for c in first.changes if c!=pond)
    assert blank.raw_fields["subject"]["kind"]=="bnode"
    assert str(source) in blank.raw_fields["subject"]["value"] and str(blob.version_id) in blank.raw_fields["subject"]["value"]
    other=binding.model_copy(update={"source_key":binding.source_key.model_copy(update={"source_id":uuid4()})})
    other_blank=next(c for c in SemanticFileParser(store,other).parse().changes if c.raw_fields["subject"]["kind"]=="bnode")
    assert other_blank.key!=blank.key
