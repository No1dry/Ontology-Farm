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


def test_selected_telemetry_reference_and_object_recovery_contract(settings):
    from lzo.main import create_app
    app = create_app(settings)
    try:
        document = app.openapi()
    finally:
        app.state.storage_registry.close()
    schemas = document['components']['schemas']
    assert schemas['SourceSupport']['properties']['evidence_refs']['items']['$ref'].endswith('/RevisionRef')
    assert {'resource_id', 'revision', 'event_id'} <= set(schemas['RevisionRef']['required'])
    parameters = document['paths']['/api/v1/objects/{id}/data']['get']['parameters']
    assert {'observations_start', 'observations_end', 'observations_device_id', 'observations_metric_id'} <= {p['name'] for p in parameters}
    page = schemas['TelemetryPage']['properties']
    assert 'incomplete' in page['status']['enum']
    assert 'query_url' in page and 'incomplete_reason' in page
