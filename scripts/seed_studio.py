"""APP_ENV=test stage extension: real audited domain services, no test API."""
import json
from pathlib import Path
from uuid import UUID


def seed(settings, auth_manifest, output):
    from sqlalchemy import text
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.permissions import current_version, permission_context
    from lzo.auth.schemas import RoleWrite, GrantWrite
    from lzo.auth import approval_policy  # Register the real identity descriptors.
    from lzo.contracts import ActorContext
    from lzo.db import PgUnitOfWork, create_db_engine
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.drafts import create_draft, update_draft, import_semantic_draft, store_for
    from lzo.ontology.schemas import CreateDraftRequest, SchemaDocument, SemanticDraftImport
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.releases import descriptor, submit_draft, publish_release
    from lzo.main import create_app
    from lzo.worker.runner import Runner
    import io
    manifest = dict(auth_manifest)
    if manifest.get('stage') != 'auth' or Path(output).exists():
        raise SystemExit('Studio seed requires original auth manifest and fresh output')
    app = create_app(settings)
    engine = app.state.engine
    users = {k: UUID(v) for k, v in manifest['users'].items()}
    def actor(uow, alias='editor-a'):
        return ActorContext(users[alias], 'workbench-studio-seed', current_version(uow))
    try:
        with PgUnitOfWork(engine) as uow, permission_context(uow):
            if uow.session.execute(text('select current_database()')).scalar_one() != manifest['database']:
                raise SystemExit('Live fixture database mismatch')
            uow.ontology_settings = settings
            admin = AuthorizationAdmin(uow)
            role = admin.create_role(actor(uow), RoleWrite(name='Synthetic Studio operators', actions=['view', 'edit', 'schema.edit', 'schema.review', 'schema.publish', 'artifact.upload', 'job.manage']))
            grants = []
            for alias in ('editor-a', 'editor-b'):
                body = GrantWrite(user_id=users[alias], role_id=role.id, global_scope=True, security_class='schema')
                approvals = ApprovalService(uow)
                change = approvals.preview(actor(uow), 'identity.grant.create', [role.id], body.model_dump(mode='json', exclude={'approval_id'}))
                submitted = approvals.submit(actor(uow), change.kind, change.target_ids, change.change_digest, change.baseline, {'command': change.command})
                approvals.decide(actor(uow, 'editor-b'), submitted.id, 'approve', 'Owned synthetic studio access')
                granted = admin.create_grant(actor(uow), body.model_copy(update={'approval_id': submitted.id}))
                grants.append(str(granted.id))
            owner = create_draft(uow, actor(uow), CreateDraftRequest(document=SchemaDocument()))
            store = store_for(uow)
            schema = b'@prefix owl:<http://www.w3.org/2002/07/owl#>. @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>. <urn:Sensor> a owl:Class; rdfs:label "Sensor". <urn:note> a owl:DatatypeProperty; rdfs:label "Note"; rdfs:range <http://www.w3.org/2001/XMLSchema#string>.'
            blob = store.put(io.BytesIO(schema), 'text/turtle')
            ArtifactService(uow, store, settings).register_upload(actor(uow), owner.id, blob, filename='synthetic-sensor.ttl', metadata={'synthetic': True})
            draft = import_semantic_draft(uow, actor(uow), SemanticDraftImport(blob=blob, format='turtle', kind='schema', expected_revision=0))
            shape = b'@prefix sh:<http://www.w3.org/ns/shacl#>. <urn:SensorShape> a sh:NodeShape; sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:note>; sh:maxCount 1; sh:pattern "^.*$"].'
            shape_blob = store.put(io.BytesIO(shape), 'text/turtle')
            ArtifactService(uow, store, settings).register_upload(actor(uow), owner.id, shape_blob, filename='synthetic-sensor-shapes.ttl', metadata={'synthetic': True})
            draft = import_semantic_draft(uow, actor(uow), SemanticDraftImport(blob=shape_blob, format='turtle', kind='shapes', expected_revision=draft.revision, target_draft_id=draft.id))
            document = draft.document.model_copy(update={'ui_metadata': {'fields': {'urn:note': {'widget': 'text', 'custom_retained': ['source']}}, 'synthetic': True}})
            draft = update_draft(uow, actor(uow), draft.id, document, draft.revision)
            change = descriptor(uow, actor(uow), draft.id)
            approval = submit_draft(uow, actor(uow), draft.id, change.change_digest, draft.revision)
            ApprovalService(uow).decide(actor(uow, 'editor-b'), approval.id, 'approve', 'Reviewed seed source and baseline')
            job = publish_release(uow, actor(uow), draft.id, approval.id, draft.revision)
            uow.commit()
            manifest.update(stage='studio', draft_id=str(draft.id), source_owner_id=str(owner.id), source_blobs=[str(blob.version_id), str(shape_blob.version_id)], grants=grants, publication_job_id=str(job.id))
        # Persist committed fixture identity before the bounded worker step.
        Path(output).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        try:
            runner = Runner(engine, settings)
            for _ in range(20):
                if not runner.run_once(): break
            from lzo.worker.service import JobService
            with PgUnitOfWork(engine) as uow, permission_context(uow):
                result = JobService(uow, settings).get(actor(uow), job.id)
                if result.job.status != 'succeeded' or result.result_ref is None:
                    raise SystemExit('Studio seed publication did not complete')
                manifest['release_id'] = str(result.result_ref.resource_id)
                manifest['release_api_path'] = result.result_ref.api_path
                Path(output).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        finally:
            app.state.engine.dispose()
    finally:
        engine.dispose()
    seed_data(settings, output)
    print('Studio stage seeded through audited services; exact publication result retained.')


def seed_data(settings, manifest_path):
    """Add one manifest-bound real Sensor for inherited-required migration checks."""
    import uuid
    from sqlalchemy import text
    from lzo.main import create_app
    from lzo.auth.permissions import current_version, permission_context
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import RoleWrite, GrantWrite
    from lzo.contracts import ActorContext
    from lzo.db import PgUnitOfWork
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.releases import current_execution_bundle
    from lzo.semantic.facts import register_object
    from lzo.semantic.schemas import CreateObjectInput
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if manifest.get('stage') != 'studio' or not manifest.get('release_id') or manifest.get('object_id'):
        raise SystemExit('Expected completed studio stage without data extension')
    app = create_app(settings)
    def actor(uow, alias='editor-a'):
        return ActorContext(UUID(manifest['users'][alias]), 'workbench-studio-data-seed', current_version(uow))
    try:
        with PgUnitOfWork(app.state.engine) as uow, permission_context(uow):
            if uow.session.execute(text('select current_database()')).scalar_one() != manifest['database']:
                raise SystemExit('Studio data database mismatch')
            uow.ontology_settings = settings
            admin = AuthorizationAdmin(uow)
            role = admin.create_role(actor(uow), RoleWrite(name='Synthetic studio data access', actions=['view', 'edit']))
            grants = []
            for alias in ('editor-a', 'editor-b'):
                body = GrantWrite(user_id=UUID(manifest['users'][alias]), role_id=role.id, global_scope=True, security_class='data')
                service = ApprovalService(uow)
                change = service.preview(actor(uow), 'identity.grant.create', [role.id], body.model_dump(mode='json', exclude={'approval_id'}))
                ref = service.submit(actor(uow), change.kind, change.target_ids, change.change_digest, change.baseline, {'command': change.command})
                service.decide(actor(uow, 'editor-b'), ref.id, 'approve', 'Synthetic current-data migration fixture')
                grant = admin.create_grant(actor(uow), body.model_copy(update={'approval_id': ref.id})); grants.append(str(grant.id))
            identifier, scope = uuid.uuid4(), uuid.uuid4()
            bundle = current_execution_bundle(uow, actor(uow))
            register_object(uow, actor(uow), CreateObjectInput(resource_id=identifier, class_iris=('urn:Sensor',), display_name='Synthetic required-field migration Sensor', owner_scope_id=scope, state='active', data_nature='synthetic'), bundle)
            uow.commit()
            manifest.update(object_id=str(identifier), data_grants=grants, owner_scope_id=str(scope), bundle_id=str(bundle.id))
            Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    finally:
        app.state.engine.dispose()
    print('One real manifest-bound Sensor created through semantic domain service.')
