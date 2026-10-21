"""Public semantic options bind exact source-time literals and original graph."""
import io,json
from sqlalchemy import select
from test_ingestion_rule_versions import configuration_case
from test_ingestion_review_boundaries import publish


def test_http_semantic_temporal_options_normalize_exact_literals(configuration_case):
    from lzo.ingestion.sources import SourceService,digest
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import FieldMap
    from lzo.ingestion.models import source_records
    from lzo.ontology.drafts import store_for
    from lzo.artifacts.service import ArtifactService
    from lzo.semantic.facts import json_value
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'identity_paths':('subject.value',),'name_path':'subject.value','fields':(
                FieldMap(source_path='terms.'+digest('urn:area'),predicate_iri='urn:area',term_kind='rdf',multiple=True),)}))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),s.i02_authority.id,s.i02_ruling.id);uow.commit()
    publish(s,bundle,s.i02_authority)
    document={'@id':'urn:original-graph','@graph':[{'@id':'urn:pond','@type':'urn:Pond',
        'urn:area':{'@value':'01.00','@type':'http://www.w3.org/2001/XMLSchema#decimal'},
        'urn:starts':{'@value':'2026-01-01T08:00:00','@type':'http://www.w3.org/2001/XMLSchema#dateTime'},
        'urn:observed':{'@value':'2026-01-01T09:00:00+08:00','@type':'http://www.w3.org/2001/XMLSchema#dateTime'}}]}
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(json.dumps(document).encode()),'application/ld+json')
        ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,filename='timed.jsonld',metadata={});uow.commit()
    command={'selection':{'source_id':str(s.i02_source.id),'upload':json_value(blob),'mode':'full',
        'semantic_format':'json-ld','semantic_kind':'instances','file_options':{'format':'json-ld','data_nature':'verified',
            'source_timezone':'Asia/Shanghai','temporal_fields':{'valid_from_path':'terms.'+digest('urn:starts'),
                'observed_at_path':'terms.'+digest('urn:observed')}}},'bundle_id':str(bundle.id)}
    s.login()
    response=s.client.post('/api/v1/imports/preview',headers=s.headers(),json=command)
    assert response.status_code==201,response.text
    assert response.json()['ready_count']==1,response.json()
    with s.transaction() as uow:
        raw=uow.session.execute(select(source_records.c.payload)).scalar_one()
        assert raw['valid_from']=='2026-01-01T00:00:00Z' and raw['observed_at']=='2026-01-01T01:00:00Z'
        assert raw['received_at']!=raw['valid_from'] and raw['data_nature']=='synthetic'
        assert raw['raw_fields']['graph']=={'kind':'iri','value':'urn:original-graph','datatype_iri':None,'language':None}
        assert raw['raw_fields']['predicates']['urn:area'][0]['value']=='01.00'
    wrong={**command,'selection':{**command['selection'],'semantic_format':'turtle'}}
    assert s.client.post('/api/v1/imports/preview',headers=s.headers(),json=wrong).status_code==422
    draft={**command,'selection':{**command['selection'],'semantic_kind':'schema'}}
    assert s.client.post('/api/v1/imports/preview',headers=s.headers(),json=draft).status_code==422
    # The real public intake delegates to the bounded offline parser.
    for content in ({'@context':'https://invalid.example/context','@id':'urn:x'},
                    {'@id':'urn:x','urn:starts':{'@id':'urn:not-a-timestamp'}}):
        with s.transaction() as uow:
            uow.ontology_settings=s.config;store=store_for(uow)
            bad=store.put(io.BytesIO(json.dumps(content).encode()),'application/ld+json')
            ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],bad,
                filename='rejected.jsonld',metadata={});uow.commit()
        rejected={**command,'selection':{**command['selection'],'upload':json_value(bad)}}
        assert s.client.post('/api/v1/imports/preview',headers=s.headers(),json=rejected).status_code==422
