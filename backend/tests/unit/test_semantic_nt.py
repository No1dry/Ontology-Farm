import pytest
from rdflib import BNode, URIRef


@pytest.mark.parametrize('newline', ['\n', '\r', '\r\n'])
def test_linear_nt_framing_preserves_terms_and_parse_local_blank_nodes(newline):
    from lzo.semantic.nt import parse_nt
    data=newline.join(['', '# comment', '_:a <urn:p> "001"^^<http://www.w3.org/2001/XMLSchema#integer> .',
        '_:a <urn:q> "hé\\nllo\u2028world"@fr .', '_:b <urn:p> _:a .']).encode()
    graph=parse_nt(data)
    assert len(graph)==3
    a=next(graph.subjects(URIRef('urn:q'),None))
    assert isinstance(a,BNode)
    assert str(graph.value(a,URIRef('urn:p')))=='001'
    value=graph.value(a,URIRef('urn:q'))
    assert str(value)=='hé\nllo\u2028world' and value.language=='fr'
    assert next(graph.objects(None,URIRef('urn:p'))) is not None
    assert (None,URIRef('urn:p'),a) in graph
    assert a not in set(parse_nt(data).subjects())
    with pytest.raises(UnicodeDecodeError):
        parse_nt(b'<urn:s> <urn:p> "\xff" .')


def test_owned_nt_parser_preserves_long_literal_and_bounds_output():
    from lzo.semantic.query_process import run_child
    from lzo.errors import ApiError
    text='hello'+'x'*(1024*1024)
    payload={'operation':'parse_nt','nt':f'<urn:s> <urn:p> "{text}" .','output_bytes':4*1024*1024}
    options=dict(timeout_ms=10000,memory_mb=512,input_bytes=4*1024*1024,output_bytes=4*1024*1024)
    result=run_child(payload,**options)
    assert result['triples'][0][2]['value']==text
    with pytest.raises(ApiError) as error:
        run_child({**payload,'output_bytes':100},**options)
    assert error.value.code=='SPARQL_BUDGET_EXCEEDED'


@pytest.mark.parametrize('response_limit,total_limit,successful_reads',[(100,70,2),(10,100,0)])
def test_repository_scoped_acquisition_counts_each_response_and_checks_encoding(monkeypatch,response_limit,total_limit,successful_reads):
    from types import SimpleNamespace
    import httpx
    from lzo.semantic.query_read import ReadBudget
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.errors import ApiError
    settings=SimpleNamespace(query_timeout_ms=10000,query_read_response_bytes=response_limit,
        query_read_total_bytes=total_limit,query_parse_output_bytes=4096,query_memory_mb=512)
    budget=ReadBudget(settings)
    body=b'<urn:s> <urn:p> <urn:o> .'
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,stream=httpx.ByteStream(body))
    client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx,'stream',lambda method,url,**kw:client.stream(method,url,
        **{k:v for k,v in kw.items() if k not in ('trust_env','verify')}))
    repo=FusekiRepository('http://fixture',read_budget=budget,parse_nt=budget.parse_nt)
    for _ in range(successful_reads):
        assert len(repo._get('urn:g'))==1
    with pytest.raises(ApiError) as error:
        repo._get('urn:g')
        repo._get('urn:g')
    assert error.value.code=='SPARQL_BUDGET_EXCEEDED'
    assert all(r.headers['accept-encoding']=='identity' for r in calls)
    client.close()


def test_acquisition_deadline_checks_native_tiny_chunks_before_aggregation(monkeypatch):
    from types import SimpleNamespace
    import httpx
    from lzo.semantic import query_read
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.errors import ApiError
    settings=SimpleNamespace(query_timeout_ms=10000,query_read_response_bytes=1000,
        query_read_total_bytes=1000,query_parse_output_bytes=4096,query_memory_mb=512)
    now,yielded=[0.0],[]
    monkeypatch.setattr(query_read.time,'monotonic',lambda:now[0])
    budget=query_read.ReadBudget(settings)
    class Tiny(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                now[0]+=1
                yielded.append(i)
                yield b' '
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Tiny())))
    monkeypatch.setattr(httpx,'stream',lambda method,url,**kw:client.stream(method,url,
        **{k:v for k,v in kw.items() if k not in ('trust_env','verify')}))
    with pytest.raises(ApiError) as error:
        FusekiRepository('http://fixture',read_budget=budget,parse_nt=budget.parse_nt)._get('urn:g')
    assert error.value.code=='SPARQL_TIMEOUT'
    assert len(yielded)==10
    assert budget.acquired==9
    client.close()


@pytest.mark.filterwarnings('error::pytest.PytestUnhandledThreadExceptionWarning')
def test_owned_nt_timeout_cancel_memory_reap_actual_children(monkeypatch):
    import subprocess,psutil
    from lzo.semantic import query_process
    from lzo.errors import ApiError
    original,owned=subprocess.Popen,[]
    def launch(*args,**kwargs):
        child=original(*args,**kwargs)
        owned.append(child)
        return child
    monkeypatch.setattr(query_process.subprocess,'Popen',launch)
    payload={'operation':'parse_nt','nt':'<urn:s> <urn:p> "hello" .','output_bytes':4096}
    base=dict(timeout_ms=5000,memory_mb=512,input_bytes=32*1024*1024,output_bytes=4096)
    for options,code in [({'timeout_ms':20},'SPARQL_TIMEOUT'),
            ({'cancelled':lambda: len(owned)>1},'SPARQL_CANCELLED'),
            ({'memory_mb':1},'SPARQL_MEMORY_EXCEEDED')]:
        with pytest.raises(ApiError) as error:
            value={**payload,'nt':'<urn:s> <urn:p> "'+('x'*(2*1024*1024))+'" .'} if 'cancelled' in options else payload
            query_process.run_child(value,**{**base,**options})
        assert error.value.code==code
    assert len(owned)==3
    assert all(p.poll() is not None and not psutil.pid_exists(p.pid) for p in owned)


def test_scoped_acquisition_rejects_encoded_body_and_shared_expired_deadline(monkeypatch):
    from types import SimpleNamespace
    import httpx
    from lzo.semantic import query_read
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.errors import ApiError
    settings=SimpleNamespace(query_timeout_ms=10000,query_read_response_bytes=100,
        query_read_total_bytes=100,query_parse_output_bytes=4096,query_memory_mb=512)
    now=[10.0]
    monkeypatch.setattr(query_read.time,'monotonic',lambda:now[0])
    budget=query_read.ReadBudget(settings)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,headers={'Content-Encoding':'gzip'},stream=httpx.ByteStream(b'compressed'))
    client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx,'stream',lambda method,url,**kw:client.stream(method,url,
        **{k:v for k,v in kw.items() if k not in ('trust_env','verify')}))
    repo=FusekiRepository('http://fixture',read_budget=budget,parse_nt=budget.parse_nt)
    with pytest.raises(ApiError) as encoded:
        repo._get('urn:g')
    assert encoded.value.code=='SPARQL_ACQUISITION_INCOMPLETE'
    now[0]=21
    with pytest.raises(ApiError) as expired:
        repo.select('ASK {}',10000)
    assert expired.value.code=='SPARQL_TIMEOUT' and len(calls)==1
    client.close()


@pytest.mark.parametrize('timeout,expected',[(1,'SPARQL_TIMEOUT'),(1000,'NOT_FOUND')])
def test_proof_metadata_precheck_consumes_same_deadline(monkeypatch,timeout,expected):
    import time
    from types import SimpleNamespace
    from lzo.semantic import proofs
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    # Isolate the temporal precheck contract; real metadata/proof selection is
    # covered by the current visible-alternative integration case.
    def slow_metadata(*args):
        time.sleep(.02)
        return None
    monkeypatch.setattr(proofs,'current_metadata',slow_metadata)
    uow=SimpleNamespace(ontology_settings=SimpleNamespace(query_timeout_ms=timeout))
    with permission_context(uow),pytest.raises(ApiError) as error:
        proofs.current_proof(None,None)
    assert error.value.code==expected


def test_unexpected_owned_pipe_close_error_is_incomplete_and_reaped(monkeypatch):
    import errno,subprocess,psutil
    from lzo.semantic import query_process
    from lzo.errors import ApiError
    original,owned=subprocess.Popen,[]
    def launch(*args,**kwargs):
        child=original(*args,**kwargs)
        owned.append(child)
        stream=child.stdin
        class FaultyClose:
            write=stream.write
            flush=stream.flush
            def close(self):
                stream.close()
                raise OSError(errno.EIO,'injected owned pipe close failure')
        child.stdin=FaultyClose()
        return child
    monkeypatch.setattr(query_process.subprocess,'Popen',launch)
    with pytest.raises(ApiError) as error:
        query_process.run_child({'operation':'parse_nt','nt':'<urn:s> <urn:p> <urn:o> .','output_bytes':4096},
            timeout_ms=5000,memory_mb=512,input_bytes=4096,output_bytes=4096)
    assert error.value.code=='SPARQL_EXECUTION_INCOMPLETE'
    assert len(owned)==1 and owned[0].poll() is not None and not psutil.pid_exists(owned[0].pid)
