"""Old immutable impact snapshots remain readable after additive preview fields."""
import json
from pathlib import Path


def test_historical_impact_snapshot_keeps_unknown_classification():
    from lzo.ontology.schemas import ImpactReport
    historical = {"digest": "historical", "baseline": {}, "affected_ids": [], "violations": [],
                  "form_diff": {}, "permission_diff": {}, "migration_required": False}
    report = ImpactReport.model_validate(historical)
    assert report.risk is None
    assert report.reasons == ()
    assert report.active_release_id is None


def test_checked_in_impact_openapi_matches_server_model(settings):
    from lzo.main import create_app
    root = Path(__file__).resolve().parents[3]
    saved = json.loads((root / "contracts/openapi.json").read_text(encoding="utf-8"))
    app = create_app(settings)
    try:
        current = app.openapi()
    finally:
        app.state.engine.dispose()
    assert saved["components"]["schemas"]["ImpactReport"] == current["components"]["schemas"]["ImpactReport"]
