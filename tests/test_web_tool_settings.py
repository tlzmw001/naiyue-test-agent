"""Settings are consumed by startup and never include real credentials."""

import pytest


def test_web_and_aitest_configuration_is_loaded(tmp_path):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[network]\nmode="direct"\n'
        '[tools.web_search]\nprovider_order=["tavily", "baidu"]\nrequest_timeout_seconds=0\n'
        '[tools.aitest_kit]\nexecutable="python"\nprefix_args=["-m", "aitest_kit.cli"]\n')
    settings = load_settings(path)
    assert settings.network.mode == 'direct'
    assert settings.tools.web_search.provider_order == ('tavily', 'baidu')
    assert settings.tools.web_search.request_timeout_seconds == 0
    assert settings.tools.aitest_kit.prefix_args == ['-m', 'aitest_kit.cli']


def test_invalid_forced_proxy_fails_startup_instead_of_silently_going_direct(tmp_path):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[network]\nmode="proxy"\n')
    with pytest.raises(ValueError):
        load_settings(path)


def test_aitest_settings_do_not_expose_unused_shell_probe_options():
    from naiyue_test_agent.tools.settings import AITestSettings
    for kwargs in ({'probe_timeout_seconds': 1}, {'max_command_chars': 10}):
        with pytest.raises(ValueError):
            AITestSettings(**kwargs)
