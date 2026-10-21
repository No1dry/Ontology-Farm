"""Actual OpenAPI exposes widened query terms and neighborhood derived edges."""
def test_openapi_query_export_and_proof_contracts(settings):
    from lzo.main import create_app
    app=create_app(settings)
    try:
        document=app.openapi()
    finally:
        app.state.engine.dispose()
    paths=document['paths']
    for path,verb,operation in (
        ('/api/v1/query/sparql','post','executeSparql'),
        ('/api/v1/query/exports','post','createSemanticExport'),
        ('/api/v1/graph/neighborhood','get','getGraphNeighborhood'),
        ('/api/v1/graph/proofs/{assertion_id}','get','getAssertionProof')):
        assert paths[path][verb]['operationId']==operation
    schemas=document['components']['schemas']
    assert schemas['QueryBlankNode']['properties']['kind']['const']=='bnode'
    assert {v['$ref'].rsplit('/',1)[-1] for v in schemas['QueryTriple']['properties']['object']['anyOf']}=={'RdfTerm','QueryBlankNode'}
    edges=schemas['GraphNeighborhood']['properties']['edges']['items']['anyOf']
    assert {v['$ref'].rsplit('/',1)[-1] for v in edges}=={'LinkView','DerivedLinkView'}
    assert schemas['DerivedLinkView']['properties']['state']['const']=='derived'
    assert schemas['ExportRequest']['additionalProperties'] is False
