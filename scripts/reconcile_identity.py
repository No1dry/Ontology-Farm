"""Operator recovery of durable identity intents; never resends ambiguous mail."""
import argparse
from uuid import UUID
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--actor-id", type=UUID, required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    args = parser.parse_args()
    load_dotenv(args.env)
    from lzo.config import Settings
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.permissions import current_version
    from lzo.contracts import ActorContext
    from lzo.db import create_db_engine, PgUnitOfWork
    settings = Settings()
    engine = create_db_engine(settings)
    try:
        with PgUnitOfWork(engine) as uow:
            actor = ActorContext(args.actor_id, "operator-reconciliation", current_version(uow))
            result = IdentityOperations(uow, settings).attempt(actor, args.operation_id)
            print(f"operation={result.id} phase={result.phase} revision={result.revision}")
            if result.phase == "delivery_unknown":
                print("Delivery remains ambiguous. Inspect SMTP/provider records; no email was resent.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
