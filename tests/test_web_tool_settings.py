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


def test_manual_proxy_credentials_and_bypass_load_without_exposing_secrets(tmp_path, caplog):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[network]\nmode="proxy"\nproxy="http://proxy.invalid:8080"\n'
        'proxy_username="PRIVATE_USER"\nproxy_password="PRIVATE@PASSWORD#"\n'
        'no_proxy=["localhost", ".corp.example", "::1"]\n')
    settings = load_settings(path)
    assert settings.network.proxy_username == 'PRIVATE_USER'
    assert settings.network.proxy_password == 'PRIVATE@PASSWORD#'
    assert settings.network.no_proxy == ('localhost', '.corp.example', '::1')
    visible = repr(settings) + str(settings.model_dump()) + caplog.text
    assert 'PRIVATE_USER' not in visible and 'PRIVATE@PASSWORD#' not in visible


@pytest.mark.parametrize('fields', [
    {'proxy_username': 'PRIVATE_USER'}, {'proxy_password': 'PRIVATE_PASSWORD'},
    {'proxy_username': '', 'proxy_password': 'PRIVATE_PASSWORD'},
    {'proxy_username': 'PRIVATE\nUSER', 'proxy_password': 'PRIVATE_PASSWORD'},
    {'proxy_username': 'PRIVATE_USER', 'proxy_password': 'PRIVATE\0PASSWORD'},
    {'proxy': 'http://PRIVATE_USER:PRIVATE_PASSWORD@proxy.invalid',
     'proxy_username': 'PRIVATE_OTHER', 'proxy_password': 'PRIVATE_OTHER_PASSWORD'},
    {'no_proxy': 'localhost'}, {'no_proxy': ['']}, {'no_proxy': [' ']},
    {'no_proxy': ['https://corp.example']}, {'no_proxy': ['corp.example/path']},
    {'no_proxy': ['corp.example:8080']}, {'no_proxy': [True]},
])
def test_invalid_manual_network_settings_hide_inputs(fields):
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    with pytest.raises(ValueError) as caught:
        NetworkSettings(**{'mode': 'proxy', 'proxy': 'http://proxy.invalid', **fields})
    assert 'PRIVATE' not in str(caught.value)


def test_empty_proxy_password_and_old_credential_url_are_supported():
    from naiyue_test_agent.tools.web_settings import NetworkSettings
    settings = NetworkSettings(mode='proxy', proxy='http://proxy.invalid',
        proxy_username='alice', proxy_password='')
    assert settings.proxy_password == ''
    previous = NetworkSettings(mode='proxy', proxy='http://alice:password@proxy.invalid')
    assert previous.proxy == 'http://alice:password@proxy.invalid'
    assert 'password' not in repr(previous) + str(previous.model_dump())


def test_invalid_bypass_configuration_fails_loader_without_secret(tmp_path, caplog):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[network]\nmode="proxy"\nproxy="http://PRIVATE:SECRET@proxy.invalid"\n'
        'no_proxy=["https://invalid.example"]\n')
    with pytest.raises(ValueError, match='Network settings are invalid') as caught:
        load_settings(path)
    assert 'PRIVATE' not in str(caught.value) + caplog.text
    assert 'SECRET' not in str(caught.value) + caplog.text


@pytest.mark.parametrize('value, expected', [('[]', ()), ('["powershell", "read"]', ('powershell', 'read'))])
def test_default_active_tool_list_loads_in_order(tmp_path, value, expected):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('default_active_tools = ' + value)
    assert load_settings(path).default_active_tools == expected


@pytest.mark.parametrize('value', ['true', '1', '"read"', '[true]', '[""]', '[" read"]', '["read", "read"]'])
def test_invalid_default_tool_selection_fails_without_budget_fallback(tmp_path, value, caplog):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('default_active_tools = ' + value)
    with pytest.raises(ValueError, match='Default active tools are invalid'):
        load_settings(path)
    assert 'fallback' not in caplog.text and 'subagent_settings_invalid' not in caplog.text
