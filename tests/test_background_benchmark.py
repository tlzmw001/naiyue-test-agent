"""Benchmark correctness gates do not encode machine-specific latency limits."""

import asyncio
from types import SimpleNamespace


def test_benchmark_case_measures_real_turns_and_reaps_load(tmp_path):
    from scripts.benchmark_background_tasks import sample
    options = SimpleNamespace(turns=3, warmup=1, output_bytes_per_second=65536, poll_seconds=.01,
                              turn_gap_seconds=0, archive_bytes=65536 * 64)
    result = asyncio.run(sample(tmp_path, 'output_open', options))
    assert len(result['turn_ms']) == 3 and result['model_calls'] == 4
    assert result['metadata_ms'] and result['output_ms']
    assert result['cleanup_confirmed'] and result['provider_requests'] == 0


def test_benchmark_distribution_uses_nearest_rank():
    from scripts.benchmark_background_tasks import distribution
    assert distribution(list(range(1, 21))) == dict(n=20, p50_ms=10.5, p95_ms=19, max_ms=20)


def test_benchmark_active_close_reaps_and_records_the_process(tmp_path):
    from scripts.benchmark_background_tasks import active_close
    result = asyncio.run(active_close(tmp_path))
    assert result['cleanup_confirmed'] and result['termination'] == 'app_close'
    assert result['close_ms'] >= 0


def test_benchmark_normalizes_workspace_before_registering_load(tmp_path):
    from scripts.benchmark_background_tasks import sample
    (tmp_path / 'parent').mkdir()
    root = tmp_path / 'parent' / '..' / 'workspace'
    options = SimpleNamespace(turns=1, warmup=0, output_bytes_per_second=65536, poll_seconds=.01,
                              turn_gap_seconds=0, archive_bytes=65536 * 64)
    result = asyncio.run(sample(root, 'output_open', options))
    assert result['cleanup_confirmed'] and result['output_ms']
