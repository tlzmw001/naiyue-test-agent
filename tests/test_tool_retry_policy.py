"""Per-tool deadlines, retry credits and cooldowns are deterministic contracts."""

import asyncio
from types import SimpleNamespace

import pytest


def test_retry_configuration_loads_and_rejects_unsupported_tools(tmp_path):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[tool_retry]\ntools=["web_fetch", "mcp__docs__*"]\nmax_retries=3\n'
                    'attempt_timeout_seconds=5\nreplay_safe_tools=["mcp__docs__search"]\n')
    settings = load_settings(path).tool_retry
    assert settings.max_retries == 3 and settings.applies('mcp__docs__search')
    assert not settings.applies('web_search')
    assert settings.can_replay('mcp__docs__search')
    path.write_text('[tool_retry]\ntools=["bash"]\n')
    with pytest.raises(ValueError, match='retry'):
        load_settings(path)


@pytest.mark.parametrize('fields', [
    {'max_retries': -1}, {'max_retries': True}, {'attempt_timeout_seconds': 0},
    {'attempt_timeout_seconds': float('inf')}, {'initial_delay_seconds': -1},
    {'backoff_factor': .5}, {'max_delay_seconds': float('nan')},
    {'tools': ['*']}, {'tools': ['mcp__bad/name__x']},
    {'replay_safe_tools': ['mcp__docs__*']}, {'replay_safe_tools': ['web_fetch']},
])
def test_invalid_retry_fields_are_not_silently_enabled(fields):
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    with pytest.raises(ValueError):
        ToolRetrySettings(**fields)


def test_total_deadline_and_per_provider_cooldown_share_one_budget(monkeypatch):
    from naiyue_test_agent.tools import retry
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    now = [0.0]
    monkeypatch.setattr(retry, 'monotonic', lambda: now[0])
    monkeypatch.setattr(retry.random, 'uniform', lambda low, high: high)
    async def scenario():
        budget = retry.RetryBudget(ToolRetrySettings(attempt_timeout_seconds=10), 'web_search', 30)
        assert budget.attempt_seconds(15) == 10
        budget.schedule('baidu', retry_after=5)
        now[0] = 3
        budget.schedule('tavily')
        assert budget.next_candidate(['baidu', 'tavily']) == 'tavily'
        now[0] = 7
        assert await budget.claim_retry('baidu')
        assert budget.retries == 1 and budget.remaining() == 23
        assert await budget.claim_retry('tavily')
        assert not await budget.claim_retry('baidu')
        now[0] = 30
        with pytest.raises(TimeoutError):
            budget.remaining()
    asyncio.run(scenario())


def test_excluded_tools_keep_original_timeout_and_have_no_retries():
    from naiyue_test_agent.tools.retry import RetryBudget
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    async def scenario():
        budget = RetryBudget(ToolRetrySettings(tools=[]), 'web_fetch', 0)
        assert budget.attempt_seconds() == 0
        assert not await budget.claim_retry('fetch')
        assert await budget.attempt(lambda: asyncio.sleep(0, result=42)) == 42
    asyncio.run(scenario())


def test_attempt_timeout_settles_before_retry_and_stop_never_retries():
    from naiyue_test_agent.tools.retry import RetryBudget
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    async def scenario():
        started, settled = asyncio.Event(), asyncio.Event()
        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()
        budget = RetryBudget(ToolRetrySettings(attempt_timeout_seconds=.01), 'web_fetch', 1)
        with pytest.raises(TimeoutError):
            await budget.attempt(pending)
        assert started.is_set() and settled.is_set()
        stopped = RetryBudget(ToolRetrySettings(), 'web_fetch', 30, control=SimpleNamespace(requested=True))
        from langchain_core.tools import ToolException
        with pytest.raises(ToolException, match='stopped'):
            await stopped.claim_retry('fetch')
    asyncio.run(scenario())


def test_network_errors_are_typed_and_retry_after_is_not_truncated():
    import httpx
    from naiyue_test_agent.tools.retry import is_transient_error, retry_after_seconds
    assert is_transient_error(httpx.ReadError('PRIVATE'))
    assert is_transient_error(TimeoutError())
    assert not is_transient_error(httpx.ProxyError('PRIVATE'))
    assert not is_transient_error(ValueError('PRIVATE'))
    assert not is_transient_error(ExceptionGroup('mixed', [TimeoutError(), ValueError()]))
    assert retry_after_seconds({'retry-after': '120'}) == 120
    assert retry_after_seconds({'retry-after': 'bad'}) == 0


def test_backoff_grows_with_jitter_is_capped_and_skips_retry_after_past_deadline(monkeypatch):
    from naiyue_test_agent.tools import retry
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    monkeypatch.setattr(retry, 'monotonic', lambda: 0)
    bounds = []
    monkeypatch.setattr(retry.random, 'uniform', lambda low, high: bounds.append((low, high)) or high)
    budget = retry.RetryBudget(ToolRetrySettings(initial_delay_seconds=2, max_delay_seconds=5), 'web_fetch', 30)
    for _ in range(4):
        budget.schedule('fetch')
    assert bounds == [(0, 2), (0, 4), (0, 5), (0, 5)]
    budget.schedule('fetch', retry_after=120)
    assert budget.next_candidate(['fetch']) is None and budget.retries == 0


def test_finite_extreme_backoff_values_do_not_overflow_before_cap(monkeypatch):
    from naiyue_test_agent.tools import retry
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    bounds = []
    monkeypatch.setattr(retry.random, 'uniform', lambda low, high: bounds.append(high) or 0)
    budget = retry.RetryBudget(ToolRetrySettings(initial_delay_seconds=1e-300,
        backoff_factor=1e300, max_delay_seconds=1e308), 'web_fetch', 0)
    for _ in range(4):
        budget.schedule('fetch')
    assert bounds == pytest.approx([1e-300, 1, 1e300, 1e308])


@pytest.mark.parametrize('stop', [False, True])
def test_backoff_stop_or_cancel_settles_wait_without_claiming_another_attempt(monkeypatch, stop):
    from naiyue_test_agent.tools import retry
    from naiyue_test_agent.tools.retry_settings import ToolRetrySettings
    from langchain_core.tools import ToolException
    from threading import Event
    async def scenario():
        queued = asyncio.Event()
        signal = Event()
        class Control:
            @property
            def requested(self):
                return signal.is_set()
        progress = SimpleNamespace(report=lambda **_: queued.set())
        budget = retry.RetryBudget(ToolRetrySettings(initial_delay_seconds=120, max_delay_seconds=120),
                                  'web_fetch', 0, control=Control(), progress=progress)
        monkeypatch.setattr(retry.random, 'uniform', lambda low, high: high)
        budget.schedule('fetch')
        task = asyncio.create_task(budget.claim_retry('fetch'))
        await queued.wait()
        if stop:
            signal.set()
        else:
            task.cancel()
        with pytest.raises(ToolException if stop else asyncio.CancelledError):
            await task
        assert budget.retries == 0
    asyncio.run(scenario())
