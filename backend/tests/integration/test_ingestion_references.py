"""Reversed source order still validates full predecessor rows before adoption."""
import io
import pytest
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case,version
from test_ingestion_review_boundaries import publish


@pytest.mark.parametrize('object_case',['recursive'],indirect=True)
@pytest.mark.parametrize('reference_kind',['iri','blank'])
@pytest.mark.parametrize('fmt',['turtle','json-ld'])
def test_new_acyclic_reference_preview_is_rollback_only_and_resumes(configuration_case,reference_kind,fmt):
    from lzo.ingestion.sources import SourceService,digest
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import FieldMap,AuthorityRule,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.models import source_evaluations,import_run_rows
    from lzo.ontology.drafts import store_for
    from lzo.artifacts.service import ArtifactService
    from lzo.semantic.models import objects,supports
    from lzo.governance.approvals import ApprovalService
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    s=configuration_case
    if reference_kind=='blank':
        s.config=s.config.model_copy(update={'ingestion_preview_sync_bytes':1,'ingestion_preview_rows_per_batch':1})
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None and getattr(error,'code',None)!='JOB_YIELD':raise error
    runner=DiagnosticRunner(s.app,s.config.model_copy(update={'job_max_batches_per_run':1}))
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'identity_paths':('subject.value',),'name_path':'subject.value','fields':(
                FieldMap(source_path='terms.'+digest('urn:area'),predicate_iri='urn:area',term_kind='rdf',multiple=True),
                FieldMap(source_path='terms.'+digest('urn:next'),predicate_iri='urn:next',term_kind='reference',multiple=True))}))
        authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            *s.i02_authority.authority_rules,AuthorityRule(predicate_iri='urn:next',owner_policy='source_backed',
                owner_source_ids=(s.i02_source.id,),digest=''))))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,s.i02_ruling.id);uow.commit()
    publish(s,bundle,authority)
    raw=b'@prefix xsd:<http://www.w3.org/2001/XMLSchema#>. <urn:a> a <urn:Pond>; <urn:area> "1"^^xsd:decimal; <urn:next> <urn:z>. <urn:z> a <urn:Pond>; <urn:area> "2"^^xsd:decimal.'
    if reference_kind=='blank':raw=raw.replace(b'<urn:z>',b'_:z')
    if fmt=='json-ld':
        import json
        target='_:z' if reference_kind=='blank' else 'urn:z'
        raw=json.dumps({'@id':'urn:graph:chain','@graph':[
            {'@id':'urn:a','@type':'urn:Pond','urn:area':{'@value':'1','@type':'http://www.w3.org/2001/XMLSchema#decimal'},'urn:next':{'@id':target}},
            {'@id':target,'@type':'urn:Pond','urn:area':{'@value':'2','@type':'http://www.w3.org/2001/XMLSchema#decimal'}}]}).encode()
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(raw),'text/turtle' if fmt=='turtle' else 'application/ld+json')
        ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,filename='chain.rdf',metadata={})
        before=uow.session.execute(select(func.count()).select_from(jobs)).scalar_one()
        # Existing context must remain unchanged, including absent keys.
        from copy import deepcopy
        from lzo.semantic.facts import revision_ref
        uow.facts_explicit_pins={revision_ref(uow,s.i02_source.id)}
        original=deepcopy(uow.facts_explicit_pins)
        assert not hasattr(uow,'ingestion_decision_stack')
        command=ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,upload=blob,
            semantic_format=fmt,semantic_kind='instances',mode='full'),bundle_id=bundle.id)
        if reference_kind=='blank':
            from lzo.ingestion.preview_jobs import dispatch
            planning_job=dispatch(uow,s.actor('alice'),command)
        else:report=IngestionService(uow).preview(s.actor('alice'),command)
        assert uow.session.execute(select(func.count()).select_from(objects)).scalar_one()==0
        assert uow.session.execute(select(func.count()).select_from(source_evaluations)).scalar_one()==0
        assert uow.session.execute(select(func.count()).select_from(supports)).scalar_one()==0
        assert uow.session.execute(select(func.count()).select_from(jobs)).scalar_one()==before+(reference_kind=='blank')
        assert uow.facts_explicit_pins==original and not hasattr(uow,'ingestion_decision_stack')
        uow.commit()
    if reference_kind=='blank':
        from lzo.ingestion.models import preview_work_rows,import_previews,import_authorizations
        from uuid import UUID
        planned=[]
        for _ in range(200):
            with s.transaction() as uow:
                current=uow.session.execute(select(jobs).where(jobs.c.id==planning_job.id)).mappings().one()
                planned.append(uow.session.execute(select(func.count()).select_from(preview_work_rows).where(
                    preview_work_rows.c.job_id==planning_job.id,preview_work_rows.c.payload.is_not(None))).scalar_one())
                for table in (objects,supports,source_evaluations):
                    assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==0
            if current['status'] in ('succeeded','failed','paused'):break
            assert runner.run_once()
        assert current['status']=='succeeded' and {0,1,2}<=set(planned)
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            report=IngestionService(uow).get_preview(s.actor('alice'),UUID(current['result_ref']['resource_id']))
            for table in (import_previews,import_authorizations):
                assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==1
            assert uow.session.execute(select(func.count()).select_from(jobs)).scalar_one()==before+1
    assert report.ready_count==2 and report.review_count==report.invalid_count==0,[r.model_dump() for r in report.rows]
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Complete acyclic rows')
        job=IngestionService(uow).apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    observed=[]
    for _ in range(200):
        with s.transaction() as uow:
            state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
            observed.append(uow.session.execute(select(func.count()).select_from(import_run_rows).where(import_run_rows.c.run_id==job.id)).scalar_one())
        if state in ('succeeded','failed','paused'):break
        assert runner.run_once()
    assert state=='succeeded' and 1 in observed
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        rows=IngestionService(uow).rows(s.actor('alice'),job.id).items
        assert all(row.state=='applied' for row in rows)
        from lzo.semantic.facts import current_assertions,object_iri
        first,second=rows
        relation=[a for a in current_assertions(uow,second.entity_id) if a['payload']['predicate_iri']=='urn:next']
        assert len(relation)==1 and relation[0]['payload']['object']['value']==object_iri(first.entity_id)
        origin=relation[0]['supports'][0]['payload']['raw_value']['term']
        assert origin['kind']==('bnode' if reference_kind=='blank' else 'iri')
        blank_prefix=f'urn:lzo:source-blank:{s.i02_source.id}:{blob.version_id}:'
        assert origin['value'].startswith(blank_prefix) if reference_kind=='blank' else origin['value']=='urn:z'
        from lzo.semantic.rdf import Triple,RdfTerm
        from rdflib.namespace import RDF
        expected={*(Triple(object_iri(row.entity_id),str(RDF.type),RdfTerm('iri','urn:Pond')) for row in rows),
            Triple(object_iri(first.entity_id),'urn:area',RdfTerm('literal','2','http://www.w3.org/2001/XMLSchema#decimal')),
            Triple(object_iri(second.entity_id),'urn:area',RdfTerm('literal','1','http://www.w3.org/2001/XMLSchema#decimal')),
            Triple(object_iri(second.entity_id),'urn:next',RdfTerm('iri',object_iri(first.entity_id)))}
        for row in rows:
            for assertion in current_assertions(uow,row.entity_id):
                provenance=assertion['supports'][0]['payload']
                assert provenance['source_id']==str(s.i02_source.id) and provenance['snapshot']['version_id']==str(blob.version_id)
                semantic=provenance['raw_value']['semantic']
                if row==second:assert semantic['subject']['value']=='urn:a'
                elif reference_kind=='iri':assert semantic['subject']['value']=='urn:z'
                else:assert semantic['subject']['kind']=='bnode' and semantic['subject']['value'].startswith(blank_prefix)
                graph=semantic['graph']
                assert graph is None if fmt=='turtle' else graph['kind']=='iri' and graph['value']=='urn:graph:chain'
        from lzo.semantic.projection import ProjectionService,graph_iri
        from lzo.semantic.fuseki import FusekiRepository
        repo=FusekiRepository(s.config.fuseki_url)
        for row in rows:assert ProjectionService(uow,repo,actor=s.actor('alice')).project_revision(row.revision).state=='synced'
        uow.commit()
    assert set().union(*(repo.read_graph(graph_iri(row.entity_id,'accepted')) for row in rows))==expected
    from ingestion_export_assertions import assert_exported_triples
    assert_exported_triples(s,runner,[row.entity_id for row in rows],fmt,expected)
