"""Generated resources cannot inherit or bypass the fixed source-footprint policy."""
from uuid import uuid4
import pytest
from sqlalchemy import insert


@pytest.fixture
def bound_export(artifacts):
    """Seed a policy-boundary footprint; semantic selection is tested separately."""
    from types import SimpleNamespace
    from lzo.auth.models import roles
    from lzo.auth.permissions import permission_context
    from lzo.semantic.exports import create_binding, ExportRequest
    s=artifacts
    s.grant('alice','editor',scope='farm_b')
    role=uuid4()
    with s.transaction() as uow:
        uow.session.execute(insert(roles).values(id=role,name='producer',actions=['export','candidate.read'],revision=1))
        uow.commit()
    s.roles['producer']=role
    for name in ('object_a','object_b'):
        s.grant('alice','producer',resource=name)
    roots={s.ids_by_name['object_a'],s.ids_by_name['object_b']}
    owner=uuid4()
    with s.transaction() as uow,permission_context(uow):
        create_binding(uow,s.actor('alice'),owner,ExportRequest(resource_ids=tuple(roots),format='turtle',layout='dataset'),
            SimpleNamespace(bundle_id=s.ids_by_name['object_a'],input_watermark=0),roots,roots,
            {s.ids_by_name['object_b']},'0'*64)
        uow.commit()
    return s,owner


def test_classified_export_without_binding_denies_even_direct_grant(scope_scenario):
    from lzo.auth.models import resource_scopes
    from lzo.auth.permissions import require_permission
    from lzo.contracts import ResourceScope
    from lzo.errors import ApiError
    s = scope_scenario
    identifier = uuid4()
    with s.transaction() as uow:
        uow.session.execute(insert(resource_scopes).values(resource_id=identifier,
            resource_kind='semantic.export',security_class='data'))
        uow.commit()
    s.ids_by_name['unbound_export'] = identifier
    s.grant('nobody','viewer',resource='unbound_export')
    with s.transaction() as uow, pytest.raises(ApiError):
        require_permission(s.actor('nobody'),'view',ResourceScope(identifier,None,(),'data'),uow=uow)


def test_binding_is_sealed_and_rejects_foreign_outputs_and_generated_ancestors(bound_export):
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError
    from lzo.semantic.models import export_bindings,export_dependencies,export_outputs
    from lzo.auth.models import resource_scopes
    s,owner=bound_export
    ordinary=s.upload('alice','object_a',b'ordinary evidence')
    statements=[update(export_bindings).where(export_bindings.c.id==owner).values(input_digest='1'*64),
        insert(export_dependencies).values(export_id=owner,resource_id=s.ids_by_name['shared_device'],export_root=False,candidate=False),
        insert(export_outputs).values(export_id=owner,version_id=ordinary.version_id,sha256=ordinary.sha256,state='completed')]
    for statement in statements:
        with s.transaction() as uow,pytest.raises(DBAPIError):
            uow.session.execute(statement)
            uow.commit()
    for kind in ('job','semantic.export'):
        ancestor,new=uuid4(),uuid4()
        with s.transaction() as uow:
            uow.session.execute(insert(resource_scopes).values(resource_id=ancestor,resource_kind=kind,security_class='data'))
            uow.commit()
        with s.transaction() as uow,pytest.raises(DBAPIError):
            uow.session.execute(insert(export_dependencies).values(export_id=new,resource_id=ancestor,export_root=True,candidate=False))
    with s.transaction() as uow,pytest.raises(DBAPIError):
        uow.session.execute(insert(export_dependencies).values(export_id=owner,resource_id=owner,export_root=True,candidate=False))


def test_actual_version_binding_and_public_mutations_fail_closed(bound_export):
    import io
    from datetime import datetime,timezone
    from sqlalchemy import select,update
    from lzo.artifacts.service import ArtifactService
    from lzo.artifacts.models import artifact_versions
    from lzo.auth.models import resource_scopes,grants,authorization_state
    from lzo.auth.permissions import permission_context,require_permission
    from lzo.contracts import ResourceScope
    from lzo.errors import ApiError
    s,owner=bound_export
    blob=s.store.put(io.BytesIO(b'<urn:a> <urn:p> <urn:b> .'),'text/turtle')
    with s.transaction() as uow,permission_context(uow):
        detail=ArtifactService(uow,s.store,s.limits).register_semantic_export(s.actor('alice'),owner,blob,
            filename='selected.ttl',metadata={'scope':'authorized_subset'})
        uow.commit()
    sibling=uuid4()
    with s.transaction() as uow:
        real=dict(uow.session.execute(select(artifact_versions).where(artifact_versions.c.id==detail.version_id)).mappings().one())
        uow.session.execute(insert(artifact_versions).values(**{**real,'id':sibling,'sha256':'f'*64}))
        uow.session.execute(insert(resource_scopes).values(resource_id=sibling,resource_kind='artifact',security_class='data'))
        uow.commit()
    for identifier in (owner,detail.version_id,sibling):
        s.ids_by_name[str(identifier)]=identifier
        s.grant('alice','editor',resource=str(identifier))
    with s.transaction() as uow,permission_context(uow):
        service=ArtifactService(uow,s.store,s.limits)
        assert service.get(s.actor('alice'),detail.version_id).version_id==detail.version_id
        with pytest.raises(ApiError):
            require_permission(s.actor('alice'),'view',ResourceScope(sibling,None,(),'data'),uow=uow)
        for command in (lambda:service.authorize_upload(s.actor('alice'),owner),
            lambda:service.link(s.actor('alice'),detail.version_id,s.ids_by_name['object_a'],'supports',1),
            lambda:service.review(s.actor('alice'),detail.version_id,'accepted','',1)):
            with pytest.raises(ApiError) as failure:
                command()
            assert failure.value.status==403
    # Completed reads do not require production export rights, but every fixed
    # candidate action remains conjunctive even when explicit generated grants exist.
    with s.transaction() as uow:
        uow.session.execute(update(grants).where(grants.c.user_id==s.users['alice'],grants.c.role_id==s.roles['producer'],
            grants.c.resource_id==s.ids_by_name['object_a']).values(active=False))
        uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1));uow.commit()
    with s.transaction() as uow:
        assert ArtifactService(uow,s.store,s.limits).get(s.actor('alice'),detail.version_id)
    with s.transaction() as uow:
        uow.session.execute(update(grants).where(grants.c.user_id==s.users['alice'],grants.c.role_id==s.roles['producer'],
            grants.c.resource_id==s.ids_by_name['object_b']).values(active=False))
        uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1));uow.commit()
    with s.transaction() as uow,pytest.raises(ApiError):
        ArtifactService(uow,s.store,s.limits).get(s.actor('alice'),detail.version_id)


def test_ordinary_artifact_metadata_cannot_classify_or_choose_read_actions(artifacts):
    import io
    from lzo.artifacts.service import ArtifactService
    s=artifacts
    blob=s.store.put(io.BytesIO(b'ordinary'),'text/plain')
    with s.transaction() as uow:
        detail=ArtifactService(uow,s.store,s.limits).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,
            filename='ordinary.txt',metadata={'generated':'semantic.export','required_action':'candidate.read','export_id':str(uuid4())})
        uow.commit()
    with s.transaction() as uow:
        assert ArtifactService(uow,s.store,s.limits).get(s.actor('alice'),detail.version_id)
