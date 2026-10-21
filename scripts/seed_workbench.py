"""Seed isolated auth or Studio test fixtures with verified provider subjects.

The operator creates the isolated database/realm and supplies exact subjects.
This script never creates provider passwords or resets a realm. Studio uses
audited domain services and an original auth-stage ownership manifest.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))


def main():
    from dotenv import dotenv_values
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--subjects', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('auth', 'studio'), default='auth')
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    values = dotenv_values(args.env)
    if values.get('APP_ENV') != 'test':
        raise SystemExit('Workbench seed requires APP_ENV=test')
    project = values.get('LZO_TEST_PROJECT', '')
    if not project.startswith('lzo-test-'):
        raise SystemExit('Workbench seed requires an isolated lzo-test-* project')
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing fixture manifest')
    from sqlalchemy.engine import make_url
    database = make_url(values['LZO_DATABASE_URL']).database
    if database != values.get('LZO_TEST_DATABASE'):
        raise SystemExit('Explicit test database identity does not match')
    for key, value in values.items():
        if key.startswith('LZO_') and value is not None:
            os.environ[key] = value
    from lzo.config import Settings
    from lzo.db import create_db_engine, PgUnitOfWork
    from lzo.auth.session import resolve_identity
    from sqlalchemy import text
    from bootstrap_authorization import bootstrap
    settings = Settings()
    if args.stage == 'studio':
        if not args.manifest:
            raise SystemExit('Studio stage requires original auth manifest')
        original = json.loads(args.manifest.read_text(encoding='utf-8'))
        if original.get('test_project') != project or original.get('database') != database or original.get('issuer') != settings.oidc_issuer:
            raise SystemExit('Studio stage auth manifest identity mismatch')
        from seed_studio import seed
        seed(settings, original, args.output)
        return
    subjects = json.loads(args.subjects.read_text(encoding='utf-8'))
    if set(subjects) != {'editor-a', 'editor-b'} or not all(isinstance(value, str) and value for value in subjects.values()):
        raise SystemExit('Exact editor-a/editor-b provider subjects are required')
    manifest = {'stage': 'auth', 'test_project': project, 'database': database,
        'issuer': settings.oidc_issuer, 'subjects': subjects, 'users': {}}
    engine = create_db_engine(settings)
    try:
        with PgUnitOfWork(engine) as uow:
            if uow.session.execute(text('select current_database()')).scalar_one() != database:
                raise SystemExit('Live database identity does not match')
            for alias, subject in subjects.items():
                resolve_identity(uow, settings.oidc_issuer, subject, '合成编辑员 ' + alias[-1].upper(), None)
            uow.commit()
        for alias, subject in subjects.items():
            manifest['users'][alias] = str(bootstrap(settings, settings.oidc_issuer, subject))
    finally:
        engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
    print('Auth-stage fixture manifest written: ' + str(args.output))


if __name__ == '__main__':
    main()
