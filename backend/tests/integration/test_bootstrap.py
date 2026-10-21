def test_real_services_persist_across_restart(compose_endpoints):
    """Deleting a data volume or replacing real stores breaks this contract."""
    probe = compose_endpoints.write_unique_pg_and_rdf_probe()
    compose_endpoints.restart_without_volume_deletion()
    assert compose_endpoints.read_pg_probe(probe.id) == probe.pg_value
    assert compose_endpoints.ask_rdf_probe(probe.iri) is True
    assert compose_endpoints.oidc_discovery()["issuer"] == compose_endpoints.issuer


def test_postgis_is_real_and_available(db_session):
    from sqlalchemy import text
    assert "POSTGIS=" in db_session.execute(text("SELECT postgis_full_version()")).scalar_one()


def test_health_reports_real_dependencies(api_client):
    assert api_client.get("/health/live").json() == {"status": "live"}
    ready = api_client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"] == {"postgres": "ready", "fuseki": "ready", "oidc": "ready", "files": "ready"}


def test_readiness_refuses_unreachable_database(api_client):
    from lzo.config import Settings
    from lzo.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(Settings(database_url="postgresql+psycopg://invalid:invalid@127.0.0.1:1/absent"))) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["dependencies"]["postgres"] == "unavailable"
