"""Search configuration is validated, frozen, and wired into actual builtins."""

import pytest
from pydantic import ValidationError

from naiyue_test_agent.tools import GrepSettings, FindSettings, create_builtin_tools
from naiyue_test_agent.settings import AppSettings, load_settings
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
    settings = load_settings(path)
    specs = create_builtin_tools(settings.tools, process_settings=settings.process, shell_task_settings=settings.shell_tasks, progress_settings=settings.progress)
    registry = prepare_tool_registry(specs)
    names = registry.names()
    assert names == ('read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent', 'subagent_list', 'aitest_kit', 'web_search', 'web_fetch', 'task_list', 'task_output', 'task_stop', 'history', 'memory')
    # Both file and process runners are selected by their verified providers.
    import asyncio
    from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
    from tool_feedback_helpers import invoke_tool_feedback
    from naiyue_test_agent.tools import find, grep
    from naiyue_test_agent.execution import host_processes
    from naiyue_test_agent.execution.host_shell import ShellAdapter
    async def search(*args, **kwargs):
        return 'provider search'
    monkeypatch.setattr(find, '_find_files', search)
    monkeypatch.setattr(grep, '_grep_files', search)
    monkeypatch.setattr(ShellAdapter, 'resolve_executable', lambda *_: '/owned/interpreter')
    monkeypatch.setattr(ShellAdapter, 'probe_version', lambda *_: '7.4.0')
    monkeypatch.setattr(host_processes.shutil, 'which', lambda *_a, **_k: '/owned/aitest')
    monkeypatch.setattr(host_processes, 'execute_process_request', search)
    for spec in (registry.get(name) for name in ('find', 'grep', 'bash', 'powershell', 'aitest_kit')):
        args = ({'command': 'text'} if spec.tool.name in ('bash', 'powershell') else
                {'args': ['doctor']} if spec.tool.name == 'aitest_kit' else
                {'pattern': '*.txt' if spec.tool.name == 'find' else 'text'})
        result = asyncio.run(invoke_tool_feedback(spec, args, call_id=spec.tool.name,
            context=typed_context(identity=RunIdentity('S', execution_root=ExecutionRoot(tmp_path))), permission_mode='BypassApprovals'))
        assert result.status == 'success' and result.content == 'provider search'
    # Find/Grep, Bash/PowerShell and the direct AITest CLI all use this snapshot.
    assert seen == [settings.process] * 5
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
    settings = load_settings(path)
    assert settings.tools == AppSettings().tools
    assert len(settings.permissions.deny) == 1
    assert 'tool_settings_fallback' in caplog.text and 'PRIVATE' not in caplog.text
