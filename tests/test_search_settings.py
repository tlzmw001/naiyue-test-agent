"""Search configuration is validated, frozen, and wired into actual builtins."""

import pytest
from pydantic import ValidationError

from naiyue_test_agent.tools import GrepSettings, FindSettings, ToolSettings, create_builtin_tools, load_tool_settings
from naiyue_test_agent.application import prepare_tool_registry


def test_search_settings_roundtrip_schema_and_process_snapshot(tmp_path, monkeypatch):
    from naiyue_test_agent.tools.processes import HostProcessRunner
    original = HostProcessRunner.__init__
    seen = []
    def record(self, settings=None):
        seen.append(settings)
        original(self, settings)
    monkeypatch.setattr(HostProcessRunner, '__init__', record)
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.grep]\ndefault_limit=2\nmax_limit=3\nmax_record_bytes=1024\n'
        'default_context_lines=1\nmax_context_lines=2\n'
        '[tools.find]\ndefault_limit=4\nmax_limit=5\nmax_record_bytes=512\n'
        '[process]\nmax_pending_output_bytes=8192\n')
    settings = load_tool_settings(path)
    specs = create_builtin_tools(settings)
    registry = prepare_tool_registry(specs)
    names = registry.names()
    assert names == ('read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent')
    assert seen == [settings.process] * 4
    schemas = {s.tool.name: s.tool.tool_call_schema.model_json_schema() for s in specs}
    assert schemas['grep']['properties']['limit']['default'] == 2
    assert schemas['grep']['properties']['context']['maximum'] == 2
    assert schemas['find']['properties']['limit']['maximum'] == 5
    for name in ('grep', 'find'):
        spec = next(s for s in specs if s.tool.name == name)
        assert spec.tool.coroutine is not None and spec.side_effects == 'read_only'
        assert 'runtime' not in schemas[name]['properties']
    with pytest.raises(ValidationError):
        settings.tools.grep.max_record_bytes = 4096


@pytest.mark.parametrize('section,content', [
    ('grep', 'default_limit=4\nmax_limit=3'),
    ('grep', 'default_context_lines=2\nmax_context_lines=1'),
    ('grep', 'max_record_bytes=0'),
    ('grep', 'max_argument_bytes=true'),
    ('grep', 'max_argument_bytes=0'),
    ('find', 'max_record_bytes=-1'),
    ('find', 'max_diagnostic_bytes="PRIVATE"'),
])
def test_invalid_search_budgets_fall_back_without_losing_deny(tmp_path, caplog, section, content):
    path = tmp_path / 'settings.toml'
    path.write_text('[permissions]\ndeny=[{tool="grep",path="${workspace}/private"}]\n'
                    f'[tools.{section}]\n{content}\n')
    settings = load_tool_settings(path)
    assert settings.tools == ToolSettings().tools
    assert len(settings.permissions.deny) == 1
    assert 'tool_settings_fallback' in caplog.text and 'PRIVATE' not in caplog.text
