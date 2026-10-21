"""Real service fixtures. Missing prerequisites fail instead of skipping acceptance."""
import os
import io
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]


def pytest_addoption(parser):
    # Root-level registration is required even when pytest starts above the
    # integration directory, before nested conftest files are discovered.
    parser.addoption("--check-persisted-rdf", action="store_true",
                     help="Read the previously seeded S01 persistence graph; never reseed it")


@dataclass(frozen=True)
class Probe:
    id: UUID
    pg_value: str
    iri: str


class ServiceEndpoints:
    def __init__(self, settings):
        self.settings = settings
        self.issuer = settings.oidc_issuer
        self.engine = create_engine(settings.database_url, connect_args={"connect_timeout": 3})
        if not settings.migration_database_url:
            raise RuntimeError("Persistence test requires a schema-owner database URL")
        self.owner_engine = create_engine(settings.migration_database_url, connect_args={"connect_timeout": 3})
        verify = ssl.create_default_context(cafile=str(settings.ca_bundle)) if settings.ca_bundle else True
        self.http = httpx.Client(verify=verify, timeout=10)

    def write_unique_pg_and_rdf_probe(self):
        probe = Probe(uuid4(), str(uuid4()), f"urn:lzo:bootstrap:{uuid4()}")
        with self.owner_engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS bootstrap_probe (id uuid PRIMARY KEY, value text NOT NULL)"))
            conn.execute(text("GRANT SELECT, INSERT ON bootstrap_probe TO lzo_app"))
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO bootstrap_probe VALUES (:id,:value)"), {"id": probe.id, "value": probe.pg_value})
        response = self.http.post(self.settings.fuseki_url + "/update", data={"update": f'INSERT DATA {{ <{probe.iri}> <urn:lzo:probe> "persistent" }}'})
        response.raise_for_status()
        return probe

    def read_pg_probe(self, probe_id):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT value FROM bootstrap_probe WHERE id=:id"), {"id": probe_id}).scalar_one()

    def ask_rdf_probe(self, iri):
        response = self.http.get(self.settings.fuseki_url + "/query", params={"query": f"ASK {{ <{iri}> <urn:lzo:probe> ?v }}"}, headers={"Accept": "application/sparql-results+json"})
        response.raise_for_status()
        return response.json()["boolean"]

    def oidc_discovery(self):
        response = self.http.get(self.settings.oidc_discovery_url)
        response.raise_for_status()
        return response.json()

    def restart_without_volume_deletion(self):
        project = os.environ.get("LZO_TEST_PROJECT", "")
        if not project.startswith("lzo-test-"):
            raise RuntimeError("Persistence test requires an isolated lzo-test-* project")
        self.engine.dispose()
        if os.environ.get("LZO_SERVICE_MODE") == "native":
            subprocess.run([sys.executable, str(ROOT / "scripts/native_dev.py"), "restart", "--project", project], check=True)
        else:
            # Docker socket is mounted ONLY in the dedicated acceptance runner.
            import docker
            client = docker.from_env()
            for service in ("postgres", "fuseki", "keycloak"):
                containers = client.containers.list(filters={"label": [f"com.docker.compose.project={project}", f"com.docker.compose.service={service}"]})
                if len(containers) != 1:
                    raise RuntimeError(f"Expected exactly one isolated {service} container")
                mounts = containers[0].attrs["Mounts"]
                if not any(m.get("Type") == "volume" and m.get("Name", "").startswith(project + "_") for m in mounts):
                    raise RuntimeError(f"Refusing restart without project-owned persistent volume: {service}")
                containers[0].restart(timeout=30)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                with self.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                self.ask_rdf_probe("urn:lzo:wait")
                assert self.oidc_discovery()["issuer"] == self.issuer
                return
            except (Exception, AssertionError):
                time.sleep(1)
        raise RuntimeError("Services did not recover after persistence restart")


@pytest.fixture(scope="session")
def settings():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".runtime/native.env", override=False)
    from lzo.config import Settings
    return Settings()


@pytest.fixture(scope="session")
def compose_endpoints(settings):
    endpoints = ServiceEndpoints(settings)
    yield endpoints
    endpoints.engine.dispose()
    endpoints.owner_engine.dispose()
    endpoints.http.close()


@pytest.fixture
def db_session(settings):
    from sqlalchemy.orm import Session
    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


@pytest.fixture
def uow(settings):
    """One real app-role unit of work; the caller controls commit."""
    from lzo.db import PgUnitOfWork
    engine = create_engine(settings.database_url)
    try:
        with PgUnitOfWork(engine) as transaction:
            yield transaction
    finally:
        engine.dispose()


@pytest.fixture
def api_client(settings):
    from fastapi.testclient import TestClient
    from lzo.main import create_app
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def scope_scenario(settings):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import insert, select
    from lzo.auth import models
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.permissions import require_permission, authorized_resource_ids
    from lzo.auth.schemas import GrantWrite
    from lzo.auth.session import resolve_identity
    from lzo.contracts import ActorContext, ResourceScope
    from lzo.db import PgUnitOfWork
    from lzo.errors import Forbidden
    from lzo.governance.revisions import append_revision

    schema = "f03_" + uuid4().hex
    owner = create_engine(settings.migration_database_url)
    app = create_engine(settings.database_url, connect_args={"options": f"-csearch_path={schema}"})
    with owner.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION lzo_owner'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO lzo_app'))
    migration = Config(str(ROOT / "database/alembic.ini"))
    migration.attributes.update(schema=schema, connection_url=settings.migration_database_url)
    command.upgrade(migration, "head")

    class Scenario:
        def transaction(self):
            return PgUnitOfWork(app)

        def actor(self, name):
            with self.transaction() as uow:
                version = uow.session.execute(select(models.authorization_state.c.version)).scalar_one()
            return ActorContext(self.users[name], "f03-test", version)

        def admin_actor(self):
            return self.actor("admin")

        def resource(self, name):
            return ResourceScope(self.ids_by_name[name], self.scopes["farm_a"],
                                 (self.scopes["farm_b"],), "data")

        def grant(self, name, role, scope=None, resource=None):
            # Explicit fixture installation, not an ordinary administration API.
            # Runtime gate behavior is exercised separately in F06 tests.
            from lzo.auth.schemas import GrantDetail
            from lzo.auth.approval_policy import record_footprints
            from sqlalchemy import update
            with self.transaction() as uow:
                result = GrantDetail(id=uuid4(), revision=1, active=True,
                    user_id=self.users[name], role_id=self.roles[role],
                    scope_id=self.scopes[scope] if scope else None,
                    resource_id=self.ids_by_name[resource] if resource else None,
                    security_class="data")
                uow.session.execute(select(models.authorization_state).with_for_update()).one()
                uow.session.execute(insert(models.grants).values(**result.model_dump(exclude={"approval_id"})))
                append_revision(uow, self.admin_actor(), result.id, 0,
                                result.model_dump(mode="json"), "grant.fixture_installed")
                uow.session.execute(insert(models.resource_scopes).values(resource_id=result.id,
                    resource_kind="grant", security_class="identity"))
                record_footprints(uow, [self.users[name]], result.model_dump(mode="json"))
                uow.session.execute(update(models.authorization_state).values(
                    version=models.authorization_state.c.version + 1))
                uow.commit()
                return result

        def revoke(self, grant):
            with self.transaction() as uow:
                AuthorizationAdmin(uow).revoke_grant(self.admin_actor(), grant.id, grant.revision)
                uow.commit()

        def allowed(self, name, action, resource):
            with self.transaction() as uow:
                try:
                    require_permission(self.actor(name), action, self.resource(resource), uow=uow)
                    return True
                except Forbidden:
                    return False

        def ids(self, name, action):
            with self.transaction() as uow:
                return list(authorized_resource_ids(self.actor(name), action, uow=uow))

    scenario = Scenario()
    scenario.app = app
    scenario.users = {}
    scenario.scopes = {name: uuid4() for name in ("farm_a", "farm_b")}
    scenario.roles = {name: uuid4() for name in ("editor", "viewer", "administrator")}
    scenario.ids_by_name = {name: uuid4() for name in ("object_a", "object_b", "shared_device")}
    with scenario.transaction() as uow:
        for name in ("alice", "nobody", "admin"):
            scenario.users[name] = resolve_identity(uow, "https://test-issuer", name, name, name + "@example.invalid")
        for name, actions in (("editor", ["view", "edit"]), ("viewer", ["view"]),
                              ("administrator", ["identity.manage", "view"])):
            uow.session.execute(insert(models.roles).values(id=scenario.roles[name], name=name, actions=actions, revision=1))
            uow.session.execute(insert(models.resource_scopes).values(resource_id=scenario.roles[name],
                resource_kind="role", security_class="identity"))
            append_revision(uow, ActorContext(scenario.users["admin"], "fixture", 1),
                scenario.roles[name], 0, {"name": name, "actions": actions}, "role.fixture_installed")
        administrator_grant = uuid4()
        uow.session.execute(insert(models.grants).values(id=administrator_grant, user_id=scenario.users["admin"],
            role_id=scenario.roles["administrator"], security_class="identity", global_scope=True, revision=1))
        uow.session.execute(insert(models.resource_scopes).values(resource_id=administrator_grant,
            resource_kind="grant", security_class="identity"))
        append_revision(uow, ActorContext(scenario.users["admin"], "fixture", 1),
            administrator_grant, 0, {"synthetic": True}, "grant.fixture_installed")
        for name in scenario.ids_by_name:
            uow.session.execute(insert(models.resource_scopes).values(resource_id=scenario.ids_by_name[name],
                scope_id=scenario.scopes["farm_a" if name == "object_a" else "farm_b"] if name != "shared_device" else None,
                owner_scope_id=scenario.scopes["farm_a"], related_scope_ids=[str(scenario.scopes["farm_b"])],
                security_class="data", resource_kind="object"))
            append_revision(uow, ActorContext(scenario.users["admin"], "fixture", 1), scenario.ids_by_name[name],
                            0, {"value": "initial"}, "object.fixture_created")
        uow.commit()
    try:
        yield scenario
    finally:
        app.dispose()
        with owner.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        owner.dispose()


@pytest.fixture
def actor(scope_scenario):
    scope_scenario.grant("alice", "editor", scope="farm_a")
    return scope_scenario.actor("alice")


@pytest.fixture
def auditor(scope_scenario):
    scope_scenario.grant("admin", "viewer", scope="farm_a")
    scope_scenario.grant("admin", "viewer", scope="farm_b")
    return scope_scenario.actor("admin")


@pytest.fixture
def other_actor(scope_scenario):
    return scope_scenario.actor("nobody")


@pytest.fixture
def approvals(scope_scenario):
    """F06 approval service in an isolated real PostgreSQL transaction."""
    from lzo.governance.approvals import ApprovalService
    with scope_scenario.transaction() as transaction:
        yield ApprovalService(transaction)


@pytest.fixture
def artifacts(scope_scenario, tmp_path, settings):
    """A private volume and real PostgreSQL artifact metadata fixture."""
    from lzo.artifacts.store import ArtifactStore
    from lzo.artifacts.service import ArtifactService

    scenario = scope_scenario
    scenario.grant("alice", "editor", scope="farm_a")
    store = ArtifactStore(tmp_path / "private", max_upload_bytes=1024 * 1024)
    limits = settings.model_copy(update={"artifact_user_quota_bytes": 1024 * 1024,
                                         "artifact_scope_quota_bytes": 1024 * 1024})

    def upload(name, resource, payload, media_type="application/pdf", filename="evidence.pdf",
               previous_version_id=None):
        blob = store.put(io.BytesIO(payload), media_type)
        with scenario.transaction() as uow:
            result = ArtifactService(uow, store, scenario.limits).register_upload(
                scenario.actor(name), scenario.ids_by_name[resource], blob,
                filename=filename, metadata={"source": "synthetic acceptance"},
                previous_version_id=previous_version_id)
            uow.commit()
        return result

    scenario.store = store
    scenario.limits = limits
    scenario.upload = upload
    return scenario


@pytest.fixture
def jobs(scope_scenario, settings):
    """Real isolated PostgreSQL queue and a synthetic, transactional handler."""
    from sqlalchemy import select, text
    from lzo.worker import register_handler
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService, checkpoint, enqueue

    s = scope_scenario

    class Jobs:
        scenario = s
        kind = "test.effects." + uuid4().hex
        resource_id = s.ids_by_name["object_a"]
        grant = s.grant("alice", "editor", scope="farm_a")

        def actor(self):
            return s.actor("alice")

        def transaction(self):
            return s.transaction()

        @property
        def payload(self):
            return {"resource_id": str(self.resource_id)}

        def enqueue(self, uow, resource_id=None):
            return enqueue(uow, self.actor(), self.kind,
                           {"resource_id": str(resource_id or self.resource_id)}, None)

        def create(self, resource_id=None):
            with self.transaction() as uow:
                ref = self.enqueue(uow, resource_id)
                uow.commit()
                return ref

        def view(self, job_id):
            with self.transaction() as uow:
                return JobService(uow, self.settings).get(self.actor(), job_id)

        def runner(self, name="worker"):
            return Runner(s.app, self.settings, worker_id=name)

        def expire(self, job_id):
            with self.transaction() as uow:
                uow.session.execute(text("UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": job_id})
                uow.commit()

        def make_available(self, job_id):
            with self.transaction() as uow:
                uow.session.execute(text("UPDATE jobs SET available_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": job_id})
                uow.commit()

        def effect(self, ctx, offset):
            ctx.uow.session.execute(text("INSERT INTO job_test_effects(job_id, offset_value) VALUES (:id,:offset)"),
                                    {"id": ctx.job_id, "offset": offset})

        def effects(self):
            with self.transaction() as uow:
                return list(uow.session.execute(text("SELECT offset_value FROM job_test_effects ORDER BY offset_value")).scalars())

        def pause_resume(self, job_id):
            for state in ("paused", "queued"):
                with self.transaction() as uow:
                    service = JobService(uow, self.settings)
                    view = service.get(self.actor(), job_id)
                    service.set_state(self.actor(), job_id, state, view.revision)
                    uow.commit()

        def retry(self, job_id):
            with self.transaction() as uow:
                service = JobService(uow, self.settings)
                service.set_state(self.actor(), job_id, "queued", service.get(self.actor(), job_id).revision)
                uow.commit()

    fixture = Jobs()
    fixture.settings = settings.model_copy(update={"job_max_attempts": 3, "job_retry_base_seconds": 60,
                                                  "list_page_size": 1})
    # Test table belongs to this isolated schema and owner-only fixture setup.
    owner = create_engine(settings.migration_database_url)
    with s.app.connect() as conn:
        schema = conn.execute(text("SELECT current_schema()")).scalar_one()
    fixture.schema = schema
    with owner.begin() as conn:
        conn.execute(text(f'CREATE TABLE "{schema}".job_test_effects(job_id uuid, offset_value integer, PRIMARY KEY(job_id, offset_value))'))
        conn.execute(text(f'GRANT SELECT, INSERT ON "{schema}".job_test_effects TO lzo_app'))
    owner.dispose()

    def handler(ctx):
        while ctx.cursor.get("offset", 0) < 2:
            with ctx.batch():
                offset = ctx.cursor.get("offset", 0) + 1
                fixture.effect(ctx, offset)
                checkpoint(ctx.job_id, ctx.lease_token, {"offset": offset})

    register_handler(fixture.kind, handler)
    return fixture
