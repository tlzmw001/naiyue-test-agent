"""Shell settings drive strict schemas, environments and both builtin registrations."""

import os

import pytest
from pydantic import ValidationError


def test_shell_defaults_and_configured_schema(tmp_path):
    from naiyue_test_agent.tools import ToolSettings, load_tool_settings, create_builtin_tools
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.bash]\nmax_command_chars=20\nprobe_timeout_seconds=1\n'
                    '[tools.bash.environment]\nunset_env=["PRIVATE"]\n'
                    'path_prepend=["/tools"]\n[tools.bash.environment.values]\nTEST="yes"\n'
                    '[tool_output]\nmax_workspace_archive_bytes=10000\n')
    settings = load_tool_settings(path)
    assert settings.tools.bash.environment.values == {'TEST': 'yes'}
    assert settings.tool_output.max_workspace_archive_bytes == 10000
    defaults = ToolSettings()
    assert defaults.tools.bash.max_output_lines == 2000
    assert defaults.tools.bash.max_output_bytes == 65536
    assert defaults.tools.powershell.execution_policy == 'inherit'
    assert defaults.tools.bash.environment.unset_env == ['BASH_ENV', 'NAIYUE_WEB_TOKEN']
    specs = {s.tool.name: s for s in create_builtin_tools(settings)}
    assert set(specs) == {'read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent'}
    for name in ('bash', 'powershell'):
        spec = specs[name]
        schema = spec.tool.tool_call_schema.model_json_schema()
        assert set(schema['properties']) == {'command', 'timeout'}
        assert spec.execution_mode == 'sequential' and spec.side_effects == 'may_mutate'
        assert spec.permissions.default == 'ask' and spec.tool.coroutine is not None
    assert specs['bash'].input_schema.model_json_schema()['properties']['command']['maxLength'] == 20
    assert '65536' in specs['bash'].tool.description
    with pytest.raises(ValidationError):
        specs['bash'].input_schema(command='x' * 21)
    assert specs['bash'].input_schema(command='ok', timeout=600).timeout == 600
    with pytest.raises(ValidationError):
        specs['bash'].input_schema(command='ok', timeout=-1)


@pytest.mark.parametrize('entry', [
    'max_output_bytes=0', 'max_command_chars=0', 'output_encoding="unknown-codec"',
    'output_encoding="base64_codec"',
    'probe_timeout_seconds=nan', 'environment={values={X=1}}',
    'environment={unset_env=["A=B"]}', 'environment={values={X="bad\u0000value"}}',
])
def test_invalid_shell_budgets_fallback_keeps_permission_rules(tmp_path, caplog, entry):
    from naiyue_test_agent.tools import ToolSettings, load_tool_settings
    path = tmp_path / 'settings.toml'
    # NUL is escaped as TOML unicode so the document remains syntactically valid.
    path.write_text('[permissions]\nmode="NoAsk"\n[tools.bash]\n' + entry.replace('\0', '\\u0000'))
    settings = load_tool_settings(path)
    assert settings.tools == ToolSettings().tools
    assert settings.permissions.mode == 'NoAsk'
    assert 'tool_settings_fallback' in caplog.text


def test_workspace_budget_fallback_is_independent(tmp_path, caplog):
    from naiyue_test_agent.tools import load_tool_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[permissions]\nmode="NoAsk"\n[tools.bash]\nmax_output_bytes=32\n'
                    '[tool_output]\nmax_workspace_archive_bytes=0\n')
    settings = load_tool_settings(path)
    assert settings.tools.bash.max_output_bytes == 32
    assert settings.tool_output.max_workspace_archive_bytes == 134217728
    assert settings.permissions.mode == 'NoAsk'
    assert 'tool_output_settings_fallback' in caplog.text


def test_environment_inherits_overrides_prepends_and_removes_last(monkeypatch):
    from naiyue_test_agent.tools.settings import EnvironmentSettings
    from naiyue_test_agent.tools.shell import build_environment
    monkeypatch.setenv('TEST_PARENT', 'inherited')
    monkeypatch.setenv('PATH', '/parent')
    settings = EnvironmentSettings(values={'PATH': '/override', 'X': 'changed'},
                                   path_prepend=['/one', '/two'], unset_env=['X'])
    env = build_environment(settings)
    assert env['TEST_PARENT'] == 'inherited'
    assert env['PATH'] == os.pathsep.join(['/one', '/two', '/override'])
    assert 'X' not in env and os.environ['PATH'] == '/parent'


def test_windows_environment_is_case_insensitive():
    from naiyue_test_agent.tools.settings import EnvironmentSettings
    from naiyue_test_agent.tools.shell import build_environment
    env = build_environment(EnvironmentSettings(values={'PATH': 'D:\\override', 'SeCrEt': 'x'},
        path_prepend=['C:\\tools'], unset_env=['SECRET']),
        parent={'Path': 'C:\\old', 'USER': 'me'}, platform='win32')
    assert env == {'PATH': 'C:\\tools;D:\\override', 'USER': 'me'}


def test_partial_environment_config_keeps_shell_specific_default_removals(tmp_path):
    from naiyue_test_agent.tools import load_tool_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.bash.environment.values]\nTEST="yes"\n'
                    '[tools.powershell.environment]\npath_prepend=["/tools"]\n')
    settings = load_tool_settings(path)
    assert settings.tools.bash.environment.unset_env == ['BASH_ENV', 'NAIYUE_WEB_TOKEN']
    assert settings.tools.powershell.environment.unset_env == ['NAIYUE_WEB_TOKEN']
