"""Opt-in synthetic examples, original authored bytes, and actual source provenance."""
import io
from uuid import UUID, uuid4
from lzo.artifacts.service import ArtifactService
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.revisions import append_revision
from lzo.governance.risk import digest_change
from lzo.ingestion.sources import add_scoped
from lzo.storage.blobs import get_blob_store
from lzo.catalog.schemas import CreateModel, ModelMetadata, CreateDocument, DocumentMetadata

SOURCE = 'https://www.w3.org/TR/2017/REC-vocab-ssn-20171019/'
EXAMPLE = b'''@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:lzo:synthetic:CameraExample> a rdfs:Class ;
    rdfs:label "SYNTHETIC camera example; not installed equipment" ;
    rdfs:subClassOf <http://www.w3.org/ns/sosa/Sensor> .
'''


def seed_examples(service, actor, scope):
    """Caller explicitly chooses scope and transaction; no startup mutation."""
    prefix = 'catalog.example.' + digest_change({'scope': str(scope.owner_scope_id),
        'security_class': scope.security_class, 'related': list(map(str, scope.related_scope_ids))})
    models = [service.create_model(actor, CreateModel(scope=scope, source_identity=prefix + '.' + name,
        metadata=ModelMetadata(exact_model='SYNTHETIC ' + name + ' example', synthetic=True,
            summary='Original authored example. No installed, connected or field-accepted equipment claim. Unknown manufacturer, firmware and protocols remain null.')),
        prefix + '.' + name) for name in ('camera', 'pump', 'drone')]
    store = get_blob_store(service.settings)
    artifacts = ArtifactService(service.uow, store, service.settings)
    def register():
        identifier = uuid4()
        add_scoped(service.uow, actor, identifier, scope, 'catalog.example.attachment', 'catalog.manage')
        append_revision(service.uow, actor, identifier, 0, {'synthetic': True, 'source': SOURCE}, 'catalog.example.created')
        artifact = artifacts.register_upload(actor, identifier, store.put(io.BytesIO(EXAMPLE), 'text/turtle'),
            filename='original-synthetic-camera.ttl', metadata={'synthetic': True, 'rights': 'CC0-1.0 original example', 'reference_source': SOURCE})
        return {'version_id': str(artifact.version_id)}
    registered = execute_idempotent(service.uow, actor, prefix + '.bytes', digest_change({'example': EXAMPLE.decode()}), register)
    version_id = UUID(registered['version_id'])
    artifacts.get(actor, version_id)
    document = service.create_document(actor, CreateDocument(scope=scope, source_identity=prefix + '.reference',
        metadata=DocumentMetadata(title='Original synthetic SOSA camera example', document_type='ontology',
            artifact_version_id=version_id, source_uri=SOURCE, source_version='2017-10-19; original-example-v1',
            license='CC0-1.0 for original example bytes. Referenced W3C publication retains its own rights; no manual copied.',
            model_ids=(models[0].id,), term_iris=('http://www.w3.org/ns/sosa/Sensor',),
            summary='Original authored subclass example with a dated W3C SSN/SOSA reference. Import requires the normal schema draft and independent release review.')),
        prefix + '.reference')
    return [*models, document]
