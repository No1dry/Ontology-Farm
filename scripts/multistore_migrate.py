"""Privileged local migration controller; credentials are read, never printed."""
import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='preview', choices=('preview', 'backfill', 'reconcile', 'cutover', 'rollback', 'recover-activation'))
    parser.add_argument('--source-env', required=True, type=Path)
    parser.add_argument('--target-env', required=True, type=Path)
    parser.add_argument('--actor', required=True, type=UUID, help='Current governed initiating platform identity')
    parser.add_argument('--plan', type=UUID)
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--backup', type=Path, help='Verified coordinated backup receipt from the backup controller')
    parser.add_argument('--approval-command', action='store_true', help='Emit exact storage.activate command after reconciliation')
    parser.add_argument('--for-rollback', action='store_true')
    parser.add_argument('--approval', type=UUID)
    parser.add_argument('--writers', type=Path, help='Startup ownership records of the stopped API and worker')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--activation', type=Path, help='NEW private runtime JSON; contains credentials, never commit')
    args = parser.parse_args()
    if args.action != 'preview' and args.plan is None:
        parser.error('--plan is required')
    if args.action in ('cutover', 'rollback') and any(getattr(args, key) is None for key in ('approval', 'writers', 'workspace', 'activation')):
        parser.error('activation requires --approval, --writers, --workspace and --activation')
    if args.action == 'recover-activation' and any(getattr(args, key) is None for key in ('writers', 'workspace', 'activation')):
        parser.error('recovery requires --writers, --workspace and --activation')
    from dotenv import dotenv_values
    from sqlalchemy import create_engine, text
    from lzo.config import Settings
    from lzo.contracts import ActorContext
    from lzo.storage.migration import MigrationService, MigrationError
    def settings(path):
        values = dotenv_values(path)
        return Settings(**{key[4:].lower(): value for key, value in values.items() if key.startswith('LZO_')})
    engines = []
    try:
        source, target = settings(args.source_env), settings(args.target_env)
        if not source.migration_database_url or not target.gis_migration_database_url:
            raise MigrationError('EXPLICIT_OWNER_CONNECTIONS_REQUIRED')
        core = create_engine(source.migration_database_url)
        gis = create_engine(target.gis_migration_database_url)
        engines.extend((core, gis))
        with core.connect() as c:
            actor = ActorContext(args.actor, 'multistore-migrate', c.execute(text('SELECT version FROM authorization_state')).scalar_one())
        service = MigrationService(core, gis, source, target)
        if args.action == 'preview':
            result = {'plan_id': str(service.preview(actor)), 'state': 'backfill', 'source_preserved': True}
        elif args.action == 'backfill':
            result = service.backfill(args.plan, actor, args.limit)
        elif args.action == 'reconcile':
            result = service.reconcile(args.plan, actor, args.backup)
            if args.approval_command:
                result['approval'] = {'kind': 'storage.activate', 'target_ids': [str(args.plan)],
                    'command': service.command(args.plan, actor, 'rollback' if args.for_rollback else 'cutover')}
        else:
            from lzo.storage.maintenance import MaintenanceWindow
            additional = {domain: create_engine(getattr(target, domain + '_migration_database_url'))
                for domain in ('timeseries', 'catalog')}
            engines.extend(additional.values())
            with MaintenanceWindow(core, gis, args.writers, args.workspace, additional=additional) as window:
                if args.action == 'recover-activation':
                    result = service.recover_activation(args.plan, actor, window, args.activation)
                else:
                    result = getattr(service, args.action)(args.plan, actor, args.approval, window, args.activation)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as error:
        # Driver/Pydantic exceptions can contain connection strings or raw inputs.
        pending = isinstance(error, MigrationError) and str(error) == 'ACTIVATION_PUBLICATION_PENDING'
        print(json.dumps({'status': 'publication_pending' if pending else 'refused',
            'transition_committed': pending, 'code': str(error) if isinstance(error, MigrationError) else type(error).__name__}), file=sys.stderr)
        return 1
    finally:
        for engine in engines:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
