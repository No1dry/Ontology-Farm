"""Language terms cross the real published-definition and adoption boundary."""
import io
from uuid import UUID
import pytest
from sqlalchemy import select
from test_schema_releases import schema_case
from test_ingestion_rule_versions import configuration_case
from test_ingestion_review_boundaries import publish


@pytest.fixture
def object_case(schema_case):
    from lzo.worker.runner import Runner
    from lzo.ontology.models import bundles
    s=schema_case;s.grant('alice','editor',scope='farm_a')
    draft=s.create({'classes':[{'iri':'urn:Pond','label':'Pond'}],
        'properties':[{'iri':'urn:area','label':'Localized name',
            'datatype_iri':'http://www.w3.org/1999/02/22-rdf-syntax-ns#langString',
            'owner_policy_iri':'urn:lzo:authority:source_backed'},
            {'iri':'urn:note','label':'Note','datatype_iri':'http://www.w3.org/2001/XMLSchema#string',
                'owner_policy_iri':'urn:lzo:authority:ontology_owned'}]})
    assert s.publish(draft,s.approve(draft)).status_code==202
    assert Runner(s.app,s.config).run_once()
    with s.transaction() as uow:s.bundle_id=uow.session.execute(select(bundles.c.id)).scalar_one()
    s.login();return s


@pytest.mark.parametrize('fmt',['turtle','json-ld'])
def test_language_literals_adopt_under_langstring_and_reject_incompatible_terms(configuration_case,fmt):
    from lzo.ingestion.sources import SourceService,digest
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import FieldMap,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.governance.approvals import ApprovalService
    from lzo.semantic.facts import current_assertions
    from lzo.semantic.projection import ProjectionService,graph_iri
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'identity_paths':('subject.value',),'name_path':'subject.value','fields':(
                FieldMap(source_path='terms.'+digest('urn:area'),predicate_iri='urn:area',term_kind='rdf',multiple=True),)}))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),s.i02_authority.id,s.i02_ruling.id);uow.commit()
    publish(s,bundle,s.i02_authority)
    def preview(content):
        with s.transaction() as uow:
            uow.ontology_settings=s.config;store=store_for(uow)
            blob=store.put(io.BytesIO(content),'text/turtle' if fmt=='turtle' else 'application/ld+json')
            ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,
                filename='language.rdf',metadata={})
            report=IngestionService(uow).preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(
                source_id=s.i02_source.id,upload=blob,semantic_format=fmt,semantic_kind='instances',mode='full'),bundle_id=bundle.id))
            uow.commit();return report
    invalid=preview(b'<urn:invalid> a <urn:Pond>; <urn:area> "2.0"^^<http://www.w3.org/2001/XMLSchema#decimal>.' if fmt=='turtle' else
        b'{"@id":"urn:invalid","@type":"urn:Pond","urn:area":{"@value":"2.0","@type":"http://www.w3.org/2001/XMLSchema#decimal"}}')
    assert invalid.invalid_count==1 and invalid.ready_count==0
    report=preview(b'<urn:pond> a <urn:Pond>; <urn:area> "pond"@en, "etang"@fr.' if fmt=='turtle' else
        b'{"@id":"urn:graph:language","@graph":[{"@id":"urn:pond","@type":"urn:Pond","urn:area":[{"@value":"pond","@language":"en"},{"@value":"etang","@language":"fr"}]}]}')
    assert report.ready_count==1
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Independent language evidence')
        job=IngestionService(uow).apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    runner=Runner(s.app,s.config)
    for _ in range(180):
        with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if state in ('succeeded','failed','paused'):break
        assert runner.run_once()
    assert state=='succeeded'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        row=IngestionService(uow).rows(s.actor('alice'),job.id).items[0]
        assertions=current_assertions(uow,row.entity_id)
        assert {(a['payload']['object']['value'],a['payload']['object']['language']) for a in assertions}=={('pond','en'),('etang','fr')}
        assert len({a['supports'][0]['id'] for a in assertions})==2
        for assertion in assertions:
            provenance=assertion['supports'][0]['payload']
            assert provenance['source_id']==str(s.i02_source.id)
            assert provenance['snapshot']['version_id']==str(report.selection.upload.version_id)
            assert provenance['raw_value']['semantic']['subject']['value']=='urn:pond'
            graph=provenance['raw_value']['semantic']['graph']
            assert graph is None if fmt=='turtle' else graph['kind']=='iri' and graph['value']=='urn:graph:language'
        repo=FusekiRepository(s.config.fuseki_url)
        assert ProjectionService(uow,repo,actor=s.actor('alice')).project_revision(row.revision).state=='synced';uow.commit()
    from lzo.semantic.facts import object_iri
    from lzo.semantic.rdf import Triple,RdfTerm
    from rdflib.namespace import RDF
    expected={Triple(object_iri(row.entity_id),str(RDF.type),RdfTerm('iri','urn:Pond')),
        *(Triple(object_iri(row.entity_id),'urn:area',RdfTerm('literal',value,language=language))
            for value,language in (('pond','en'),('etang','fr')))}
    assert set(repo.read_graph(graph_iri(row.entity_id,'accepted')))==expected
    from ingestion_export_assertions import assert_exported_triples
    assert_exported_triples(s,runner,[row.entity_id],fmt,expected)
