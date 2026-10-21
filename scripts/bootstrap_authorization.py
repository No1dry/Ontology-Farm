"""Explicit operator bootstrap for one verified issuer/sub; no email-based admin."""
import argparse
import os
from uuid import uuid4

from sqlalchemy import insert, select
from dotenv import load_dotenv


def bootstrap(settings, issuer, subject):
    from lzo.auth import models
    from lzo.auth.permissions import current_version
    from lzo.auth.session import resolve_identity
    from lzo.contracts import ActorContext
    from lzo.db import create_db_engine, PgUnitOfWork
    from lzo.governance.revisions import append_revision
    engine = create_db_engine(settings)
    try:
        with PgUnitOfWork(engine) as uow:
            uow.session.execute(select(models.authorization_state).with_for_update()).one()
            user_id = resolve_identity(uow, issuer, subject, "Platform administrator", None)
            existing = uow.session.execute(select(models.grants.c.id).where(models.grants.c.user_id == user_id,
                models.grants.c.security_class == "identity", models.grants.c.global_scope.is_(True),
                models.grants.c.active.is_(True))).first()
            if existing:
                return user_id
            actor = ActorContext(user_id, "operator-bootstrap", current_version(uow))
            role_id, grant_id = uuid4(), uuid4()
            role = {"id": role_id, "name": "Identity administrator", "actions": ["identity.manage", "view"], "revision": 1}
            grant = {"id": grant_id, "user_id": user_id, "role_id": role_id, "security_class": "identity",
                     "global_scope": True, "active": True, "revision": 1}
            uow.session.execute(insert(models.roles).values(**role))
            uow.session.execute(insert(models.grants).values(**grant))
            for value, event in ((role, "role.created"), (grant, "grant.created")):
                payload = {key: str(item) if isinstance(item, __import__("uuid").UUID) else item for key, item in value.items()}
                append_revision(uow, actor, value["id"], 0, payload, event)
                uow.session.execute(insert(models.resource_scopes).values(resource_id=value["id"],
                    security_class="identity", resource_kind=event.split(".")[0]))
            from sqlalchemy import update
            uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version + 1))
            uow.commit()
            return user_id
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    load_dotenv(args.env)
    from lzo.config import Settings
    bootstrap(Settings(), args.issuer, args.subject)
    print("Explicit issuer/subject administrator grant recorded in the audit ledger.")


if __name__ == "__main__":
    main()
