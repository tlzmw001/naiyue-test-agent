"""Public timeout semantics reach the existing tool/permission execution chain."""

from dataclasses import replace
from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test

import asyncio
import json

import pytest
from pydantic import ValidationError

from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessOutcome
from naiyue_test_agent.tools.shell import ShellAdapter
from naiyue_test_agent.settings import AppSettings
from tool_feedback_helpers import invoke_tool_feedback


@pytest.fixture(params=['bash', 'powershell', 'grep', 'find'])
def spec(request, monkeypatch):
    # Exercise the actual tool factory without requiring PowerShell on macOS.
    monkeypatch.setattr(ShellAdapter, 'resolve_executable', lambda *args, **kwargs: 'owned-shell')
    monkeypatch.setattr(ShellAdapter, 'probe_version', lambda *args: '7.4')
    return next(item for item in create_builtin_tools(AppSettings().tools) if item.tool.name == request.param)


def args_for(spec):
    return {'command': 'test-command'} if spec.tool.name in ('bash', 'powershell') else {'pattern': 'HIT'}


def test_public_timeout_schema_is_optional_strict_nonnegative_and_has_no_300_cap(spec):
    args = args_for(spec)
    for value in (None, 0, 600):
        assert spec.validate_input({**args, 'timeout': value}).timeout == value
    assert spec.validate_input(args).timeout is None
    for value in (-1, True, 1.5, '30'):
        with pytest.raises(ValidationError):
            spec.validate_input({**args, 'timeout': value})
    for schema in (spec.input_schema, spec.tool.tool_call_schema):
        shape = schema.model_json_schema()
        field = shape['properties']['timeout']
        assert field['default'] is None and 'timeout' not in shape.get('required', [])
        assert field['anyOf'][0] == {'minimum': 0, 'type': 'integer'}
        assert 'seconds' in field['description'] and '0' in field['description']
        assert 'runtime' not in shape['properties']


@async_test
@pytest.mark.parametrize('value', ['omitted', None, 0, 600])
async def test_normalized_timeout_reaches_process_requests_through_formal_graph(spec, monkeypatch, tmp_path, value):
    target = tmp_path / 'note.txt'
    target.write_text('HIT\n')
    seen = []
    async def run(self, request, consume, control=None):
        seen.append(request)
        if '--files' in request.argv or '--print0' in request.argv:
            await consume('stdout', (str(target) + '\0').encode())
        elif '--json' in request.argv:
            record = {'type': 'match', 'data': {'path': {'text': str(target)},
                'line_number': 1, 'lines': {'text': 'HIT\n'}}}
            await consume('stdout', (json.dumps(record) + '\n').encode())
        else:
            await consume('stdout', b'HIT\n')
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    monkeypatch.setattr(HostProcessRunner, 'run', run)
    args = args_for(spec)
    if value != 'omitted':
        args['timeout'] = value
    feedback = (await invoke_tool_feedback(spec, args, call_id='timeout',
        context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)), permission_mode='BypassApprovals'))
    assert feedback.status == 'success'
    assert ('note.txt' if spec.tool.name == 'find' else 'HIT') in feedback.content
    assert seen
    if value in ('omitted', None, 0):
        assert all(request.timeout_seconds is None for request in seen)
    else:
        assert all(0 < request.timeout_seconds <= 600 for request in seen)
        assert max(request.timeout_seconds for request in seen) > 300


@async_test
async def test_invalid_timeout_is_tool_feedback_without_starting_process(spec, monkeypatch, tmp_path):
    async def forbidden(*args, **kwargs):
        pytest.fail('Invalid input must not acquire a process')
    monkeypatch.setattr(HostProcessRunner, 'run', forbidden)
    feedback = (await invoke_tool_feedback(spec, {**args_for(spec), 'timeout': -1}, call_id='invalid',
        context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)), permission_mode='BypassApprovals'))
    assert feedback.status == 'error' and 'timeout' in feedback.content
    assert 'runtime' not in feedback.content


def test_obsolete_execution_default_fields_are_absent_but_probe_cleanup_remain():
    from naiyue_test_agent.settings import AppSettings
    settings = AppSettings()
    for shell in (settings.tools.bash, settings.tools.powershell):
        assert not hasattr(shell, 'default_timeout_seconds') and not hasattr(shell, 'max_timeout_seconds')
        assert shell.probe_timeout_seconds > 0
    assert not hasattr(settings.tools.grep, 'timeout_seconds')
    assert not hasattr(settings.tools.find, 'timeout_seconds')
    assert settings.process.cleanup_timeout_seconds > 0


@pytest.mark.parametrize('section,key', [('bash', 'default_timeout_seconds'),
    ('bash', 'max_timeout_seconds'), ('powershell', 'default_timeout_seconds'),
    ('powershell', 'max_timeout_seconds'), ('grep', 'timeout_seconds'), ('find', 'timeout_seconds')])
def test_old_timeout_config_keys_are_rejected_without_compatibility_read(tmp_path, caplog, section, key):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text(f'[permissions]\nmode="NoAsk"\n[tools.{section}]\n{key}=600\n')
    loaded = load_settings(path)
    assert loaded.tools == AppSettings().tools and loaded.permissions.mode == 'NoAsk'
    assert 'tool_settings_fallback' in caplog.text


def test_async_approval_resume_preserves_explicit_timeout_and_does_not_run_before_approval(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage
    from naiyue_test_agent.tools.bash import create_session_bash_tool
    from test_async_application import make_app, StreamingToolModel, call
    async def scenario():
        seen = []
        monkeypatch.setattr(ShellAdapter, 'resolve_executable', lambda *args, **kwargs: 'owned-shell')
        monkeypatch.setattr(ShellAdapter, 'probe_version', lambda *args: '5.2')
        async def run(self, request, consume, control=None):
            seen.append(request.timeout_seconds)
            await consume('stdout', b'approved\n')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        monkeypatch.setattr(HostProcessRunner, 'run', run)
        model = StreamingToolModel(replies=[call('bash', {'command': 'test-command', 'timeout': 600}),
                                            AIMessage(content='done')])
        app = await make_app(monkeypatch, model, [create_session_bash_tool()])
        sid = await app.sessions.create_workspace('A', tmp_path)
        try:
            pending = await app.runs.start(sid, [('user', 'run')])
            assert pending['approval_requests'] and seen == []
            result = await app.runs.resume_approval(sid, ['approve'])
            assert seen == [600]
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert feedback.status == 'success' and 'approved' in feedback.content
            assert len(model.seen) == 2
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('timeout', [0, 600])
def test_native_async_graph_runs_each_tool_with_the_new_timeout_contract(spec, monkeypatch, tmp_path, timeout):
    from dataclasses import replace
    import sys
    from langchain_core.messages import AIMessage, ToolMessage
    from test_async_application import make_app, StreamingToolModel, call
    async def scenario():
        (tmp_path / 'HIT').write_text('HIT\n')
        original, seen = HostProcessRunner.run, []
        async def record(self, request, consume, control=None):
            seen.append(request.timeout_seconds)
            if request.argv[0] == 'owned-shell':
                # Interpreter fixture only; execution, streaming and cleanup stay real.
                request = replace(request, argv=[sys.executable, '-u', '-c', "print('HIT')"])
            return await original(self, request, consume, control)
        monkeypatch.setattr(HostProcessRunner, 'run', record)
        model = StreamingToolModel(replies=[call(spec.tool.name, {**args_for(spec), 'timeout': timeout}),
                                            AIMessage(content='done')])
        app = await make_app(monkeypatch, model, [spec])
        sid = await app.sessions.create_workspace('A', tmp_path)
        await app.sessions.select_permission_mode(sid, 'BypassApprovals')
        try:
            result = await app.runs.start(sid, [('user', 'run')])
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert feedback.status == 'success' and 'HIT' in feedback.content
            assert result['messages'][-1].content == 'done' and len(model.seen) == 2
            assert seen
            if timeout == 0:
                assert all(value is None for value in seen)
            else:
                assert all(300 < value <= 600 for value in seen)
        finally:
            await app.aclose()
    asyncio.run(scenario())
