"""Owner-only synthetic F06 fixture/queue evidence; no HTTP bypass routes."""
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine, insert, select, text, update

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))
os.environ.update(dict(dotenv_values(ROOT / ".runtime/native.env")))

from lzo.auth import models
from lzo.auth.permissions import current_version
from lzo.config import Settings
from lzo.contracts import ActorContext
from lzo.db import PgUnitOfWork
from lzo.governance.approvals import approvals
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.worker.service import enqueue
from tests.e2e.support.approval_domain import JOB_KIND

settings = Settings()
engine = create_engine(settings.database_url)
state_path = ROOT / ".runtime/f06-acceptance.json"


def setup(submitter, reviewer):
    state = {"submitter": submitter, "reviewer": reviewer, "resource": str(uuid4()),
             "reviewer_grant": str(uuid4()), "scope": str(uuid4())}
    owner = create_engine(settings.migration_database_url)
    with owner.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS f06_synthetic_effects(job_id uuid PRIMARY KEY)"))
        conn.execute(text("GRANT SELECT, INSERT ON f06_synthetic_effects TO lzo_app"))
    owner.dispose()
    with PgUnitOfWork(engine) as uow:
        uow.session.execute(select(models.authorization_state).with_for_update()).one()
        actor = ActorContext(UUID(submitter), "f06-owner-fixture", current_version(uow))
        for person, actions, grant_id in (
                (submitter, ["view", "schema.edit", "schema.review", "schema.publish"], uuid4()),
                (reviewer, ["view", "schema.review"], UUID(state["reviewer_grant"]))):
            role_id = uuid4()
            uow.session.execute(insert(models.roles).values(id=role_id, name="Synthetic F06", actions=actions, revision=1))
            uow.session.execute(insert(models.grants).values(id=grant_id, user_id=UUID(person), role_id=role_id,
                scope_id=UUID(state["scope"]), security_class="schema", revision=1))
            for rid, payload, event in (
                    (role_id, {"actions": actions, "synthetic": True}, "role.fixture_installed"),
                    (grant_id, {"user_id": person, "synthetic": True}, "grant.fixture_installed")):
                uow.session.execute(insert(models.resource_scopes).values(
                    resource_id=rid, resource_kind="identity", security_class="identity"))
                append_revision(uow, actor, rid, 0, payload, event)
        uow.session.execute(insert(models.resource_scopes).values(resource_id=UUID(state["resource"]),
            resource_kind="synthetic_schema_fixture", scope_id=UUID(state["scope"]), security_class="schema"))
        append_revision(uow, actor, UUID(state["resource"]), 0, {"synthetic": True}, "synthetic.created")
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version + 1))
        uow.commit()
    state_path.write_text(json.dumps(state))
    return state


def enqueue_approval(approval_id, revoke=False):
    state = json.loads(state_path.read_text())
    if revoke:
        with PgUnitOfWork(engine) as uow:
            uow.session.execute(select(models.authorization_state).with_for_update()).one()
            actor = ActorContext(UUID(state["submitter"]), "f06-owner-revoke", current_version(uow))
            lock_ledger(uow)
            uow.session.execute(update(models.grants).where(models.grants.c.id == UUID(state["reviewer_grant"]))
                                .values(active=False, revision=2))
            append_revision(uow, actor, UUID(state["reviewer_grant"]), 1,
                            {"active": False, "synthetic": True}, "grant.fixture_revoked")
            uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version + 1))
            uow.commit()
    with PgUnitOfWork(engine) as uow:
        row = uow.session.execute(select(approvals).where(approvals.c.id == UUID(approval_id))).mappings().one()
        actor = ActorContext(UUID(state["submitter"]), "f06-enqueue", current_version(uow))
        uow.job_settings = settings.model_copy(update={"job_max_attempts": 1})
        ref = enqueue(uow, actor, JOB_KIND, {"resource_id": state["resource"], "approval_id": approval_id,
                                           "command": row["descriptor"]["command"]}, None)
        uow.commit()
    return {"job_id": str(ref.id)}


def evidence(job_id):
    from lzo.worker.models import jobs
    with PgUnitOfWork(engine) as uow:
        row = uow.session.execute(select(jobs.c.status, jobs.c.error_code).where(jobs.c.id == UUID(job_id))).mappings().one()
        effects = uow.session.execute(text(
            "SELECT count(*) FROM f06_synthetic_effects WHERE job_id=:id"), {"id": UUID(job_id)}).scalar_one()
        return {**row, "effects": effects}


if __name__ == "__main__":
    try:
        if sys.argv[1] == "setup":
            result = setup(sys.argv[2], sys.argv[3])
        elif sys.argv[1] in {"enqueue", "revoke-enqueue"}:
            result = enqueue_approval(sys.argv[2], revoke=sys.argv[1] == "revoke-enqueue")
        else:
            result = evidence(sys.argv[2])
        print(json.dumps(result))
    finally:
        engine.dispose()
