"""HTTP success and a complete domain result are separate measurements."""
import importlib.util
from pathlib import Path


def test_benchmark_accounts_for_incomplete_200_and_errors():
    path = Path(__file__).resolve().parents[3] / 'scripts/benchmark_multistore.py'
    spec = importlib.util.spec_from_file_location('benchmark_multistore', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.completeness(200, {'observations': {'status': 'incomplete'}}) == 'incomplete'
    assert module.completeness(200, {'gis_status': 'pending'}) == 'incomplete'
    assert module.completeness(200, {'truncated': True}) == 'incomplete'
    assert module.completeness(422, {'code': 'SPARQL_TIMEOUT'}) == 'error'
    assert module.completeness(200, {'status': 'current', 'items': []}) == 'complete'
    result = module.summarize([{'seconds': .1, 'outcome': 'complete'}, {'seconds': .2, 'outcome': 'incomplete'},
        {'seconds': .3, 'outcome': 'error'}], elapsed=1)
    assert result['http_or_transport_errors'] == 1 and result['incomplete'] == 1 and result['complete'] == 1
    assert result['complete_throughput_per_second'] == 1 and result['attempt_throughput_per_second'] == 3
    assert result['latency_by_outcome']['complete']['p95_seconds'] == .1
    assert result['latency_by_outcome']['incomplete']['p50_seconds'] == .2
    assert result['latency_by_outcome']['error']['attempts'] == 1
