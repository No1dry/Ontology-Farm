"""The ledger's guarantees are checked against two real PostgreSQL sessions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


@pytest.fixture
def ledger_scenario(settings):
    from lzo.db import PgUnitOfWork
    from lzo.contracts import ActorContext
    from lzo.governance.revisions import append_revision

    schema = "f02_" + uuid4().hex
    owner = create_engine(settings.migration_database_url)
    app = create_engine(settings.database_url, connect_args={"options": f"-csearch_path={schema}"})
    with owner.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION lzo_owner'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO lzo_app'))
    migration = Config(str(__import__("pathlib").Path(__file__).resolve().parents[3] / "database/alembic.ini"))
    migration.attributes["schema"] = schema
    migration.attributes["connection_url"] = settings.migration_database_url
    command.upgrade(migration, "head")
    resource_id = uuid4()
    actor = ActorContext(subject_id=uuid4(), request_id="f02-test", authorization_version=1)

    class Scenario:
        def edit(self, expected_revision, value):
            with PgUnitOfWork(app) as transaction:
                result = append_revision(transaction, actor, resource_id, expected_revision,
                                         {"value": value}, "object.edited")
                transaction.commit()
                return result

        def edit_error(self, expected_revision):
            from lzo.errors import ApiError
            with pytest.raises(ApiError) as failure:
                self.edit(expected_revision, "stale")
            return failure.value.code

        def fail_between_revision_and_outbox(self, expected_revision):
            with owner.begin() as connection:
                connection.execute(text(f"""CREATE FUNCTION \"{schema}\".reject_outbox() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected outbox failure'; END $$"""))
                connection.execute(text(f"""CREATE TRIGGER reject_outbox BEFORE INSERT ON \"{schema}\".outbox_events
                    FOR EACH ROW EXECUTE FUNCTION \"{schema}\".reject_outbox()"""))
            try:
                with pytest.raises(DBAPIError):
                    with PgUnitOfWork(app) as transaction:
                        append_revision(transaction, actor, resource_id, expected_revision,
                                        {"value": "fail"}, "object.edited")
                        transaction.commit()
            finally:
                with owner.begin() as connection:
                    connection.execute(text(f'DROP TRIGGER reject_outbox ON "{schema}".outbox_events'))
                    connection.execute(text(f'DROP FUNCTION "{schema}".reject_outbox()'))

        def count(self, table):
            with app.connect() as connection:
                return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()

        def scalar(self, statement):
            with app.connect() as connection:
                return connection.execute(text(statement)).scalar_one()

    scenario = Scenario()
    scenario.app = app
    scenario.owner = owner
    scenario.schema = schema
    scenario.actor = actor
    scenario.resource_id = resource_id
    yield scenario
    app.dispose()
    with owner.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    owner.dispose()


def test_stale_edit_and_failed_outbox_do_not_half_commit(ledger_scenario):
    first = ledger_scenario.edit(expected_revision=0, value="initial")
    assert first.revision == 1
    assert ledger_scenario.edit_error(expected_revision=0) == "REVISION_CONFLICT"
    ledger_scenario.fail_between_revision_and_outbox(expected_revision=1)
    assert ledger_scenario.scalar("SELECT revision FROM resource_heads") == 1
    assert ledger_scenario.count("revisions") == 1
    assert ledger_scenario.count("audit_events") == 1
    assert ledger_scenario.count("outbox_events") == 1
    assert ledger_scenario.scalar("SELECT event_position FROM platform_watermarks") == 1


def test_caught_conflict_cannot_commit_a_watermark_gap(ledger_scenario):
    from lzo.db import PgUnitOfWork
    from lzo.governance.revisions import append_revision
    from lzo.errors import Conflict

    ledger_scenario.edit(0, "first")
    with PgUnitOfWork(ledger_scenario.app) as transaction:
        with pytest.raises(Conflict):
            append_revision(transaction, ledger_scenario.actor, ledger_scenario.resource_id,
                            0, {"value": "stale"}, "object.edited")
        transaction.commit()
    assert ledger_scenario.scalar("SELECT event_position FROM platform_watermarks") == 1
    assert ledger_scenario.count("outbox_events") == 1


def test_caught_command_failure_does_not_poison_idempotency_key(ledger_scenario):
    from lzo.db import PgUnitOfWork
    from lzo.governance.idempotency import execute_idempotent

    with PgUnitOfWork(ledger_scenario.app) as transaction:
        with pytest.raises(RuntimeError):
            execute_idempotent(transaction, ledger_scenario.actor, "retry", "body",
                               lambda: (_ for _ in ()).throw(RuntimeError("injected")))
        transaction.commit()
    with PgUnitOfWork(ledger_scenario.app) as transaction:
        result = execute_idempotent(transaction, ledger_scenario.actor, "retry", "body",
                                    lambda: {"accepted": True})
        transaction.commit()
    assert result == {"accepted": True}
    assert ledger_scenario.count("idempotency_results") == 1


def test_idempotency_replays_result_and_rejects_different_body(ledger_scenario):
    from lzo.db import PgUnitOfWork
    from lzo.governance.idempotency import execute_idempotent
    from lzo.errors import Conflict

    # Command and idempotency record must share one transaction.
    def in_transaction(digest, body):
        with PgUnitOfWork(ledger_scenario.app) as transaction:
            result = execute_idempotent(transaction, ledger_scenario.actor, "key-1", digest,
                lambda: {"revision": __import__("lzo.governance.revisions", fromlist=["append_revision"])
                    .append_revision(transaction, ledger_scenario.actor, ledger_scenario.resource_id,
                                     0, {"value": body}, "object.edited").revision})
            transaction.commit()
            return result

    assert in_transaction("digest-a", "first") == {"revision": 1}
    assert in_transaction("digest-a", "ignored") == {"revision": 1}
    with pytest.raises(Conflict) as failure:
        in_transaction("digest-b", "different")
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"
    assert ledger_scenario.count("revisions") == 1
    assert ledger_scenario.count("idempotency_results") == 1


def test_runtime_role_cannot_change_history(ledger_scenario):
    ledger_scenario.edit(0, "first")
    for statement in ("UPDATE revisions SET event_type='forged'", "DELETE FROM revisions",
                      "UPDATE audit_events SET event_type='forged'", "DELETE FROM audit_events",
                      "UPDATE outbox_events SET event_type='forged'", "DELETE FROM outbox_events"):
        with pytest.raises(DBAPIError):
            with ledger_scenario.app.begin() as connection:
                connection.execute(text(statement))
    assert ledger_scenario.count("revisions") == 1


def test_commit_positions_wait_for_earlier_transaction(ledger_scenario):
    from lzo.db import PgUnitOfWork
    from lzo.governance.revisions import append_revision

    entered = Event()
    release = Event()
    second_done = Event()
    second_resource = uuid4()

    def first():
        with PgUnitOfWork(ledger_scenario.app) as transaction:
            append_revision(transaction, ledger_scenario.actor, ledger_scenario.resource_id,
                            0, {"value": "first"}, "object.edited")
            entered.set()
            assert release.wait(10)
            transaction.rollback()

    def second():
        assert entered.wait(10)
        with PgUnitOfWork(ledger_scenario.app) as transaction:
            append_revision(transaction, ledger_scenario.actor, second_resource,
                            0, {"value": "second"}, "object.edited")
            transaction.commit()
        second_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        b = pool.submit(second)
        assert entered.wait(10)
        assert not second_done.wait(0.2)
        release.set()
        a.result(timeout=10)
        b.result(timeout=10)
    assert ledger_scenario.scalar("SELECT event_position FROM platform_watermarks") == 1
    assert ledger_scenario.scalar("SELECT position FROM outbox_events") == 1


def test_two_writers_same_head_only_one_succeeds(ledger_scenario):
    from lzo.errors import Conflict

    def write(value):
        try:
            return ledger_scenario.edit(0, value)
        except Conflict as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("one", "two")))
    assert sorted(isinstance(item, str) for item in results) == [False, True]
    assert "REVISION_CONFLICT" in results
    assert ledger_scenario.count("revisions") == 1
    assert ledger_scenario.count("outbox_events") == 1


def test_watermarks_advance_only_over_committed_contiguous_positions(ledger_scenario):
    from lzo.db import PgUnitOfWork
    from lzo.governance.revisions import advance_watermark
    from lzo.errors import Conflict

    ledger_scenario.edit(0, "first")
    ledger_scenario.edit(1, "second")
    with PgUnitOfWork(ledger_scenario.app) as transaction:
        with pytest.raises(Conflict) as failure:
            advance_watermark(transaction, "projection", 2)
        assert failure.value.code == "WATERMARK_GAP"
    with PgUnitOfWork(ledger_scenario.app) as transaction:
        assert advance_watermark(transaction, "projection", 1) == 1
        transaction.commit()
    with PgUnitOfWork(ledger_scenario.app) as transaction:
        assert advance_watermark(transaction, "projection", 2) == 2
        transaction.commit()
    assert ledger_scenario.scalar("SELECT projection_watermark FROM platform_watermarks") == 2
    with PgUnitOfWork(ledger_scenario.app) as transaction:
        with pytest.raises(Conflict):
            advance_watermark(transaction, "inference", 3)


def test_concurrent_same_idempotency_key_runs_command_once(ledger_scenario):
    from lzo.db import PgUnitOfWork
    from lzo.governance.idempotency import execute_idempotent
    from lzo.governance.revisions import append_revision

    entered = Event()
    release = Event()
    calls = []

    def run(block):
        with PgUnitOfWork(ledger_scenario.app) as transaction:
            def command():
                calls.append(1)
                result = append_revision(transaction, ledger_scenario.actor,
                    ledger_scenario.resource_id, 0, {"value": "once"}, "object.edited")
                if block:
                    entered.set()
                    assert release.wait(10)
                return {"revision": result.revision}
            result = execute_idempotent(transaction, ledger_scenario.actor, "same", "digest", command)
            transaction.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, True)
        assert entered.wait(10)
        second = pool.submit(run, False)
        release.set()
        assert first.result(timeout=10) == {"revision": 1}
        assert second.result(timeout=10) == {"revision": 1}
    assert len(calls) == 1
    assert ledger_scenario.count("revisions") == 1


def test_api_error_contains_request_id_without_sensitive_details(api_client):
    from fastapi import Request
    from lzo.errors import Conflict

    @api_client.app.get("/test-ledger-error")
    def error(_request: Request):
        raise Conflict("REVISION_CONFLICT", "Revision changed", {"current_revision": 2})

    response = api_client.get("/test-ledger-error", headers={"X-Request-ID": "ledger-test-123"})
    assert response.status_code == 409
    assert response.json() == {"code": "REVISION_CONFLICT", "message": "Revision changed",
                               "request_id": "ledger-test-123", "details": {"current_revision": 2}}


def test_http_preconditions_use_shared_error_statuses():
    from lzo.errors import ApiError, parse_if_match, require_idempotency_key

    assert parse_if_match('"17"') == 17
    assert require_idempotency_key("request-1") == "request-1"
    for value, status in ((None, 428), ("17", 422), ('"-1"', 422), ('"abc"', 422)):
        with pytest.raises(ApiError) as failure:
            parse_if_match(value)
        assert failure.value.status == status
    with pytest.raises(ApiError) as failure:
        require_idempotency_key(None)
    assert failure.value.status == 428


def test_http_error_redacts_sensitive_detail_fields(api_client):
    from lzo.errors import ApiError

    @api_client.app.get("/test-ledger-redaction")
    def error():
        raise ApiError("VALIDATION_FAILED", 422, "Invalid input",
                       {"credential": "private", "nested": {"access_token": "private"}, "count": 2})

    response = api_client.get("/test-ledger-redaction")
    assert response.status_code == 422
    assert response.json()["details"] == {"credential": "[redacted]",
                                           "nested": {"access_token": "[redacted]"}, "count": 2}


def test_framework_404_uses_shared_error_body(api_client):
    response = api_client.get("/missing-f02-route", headers={"X-Request-ID": "missing-42"})
    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Resource not found",
                               "request_id": "missing-42", "details": None}


def test_framework_validation_never_returns_raw_input_or_context(api_client):
    from fastapi import Query

    @api_client.app.get("/test-ledger-validation")
    def endpoint(count: int = Query(...)):
        return {"count": count}

    synthetic_secret = "postgresql://user:SYNTHETIC_PASSWORD@host/db"
    response = api_client.get("/test-ledger-validation", params={"count": synthetic_secret},
                              headers={"X-Request-ID": "validation-42"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == "validation-42"
    assert body["details"] == {"errors": [{"field": "count", "type": "int_parsing"}]}
    assert synthetic_secret not in response.text


def test_generic_detail_dsn_is_redacted(api_client):
    from lzo.errors import ApiError

    synthetic_secret = "postgresql://user:SYNTHETIC_PASSWORD@host/db"

    @api_client.app.get("/test-ledger-dsn")
    def endpoint():
        raise ApiError("VALIDATION_ERROR", 422, "Invalid value",
                       {"database_url": synthetic_secret, "input": synthetic_secret,
                        "nested": {"migration_database_url": synthetic_secret}})

    response = api_client.get("/test-ledger-dsn")
    assert response.status_code == 422
    assert response.json()["details"] == {"database_url": "[redacted]", "input": "[redacted]",
                                           "nested": {"migration_database_url": "[redacted]"}}
    assert synthetic_secret not in response.text


def test_settings_representation_hides_synthetic_credentials(tmp_path):
    from lzo.config import Settings

    secrets = ("app:SYNTHETIC_APP", "owner:SYNTHETIC_OWNER", "SYNTHETIC_OIDC",
               "SYNTHETIC_FUSEKI", "SYNTHETIC_DISCOVERY")
    settings = Settings(database_url=f"postgresql+psycopg://{secrets[0]}@host/lzo",
                        migration_database_url=f"postgresql+psycopg://{secrets[1]}@host/lzo",
                        fuseki_url=f"http://user:{secrets[3]}@fuseki/lzo",
                        oidc_issuer="https://issuer/realms/lzo",
                        oidc_discovery_url=f"https://issuer/.well-known/openid-configuration?key={secrets[4]}",
                        oidc_client_secret=secrets[2], file_root=tmp_path)
    for value in secrets:
        assert value not in repr(settings)
        assert value not in str(settings)
    assert settings.database_url.endswith("@host/lzo")


def test_repeat_upgrade_preserves_populated_ledger(ledger_scenario, settings):
    ledger_scenario.edit(0, "seeded")
    migration = Config(str(__import__("pathlib").Path(__file__).resolve().parents[3] / "database/alembic.ini"))
    migration.attributes["schema"] = ledger_scenario.schema
    migration.attributes["connection_url"] = settings.migration_database_url
    command.upgrade(migration, "head")
    assert ledger_scenario.scalar("SELECT revision FROM resource_heads") == 1
    assert ledger_scenario.count("revisions") == 1
    assert ledger_scenario.count("outbox_events") == 1
    assert ledger_scenario.scalar("SELECT event_position FROM platform_watermarks") == 1


def test_native_runtime_environment_excludes_owner_credentials():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from native_dev import runtime_environment

    supplied = {"LZO_DATABASE_URL": "app-url", "LZO_MIGRATION_DATABASE_URL": "owner-url",
                "LZO_POSTGRES_ADMIN_URL": "admin-url", "LZO_OWNER_PASSWORD": "owner-password",
                "POSTGRES_PASSWORD": "admin-password", "LZO_APP_PASSWORD": "app-password",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": "bootstrap-password", "PATH": "system-path"}
    child = runtime_environment(supplied)
    assert child["LZO_DATABASE_URL"] == "app-url"
    assert child["PATH"] == "system-path"
    assert not ({"LZO_MIGRATION_DATABASE_URL", "LZO_POSTGRES_ADMIN_URL", "LZO_OWNER_PASSWORD",
                 "POSTGRES_PASSWORD", "LZO_APP_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD"} & child.keys())
