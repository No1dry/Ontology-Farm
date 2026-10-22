"""Local privileged platform backup/recovery; private material is never printed."""
import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'verify', 'restore'))
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--authority-key', type=Path, required=True, help='Private trusted key OUTSIDE the backup; retain separately for recovery')
    parser.add_argument('--env', type=Path, help='Complete current or staged target runtime/owner configuration')
    parser.add_argument('--actor', type=UUID)
    parser.add_argument('--migration-plan', type=UUID)
    parser.add_argument('--writers', type=Path)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--namespace', help='NEW lzo_restore_<8..24 lowercase letters/digits> namespace')
    parser.add_argument('--admin-env', type=Path, help='Private LZO_RESTORE_ADMIN_DATABASE_URL; never put a DSN in command arguments')
    args = parser.parse_args()
    required = {'create': ('env', 'actor', 'writers', 'workspace', 'destination'),
        'verify': ('manifest',), 'restore': ('manifest', 'namespace', 'destination', 'admin_env')}
    if any(getattr(args, key) is None for key in required[args.action]):
        parser.error('Missing arguments for ' + args.action + ': ' + ', '.join('--' + k.replace('_', '-') for k in required[args.action]))
    from dotenv import dotenv_values
    from sqlalchemy import create_engine, text
    from lzo.config import Settings
    from lzo.contracts import ActorContext
    from lzo.storage.backup import BackupService, verify_backup_material
    from lzo.storage.backup_format import BackupError
    from lzo.storage.migration import MigrationError
    engines = {}
    try:
        if args.action == 'verify':
            result = verify_backup_material(args.manifest, args.authority_key, args.pg_bin)
        elif args.action == 'restore':
            from lzo.storage.restore import RestoreService
            url = dotenv_values(args.admin_env).get('LZO_RESTORE_ADMIN_DATABASE_URL')
            if not url:
                raise BackupError('EXPLICIT_RESTORE_ADMIN_REQUIRED')
            admin = create_engine(url)
            engines['admin'] = admin
            result = RestoreService(admin, args.pg_bin, args.authority_key).restore(args.manifest, args.namespace, args.destination)
        else:
            values = dotenv_values(args.env)
            settings = Settings(**{key[4:].lower(): value for key, value in values.items() if key.startswith('LZO_')})
            for domain in ('core', 'gis', 'timeseries', 'catalog'):
                url = getattr(settings, ('' if domain == 'core' else domain + '_') + 'migration_database_url')
                if not url:
                    raise BackupError('EXPLICIT_OWNER_CONNECTIONS_REQUIRED')
                engines[domain] = create_engine(url)
            with engines['core'].connect() as c:
                actor = ActorContext(args.actor, 'multistore-backup', c.execute(text('SELECT version FROM authorization_state')).scalar_one())
            from lzo.storage.maintenance import MaintenanceWindow
            with MaintenanceWindow(engines['core'], engines['gis'], args.writers, args.workspace,
                    additional={d: engines[d] for d in ('timeseries', 'catalog')}) as window:
                manifest = BackupService(settings, args.pg_bin, args.authority_key).create(args.destination, window,
                    actor, migration_plan=args.migration_plan)
            result = {'status': 'verified', 'manifest': str(manifest)}
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'refused', 'code': str(error) if isinstance(error, (BackupError, MigrationError)) else type(error).__name__}), file=sys.stderr)
        return 1
    finally:
        for engine in engines.values():
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
