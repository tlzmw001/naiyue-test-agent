"""Disposable benchmarks verify the measured work without latency thresholds."""

import asyncio


def test_benchmark_keeps_actual_sample_and_call_counts(tmp_path):
    from scripts.benchmark_subagents import measure
    result = asyncio.run(measure(tmp_path, turns=3, warmup=1, registry_counts=[0, 5], repeats=3))
    for case in ('fresh', 'continue'):
        assert len(result[case]['samples_ms']) == 3
        assert result[case]['root_model_calls'] == 8
        assert result[case]['child_model_calls'] == 4
        assert result[case]['summary']['n'] == 3
    assert [row['synthetic_records'] for row in result['metadata']] == [0, 5]
    assert all(len(row['samples_ms']) == 3 and row['model_calls'] == 0 for row in result['metadata'])
    assert result['provider_requests'] == 0
