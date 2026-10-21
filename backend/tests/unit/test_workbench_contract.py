from lzo.ingestion.schemas import ConnectionCheck, ScheduleView


def test_connection_check_and_schedule_identity_are_readonly_generated_contracts():
    check = ConnectionCheck.model_json_schema()['properties']
    assert all(check[name].get('readOnly') is True for name in ('source_id', 'reachable', 'read_only', 'incremental_mode', 'issues'))
    assert ScheduleView.model_json_schema()['properties']['source_id'].get('readOnly') is True
