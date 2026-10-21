"""Public commands and server-computed import results remain distinct."""
def test_import_openapi_marks_server_results_readonly(settings):
    from lzo.main import create_app
    app=create_app(settings)
    try:document=app.openapi()
    finally:app.state.engine.dispose()
    from pathlib import Path
    import json
    snapshot=Path(__file__).resolve().parents[3]/'contracts/openapi.json'
    assert json.loads(snapshot.read_text(encoding='utf-8'))==document, 'Tracked contract must match this backend'
    assert snapshot.read_bytes()==(json.dumps(document,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf-8')
    for path,verb,operation in (
        ('/imports/preview','post','previewImport'),('/imports/apply','post','applyImport'),
        ('/imports/previews/{id}','get','getImportPreview'),
        ('/imports/previews/{id}/rows','get','listImportPreviewRows'),
        ('/imports/{id}','get','getImport'),('/imports/{id}/rows','get','listImportRows')):
        assert document['paths']['/api/v1'+path][verb]['operationId']==operation
    schemas=document['components']['schemas']
    for name in ('PreviewReport','RowResult','ImportRunView'):
        assert all(value.get('readOnly') is True for value in schemas[name]['properties'].values()),name
    for name in ('ImportPreviewCommand','ApplyImportCommand','ImportSelection'):
        assert schemas[name]['additionalProperties'] is False
    assert '202' in document['paths']['/api/v1/imports/preview']['post']['responses']
    options=schemas['ImportSelection']['properties']['file_options']['anyOf']
    assert {v['$ref'].rsplit('/',1)[-1] for v in options if '$ref' in v}=={'FileOptions','SemanticFileOptions'}
