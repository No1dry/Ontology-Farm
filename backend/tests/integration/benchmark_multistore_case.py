"""Explicit-only benchmark bridge, excluded from the normal test file pattern."""
def test_measured_multistore_workload(schema_case):
    from benchmark_multistore import run_case
    run_case(schema_case)
