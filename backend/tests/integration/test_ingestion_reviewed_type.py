"""Reviewed source-created type roots retain exact decision currentness."""
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle
from test_ingestion_support_currentness import adopt


def test_reviewed_type_only_creation_invalidates_with_source_validity(configuration_case):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rulings import RulingService
    from lzo.semantic.facts import confirmed_types,current_classes
    from lzo.semantic.proofs import get_type_declaration_evidence
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        s.i02_mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={'fields':()}))
        uow.commit()
    bundle=ordinary_bundle(s);change=raw_change(s)
    identity,mapped,ruling=adopt(s,change,s.i02_mapping,bundle)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert confirmed_types(uow,s.actor('alice'),identity.resource_id)[1]=='active'
        assert current_classes(uow,s.actor('alice'),identity.resource_id)==('urn:Pond',)
    raw_change(s,temporal={'start':'2020-01-01T00:00:00Z','end':'2021-01-01T00:00:00Z','seen':None})
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert current_classes(uow,s.actor('alice'),identity.resource_id)==(),'Reviewed source type must lose eligibility'
        assert RulingService(uow).has_invalidated_supports(uow)
        evidence=get_type_declaration_evidence(uow,s.actor('alice'),identity.resource_id)
        assert evidence.class_iris==('urn:Pond',) and evidence.lifecycle_state=='inactive'
