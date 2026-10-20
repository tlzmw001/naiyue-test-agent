"""Trusted user configuration fails before MCP process/network IO."""

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError


def test_mcp_defaults_and_user_loader(tmp_path):
    from naiyue_test_agent.settings import AppSettings, load_settings
    assert AppSettings().mcp.servers == {}
    path = tmp_path / 'settings.toml'
    path.write_text('[mcp.servers.local]\ntransport="stdio"\ncommand="fixture"\n')
    server = load_settings(path).mcp.servers['local']
    assert not server.enabled
    assert server.connect_timeout_seconds == 15
    assert server.call_timeout_seconds == server.read_timeout_seconds == 0
    assert server.max_tools == 64 and server.max_schema_bytes == 65536


@pytest.mark.parametrize('fields', [
    {'transport': 'sse', 'url': 'https://example.invalid'},
    {'transport': 'stdio'},
    {'transport': 'stdio', 'command': 'app', 'url': 'https://example.invalid'},
    {'transport': 'stdio', 'command': 'app', 'args': ['a\0b']},
    {'transport': 'stdio', 'command': 'app', 'env': {'KEY': 'literal-secret'}},
    {'transport': 'streamable_http', 'url': 'https://user:password@example.invalid'},
    {'transport': 'streamable_http', 'url': 'https://example.invalid/#fragment'},
    {'transport': 'streamable_http', 'url': 'http://example.invalid'},
    {'transport': 'streamable_http', 'url': 'http://localhost:3210', 'headers_env': {'X-Key': 'SECRET'}},
    {'transport': 'streamable_http', 'url': 'https://example.invalid', 'headers_env': {'Bad\nHeader': 'SECRET'}},
    {'transport': 'streamable_http', 'url': 'https://example.invalid', 'headers_env': {'Host': 'SECRET'}},
    {'transport': 'stdio', 'command': 'app', 'connect_timeout_seconds': 0},
    {'transport': 'stdio', 'command': 'app', 'connect_timeout_seconds': float('nan')},
    {'transport': 'stdio', 'command': 'app', 'max_tools': True},
    {'transport': 'stdio', 'command': 'app', 'idle_timeout_seconds': -1},
    {'transport': 'stdio', 'command': 'app', 'allowed_tools': ['a', 'a']},
])
def test_invalid_configuration(fields):
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    with pytest.raises(ValidationError):
        MCPServerSettings.model_validate(fields)


@pytest.mark.parametrize('server_id', ['too_long_server_id', 'a.b', 'a-b', '', 'a\n'])
def test_invalid_server_identity(server_id):
    from naiyue_test_agent.mcp_client.settings import MCPSettings
    with pytest.raises(ValidationError):
        MCPSettings.model_validate({'servers': {server_id: {'transport': 'stdio', 'command': 'app'}}})


def test_plain_http_explicit_optin_and_loopback():
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    for url in ['http://localhost:123', 'http://127.0.0.1:123', 'http://[::1]:123']:
        assert MCPServerSettings(transport='streamable_http', url=url).url == url
    assert MCPServerSettings(transport='streamable_http', url='http://example.invalid',
                             allow_insecure_http=True)


def test_config_error_does_not_expose_input(tmp_path, caplog):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[mcp.servers.local]\ntransport="streamable_http"\nurl="https://user:PRIVATE@example.invalid"\n')
    with pytest.raises(ValueError, match='MCP settings are invalid') as caught:
        load_settings(path)
    assert 'PRIVATE' not in str(caught.value) + caplog.text


def test_empty_and_disabled_cli_are_lazy_and_readonly(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('[mcp.servers.off]\ntransport="stdio"\ncommand="must-never-run"\n')
    program = '''
import json, sys
from naiyue_test_agent.cli import main
assert main(['mcp', 'list', '--settings', sys.argv[1]]) == 0
assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)
assert not any(name.startswith('naiyue_test_agent.models') for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', program, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'servers': []}


@pytest.mark.parametrize('value', [0, 0.05, 10])
def test_idle_timeout_accepts_explicit_nonnegative_finite_values(value):
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    server = MCPServerSettings(transport='stdio', command='app', idle_timeout_seconds=value)
    assert server.idle_timeout_seconds == value


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), True])
def test_idle_timeout_rejects_nonfinite_or_boolean_values(value):
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    with pytest.raises(ValidationError):
        MCPServerSettings(transport='stdio', command='app', idle_timeout_seconds=value)
