"""Test-runner controls for manifest-bound auth fixtures, never an API endpoint."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend/src'))


def main():
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    from sqlalchemy import select, update, text
    values = dotenv_values(os.environ['LZO_WORKBENCH_ENV'])
    manifest = json.loads(Path(os.environ['LZO_WORKBENCH_MANIFEST']).read_text(encoding='utf-8'))
    if values.get('APP_ENV') != 'test' or manifest.get('stage') != 'auth':
        raise SystemExit('Auth fixture requires APP_ENV=test and auth-stage manifest')
    if not manifest['test_project'].startswith('lzo-test-') or values.get('LZO_TEST_PROJECT') != manifest['test_project']:
        raise SystemExit('Fixture project mismatch')
    if make_url(values['LZO_DATABASE_URL']).database != manifest['database'] or values.get('LZO_OIDC_ISSUER') != manifest['issuer']:
        raise SystemExit('Fixture database/issuer mismatch')
    for key, value in values.items():
        if key.startswith('LZO_') and value is not None: os.environ[key] = value
    from lzo.config import Settings
    from lzo.db import PgUnitOfWork, create_db_engine
    from lzo.auth.models import sessions, login_states, grants
    from lzo.auth.session import SessionService
    from lzo.auth.permissions import current_version
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import RoleWrite
    from lzo.contracts import ActorContext
    settings = Settings()
    command, alias = sys.argv[1:3]
    user_id = UUID(manifest['users'][alias])
    engine = create_db_engine(settings)
    try:
        with PgUnitOfWork(engine) as uow:
            if uow.session.execute(text('select current_database()')).scalar_one() != manifest['database']:
                raise SystemExit('Live fixture database mismatch')
            if command == 'expire':
                uow.session.execute(update(sessions).where(sessions.c.user_id == user_id).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
                uow.commit()
            elif command == 'nonce':
                service = SessionService(uow, settings)
                row = uow.session.execute(select(login_states).order_by(login_states.c.expires_at.desc())).mappings().first()
                if row is None: raise SystemExit('No owned pending login')
                data = service.decrypt(row['encrypted_data'])
                data['nonce'] = 'owned-test-invalid-nonce'
                uow.session.execute(update(login_states).where(login_states.c.id_hash == row['id_hash']).values(encrypted_data=service.encrypt(data)))
                uow.commit()
            elif command == 'version':
                actor = ActorContext(user_id, 'workbench-auth-fixture', current_version(uow))
                AuthorizationAdmin(uow).create_role(actor, RoleWrite(name='Fixture freshness ' + str(datetime.now(timezone.utc).timestamp()), actions=['view']))
                uow.commit()
            elif command == 'revoke':
                actor = ActorContext(UUID(manifest['users']['editor-a']), 'workbench-revocation-fixture', current_version(uow))
                rows = uow.session.execute(select(grants).where(grants.c.user_id == user_id, grants.c.active.is_(True))).mappings().all()
                for row in rows:
                    AuthorizationAdmin(uow).revoke_grant(actor, row['id'], row['revision'])
                    actor = ActorContext(actor.subject_id, actor.request_id, current_version(uow))
                uow.commit()
            elif command == 'restore':
                sys.path.insert(0, str(ROOT / 'scripts'))
                from bootstrap_authorization import bootstrap
                bootstrap(settings, manifest['issuer'], manifest['subjects'][alias])
            elif command == 'deadlines':
                print(json.dumps(sorted(value.isoformat() for value in uow.session.execute(select(sessions.c.expires_at).where(sessions.c.user_id == user_id)).scalars())))
            elif command == 'provider-logout':
                from lzo.auth.identity_admin import KeycloakAdmin
                KeycloakAdmin(settings)._request('POST', '/users/' + manifest['subjects'][alias] + '/logout')
            else:
                raise SystemExit('Unsupported fixture control')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
