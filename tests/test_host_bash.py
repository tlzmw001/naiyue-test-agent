"""Explicitly enabled host Bash requires exact approval and bounded execution."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test, build_application

from tool_feedback_helpers import invoke_tool_feedback

import logging
import os
import re
import subprocess
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import sys

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from naiyue_test_agent.tools import create_session_read_tool, create_session_bash_tool, BashSettings
from naiyue_test_agent.tools.permissions import OneUseApprovalGrants
from naiyue_test_agent.model_access import ModelCatalog


class BashFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

async def make_app(monkeypatch, command: str, *, timeout: int | None = None, settings=None, shell_task_settings=None):
    # Simulate an activated test venv in the inherited parent environment.
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    args = {'command': command}
    if timeout is not None:
        args['timeout'] = timeout
    model = BashFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'bash', 'id': 'bash-1', 'args': args}]),
        AIMessage(content='done'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return await build_application(
        Path('synthetic.json'), tools=[create_session_bash_tool(settings=settings, shell_task_settings=shell_task_settings)],
        system_prompt='', response_format=None,
    )

def bash_results(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)
            and item.name == 'bash']

@async_test
async def test_host_bash_approval_then_runs_in_trusted_workspace(monkeypatch, tmp_path, caplog):
    root = tmp_path / 'workspace'
    root.mkdir()
    marker = root / 'created.txt'
    command = 'printf approved > created.txt; pwd'
    app = await make_app(monkeypatch, command)
    session = await app.acreate_workspace_session('A', root)

    with caplog.at_level(logging.INFO):
        pending = await app.arun(session, [('user', 'run a command')])
    assert not marker.exists()
    assert bash_results(pending) == []
    assert pending['approval_requests'] == [{
        'tool_call_id': 'bash-1', 'tool_name': 'bash',
        'target': '[automatic background after 120s; original execution deadline applies] ' + command,
        'workspace': str(root.resolve()),
    }]
    assert app.pending_tool_approvals(session) == pending['approval_requests']

    with caplog.at_level(logging.INFO):
        result = await app.aresume_tool_approval(session, ['approve'])
    assert marker.read_text() == 'approved'
    tool_result = bash_results(result)[0]
    assert tool_result.tool_call_id == 'bash-1'
    assert 'exit_code=0' in tool_result.content
    assert str(root.resolve()) in tool_result.content
    assert result['messages'][-1].content == 'done'
    assert command not in caplog.text
    assert 'approved' not in caplog.text

@async_test
async def test_host_bash_reject_has_no_side_effect(monkeypatch, tmp_path):
    marker = tmp_path / 'blocked.txt'
    app = await make_app(monkeypatch, 'printf blocked > blocked.txt')
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['reject'])
    assert not marker.exists()
    assert bash_results(result)[0].status == 'error'

@async_test
async def test_host_bash_can_access_outside_workspace_after_approval(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    command = f'printf outside > "{outside}"'
    app = await make_app(monkeypatch, command)
    session = await app.acreate_workspace_session('A', root)
    await app.arun(session, [('user', 'run')])
    await app.aresume_tool_approval(session, ['approve'])
    assert outside.read_text() == 'outside'

@async_test
async def test_host_bash_nonzero_and_inherited_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-token')
    command = 'if [ -n "$OPENAI_API_KEY" ]; then echo inherited; else exit 9; fi; exit 7'
    app = await make_app(monkeypatch, command)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert 'exit_code=7' in message.content
    assert message.status == 'error'
    assert 'inherited' in message.content
    assert 'secret-test-token' not in str(result)

@async_test
async def test_host_bash_timeout_returns_error_and_model_continues(monkeypatch, tmp_path):
    app = await make_app(monkeypatch, 'sleep 3', timeout=1)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert message.status == 'error' and message.tool_call_id == 'bash-1'
    assert 'timed out' in message.content and 'Inspect side effects' in message.content
    assert result['messages'][-1].content == 'done'
    assert not app._sessions.require(session).describe_session()['manual_review_required']

@async_test
async def test_host_bash_output_is_bounded(monkeypatch, tmp_path):
    app = await make_app(monkeypatch, 'printf "%0100000d" 0')
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert len(message.content) < 70000
    assert 'truncated=true' in message.content

def test_bash_grant_is_one_use_and_exact(tmp_path):
    root = tmp_path.resolve()
    grant = OneUseApprovalGrants({'id-1': ('printf ok', root)})
    assert not grant.consume('id-2', ('printf ok', root))
    assert not grant.consume('id-1', ('printf changed', root))
    assert not grant.consume('id-1', ('printf ok', root.parent))
    assert grant.consume('id-1', ('printf ok', root))
    assert not grant.consume('id-1', ('printf ok', root))



@pytest.mark.parametrize('args', [
    {'timeout': 1},
    {'command': ''},
    {'command': 'printf ok', 'timeout': '3'},
    {'command': 'printf ok', 'unexpected': True},
])
@async_test
async def test_invalid_bash_args_return_tool_error_without_approval(monkeypatch, tmp_path, args):
    model = BashFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'bash', 'id': 'bad-args', 'args': args}]),
        AIMessage(content='I will correct the arguments'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[create_session_bash_tool()],
        system_prompt='', response_format=None,
    )
    session = await app.acreate_workspace_session('A', tmp_path)

    result = await app.arun(session, [('user', 'run')])
    assert 'approval_requests' not in result
    message = bash_results(result)[0]
    assert message.tool_call_id == 'bad-args'
    assert message.status == 'error'
    assert 'Invalid bash arguments' in message.content
    assert result['messages'][-1].content == 'I will correct the arguments'

@async_test
async def test_invalid_and_valid_bash_in_one_batch_only_valid_needs_approval(
    monkeypatch, tmp_path,
):
    model = BashFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'bash', 'id': 'bad', 'args': {'timeout': 1}},
            {'name': 'bash', 'id': 'good', 'args': {'command': 'printf ok > result.txt'}},
        ]),
        AIMessage(content='handled both'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = await build_application(
        Path('synthetic.json'), tools=[create_session_bash_tool()],
        system_prompt='', response_format=None,
    )
    session = await app.acreate_workspace_session('A', tmp_path)

    pending = await app.arun(session, [('user', 'run both')])
    assert [item['tool_call_id'] for item in pending['approval_requests']] == ['good']
    assert not (tmp_path / 'result.txt').exists()
    result = await app.aresume_tool_approval(session, ['approve'])
    assert (tmp_path / 'result.txt').read_text() == 'ok'
    assert [(item.tool_call_id, item.status) for item in bash_results(result)] == [
        ('bad', 'error'), ('good', 'success'),
    ]

@async_test
async def test_long_bash_output_keeps_failure_summary_at_tail(monkeypatch, tmp_path):
    command = 'python -c "print(\'A\'*70000); print(\'FAILED 1 test\')"'
    app = await make_app(monkeypatch, command)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert 'truncated=true' in message.content
    assert 'FAILED 1 test' in message.content
    assert 'A' * 70000 not in message.content

@async_test
async def test_bash_tail_does_not_split_utf8_character(monkeypatch, tmp_path):
    command = 'python -c "import sys; sys.stdout.buffer.write(b\'A\'*65535 + \'中\'.encode())"'
    app = await make_app(monkeypatch, command)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert 'truncated=true' in message.content
    assert '中' in message.content
    assert '\ufffd' not in message.content


@pytest.mark.parametrize(('source', 'expected_status', 'summary'), [
    ('def test_case():\n    assert 1 == 1\n', 'success', 'passed'),
    ('def test_case():\n    assert 1 == 2\n', 'error', 'failed'),
])
@async_test
async def test_actual_pytest_result_reaches_model_tool_message(
    monkeypatch, tmp_path, source, expected_status, summary,
):
    (tmp_path / 'test_generated.py').write_text(source, encoding='utf-8')
    app = await make_app(monkeypatch, 'python -m pytest -q test_generated.py')
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run generated test')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert message.status == expected_status
    assert summary in message.content
    assert message.tool_call_id == 'bash-1'

@async_test
async def test_timeout_keeps_bounded_partial_output_for_model_feedback(
    monkeypatch, tmp_path, caplog,
):
    app = await make_app(monkeypatch, 'printf "EARLY_DIAGNOSTIC\\n"; sleep 3', timeout=1)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])

    with caplog.at_level(logging.INFO):
        result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert message.status == 'error' and 'EARLY_DIAGNOSTIC' in message.content
    assert 'output:\n' in message.content
    assert 'EARLY_DIAGNOSTIC' not in caplog.text
    assert result['messages'][-1].content == 'done'

@async_test
async def test_timeout_partial_output_respects_merged_budget(monkeypatch, tmp_path):
    command = ('python -c "import sys; '
               "sys.stdout.write('A'*70000+'OUT_TAIL'); sys.stdout.flush(); "
               "sys.stderr.write('B'*70000+'ERR_TAIL'); sys.stderr.flush()"
               '"; sleep 3')
    app = await make_app(monkeypatch, command, timeout=1)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])

    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert message.status == 'error' and 'truncated=true' in message.content
    output = message.content.split('output:\n')[1].split('\narchive_complete=')[0]
    assert len(output.encode('utf-8')) <= 65536 and output.endswith('ERR_TAIL')
    assert 'OUT_TAIL' not in output and 'partial_line=true' in message.content


def test_bash_model_schema_explains_timeout_and_output_limit():
    schema = create_session_bash_tool().tool.tool_call_schema.model_json_schema()
    description = schema['description']
    assert 'Configured Shell execution limit: 0 seconds' in description
    assert '0 means unlimited' in description
    assert 'cannot extend' in description
    assert schema['properties']['timeout']['anyOf'][0] == {'minimum': 0, 'type': 'integer'}
    assert '65536' in description and '2000' in description
    assert 'workspace' in description

def _archive_path(message: str, stream: str) -> str:
    match = re.search(rf'{stream}_path=([^;\n]*naiyue-agent-output[/\\][0-9a-f]{{32}}-{stream}\.log)',
                      message)
    assert match is not None, message
    return match.group(1)

@async_test
async def test_long_bash_output_is_saved_outside_workspace_and_requires_read_permission(monkeypatch, tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    app = await make_app(monkeypatch, "python -c \"print('A'*70000); print('END')\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    path = _archive_path(message, 'raw')
    assert 'archive_complete=true' in message
    assert 'truncated=true' in message
    assert (tmp_path / path).read_text().endswith('END\n')
    denied = await invoke_tool_feedback(create_session_read_tool(), {'path': path},
        call_id='archive-denied', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)))
    assert denied.status == 'error' and 'access denied' in denied.content
    assert 'A' * 5000 in (await invoke_tool_feedback(create_session_read_tool(), {'path': path},
        call_id='archive-read', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None)), permission_mode='BypassApprovals')).content
    output_dir = Path(path).parent
    assert not output_dir.is_relative_to(tmp_path)
    assert output_dir.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / path).stat().st_mode & 0o777 == 0o600
    assert subprocess.run(['git', '-C', str(tmp_path), 'status', '--porcelain'], capture_output=True,
                          check=False).returncode == 0
    assert not (tmp_path / 'naiyue-agent-output').exists()

@async_test
async def test_short_bash_output_creates_no_artifacts(monkeypatch, tmp_path):
    app = await make_app(monkeypatch, 'printf short')
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    assert 'raw_path=' not in bash_results(result)[0].content
    assert not (tmp_path / 'naiyue-agent-output').exists()

@async_test
async def test_archive_preserves_both_streams_without_claiming_order(monkeypatch, tmp_path):
    command = ('python -c "import sys; '
               "sys.stdout.write('O'*70000); sys.stderr.write('E'*70000)"
               '"')
    app = await make_app(monkeypatch, command)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    raw = (tmp_path / _archive_path(message, 'raw')).read_text()
    assert len(raw) == 140000 and raw.count('O') == raw.count('E') == 70000

@async_test
async def test_archive_quota_is_explicit_and_command_still_finishes(monkeypatch, tmp_path):
    app = await make_app(monkeypatch, "python -c \"print('Q'*100000)\"",
        settings=BashSettings(max_archive_bytes=5000))
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    assert 'exit_code=0' in message
    assert 'archive_complete=false' in message
    assert 'archive_reason=command_limit' in message
    assert 'Q' * 100000 not in message
    assert (tmp_path / _archive_path(message, 'raw')).stat().st_size == 5000

@async_test
async def test_existing_unowned_output_directory_is_not_modified(monkeypatch, tmp_path):
    app = await make_app(monkeypatch, "python -c \"print('A'*70000)\"")
    session = await app.acreate_workspace_session('A', tmp_path)
    output_dir = app._sessions.workspace_archive_root(tmp_path) / 'naiyue-agent-output'
    output_dir.mkdir(parents=True)
    marker = output_dir / 'user-file.txt'
    marker.write_text('keep')
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    assert 'archive_complete=false' in message
    assert 'archive_reason=directory_unavailable' in message
    assert marker.read_text() == 'keep'
    assert sorted(item.name for item in output_dir.iterdir()) == ['user-file.txt']

@async_test
async def test_timeout_keeps_archive_path_in_tool_feedback(monkeypatch, tmp_path):
    app = await make_app(monkeypatch, "python -c \"print('T'*70000, flush=True)\"; sleep 3",
                   timeout=1)
    session = await app.acreate_workspace_session('A', tmp_path)
    await app.arun(session, [('user', 'run')])
    result = await app.aresume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    path = _archive_path(message.content, 'raw')
    assert (tmp_path / path).read_text().startswith('T' * 70000)
    assert 'archive_complete=true' in message.content and message.status == 'error'
    assert result['messages'][-1].content == 'done'

@async_test
async def test_archive_budgets_are_independent_across_commands(monkeypatch, tmp_path):
    paths = []
    for _ in range(2):
        app = await make_app(monkeypatch, "python -c \"print('W'*5000)\"",
            settings=BashSettings(max_output_bytes=4096, max_archive_bytes=10000))
        session = await app.acreate_workspace_session('A', tmp_path)
        await app.arun(session, [('user', 'run')])
        message = bash_results(await app.aresume_tool_approval(session, ['approve']))[0].content
        paths.append(tmp_path / _archive_path(message, 'raw'))
    assert sum(path.stat().st_size for path in paths) == 10002
    assert 'archive_complete=true' in message
    assert 'archive_reason=' not in message

def test_cli_refuses_output_cleanup_during_active_run(monkeypatch, tmp_path, capsys):
    entered, release = Event(), Event()

    class BlockingModel(BashFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager,
                                     **kwargs)

    model = BlockingModel(responses=[AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    entries = iter(('run', ':outputs clear', ':quit'))

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == ':outputs clear':
            assert entered.wait(timeout=3)
        if line == ':quit':
            release.set()
        return line

    monkeypatch.setattr('builtins.input', terminal_input)
    assert cli.main(['--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0
    assert '当前运行尚未结束，不能清理 Shell 输出' in capsys.readouterr().out
