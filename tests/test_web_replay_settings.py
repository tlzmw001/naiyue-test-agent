"""Replay budget uses the existing startup settings file and explicit Web overrides."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from native_application_helpers import async_test
from naiyue_test_agent.tools.settings import ToolSettings, load_tool_settings
from naiyue_test_agent.web.settings import WebSettings


def test_replay_budget_defaults_and_repository_sample(tmp_path):
    assert load_tool_settings(tmp_path / 'missing.toml').web_event_cache_bytes == 4194304
    assert ToolSettings().web_event_cache_bytes == 4194304
    sample = Path(__file__).resolve().parents[1] / 'docs/examples/settings.example.toml'
    assert load_tool_settings(sample).web_event_cache_bytes == 4194304


@pytest.mark.parametrize('value', ['0', '-1', 'true', '"1024"', '[]', '{}'])
def test_invalid_replay_budget_falls_back_without_discarding_permission_rules(tmp_path, caplog, value):
    path = tmp_path / 'settings.toml'
    path.write_text(f'web_event_cache_bytes = {value}\nmax_active_runs = 3\n'
                    '[permissions]\ndeny = [{tool = "read", path = "/private/**"}]\n')
    settings = load_tool_settings(path)
    assert settings.web_event_cache_bytes == 4194304
    assert settings.max_active_runs == 3
    assert len(settings.permissions.deny) == 1
    assert settings.permissions.deny[0].tool == 'read'
    assert settings.permissions.deny[0].path == '/private/**'
    assert 'web_event_cache_bytes_fallback' in caplog.text


@pytest.mark.parametrize('value', [0, -1, True, '1024'])
def test_explicit_web_budget_requires_positive_integer(value):
    with pytest.raises(ValidationError):
        WebSettings(database_path='unused', frontend_dist='unused', event_cache_bytes=value)


@pytest.mark.parametrize('override, expected', [(None, 2048), (7, 7)])
@async_test
async def test_real_web_startup_loads_config_and_preserves_explicit_override(monkeypatch, tmp_path, override, expected):
    from naiyue_test_agent.tools import settings as module
    from test_async_application import StreamingToolModel
    from test_web_api import web_client
    (tmp_path / 'settings.toml').write_text('web_event_cache_bytes = 2048\n')
    monkeypatch.setattr(module, 'get_agent_dir', lambda: tmp_path)
    options = {} if override is None else {'event_cache_bytes': override}
    async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), **options) as (client, app):
        assert app.state.delivery.settings.event_cache_bytes == expected
        assert app.state.service.settings.event_cache_bytes == expected


@async_test
async def test_injected_runtime_settings_use_same_replay_budget(monkeypatch, tmp_path):
    from test_async_application import StreamingToolModel
    from test_web_api import web_client
    runtime = ToolSettings(web_event_cache_bytes=1234)
    async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), runtime_settings=runtime) as (_, app):
        assert app.state.delivery.settings.event_cache_bytes == 1234
