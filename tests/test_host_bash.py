"""Explicitly enabled host Bash requires exact approval and bounded execution."""

import logging
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
from naiyue_test_agent.application import cli
from naiyue_test_agent.agent_runtime.tools import create_read_tool, create_session_bash_tool
from naiyue_test_agent.agent_runtime.tools.bash import BashApprovalGrants, BashOutcomeUnknown
from naiyue_test_agent.model_access import ModelCatalog


class BashFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_app(monkeypatch, command: str, *, timeout: int | None = None):
    args = {'command': command}
    if timeout is not None:
        args['timeout'] = timeout
    model = BashFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'bash', 'id': 'bash-1', 'args': args}]),
        AIMessage(content='done'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    return application.build_local_application(
        Path('synthetic.json'), tools=[create_session_bash_tool()],
        system_prompt='', response_format=None,
    )


def bash_results(result):
    return [item for item in result['messages'] if isinstance(item, ToolMessage)
            and item.name == 'bash']


def test_host_bash_approval_then_runs_in_trusted_workspace(monkeypatch, tmp_path, caplog):
    root = tmp_path / 'workspace'
    root.mkdir()
    marker = root / 'created.txt'
    command = 'printf approved > created.txt; pwd'
    app = make_app(monkeypatch, command)
    session = app.create_workspace_session('A', root)

    with caplog.at_level(logging.INFO):
        pending = app.run(session, [('user', 'run a command')])
    assert not marker.exists()
    assert bash_results(pending) == []
    assert pending['approval_requests'] == [{
        'tool_call_id': 'bash-1', 'tool_name': 'bash',
        'target': command, 'workspace': str(root.resolve()),
    }]
    assert app.pending_tool_approvals(session) == pending['approval_requests']

    with caplog.at_level(logging.INFO):
        result = app.resume_tool_approval(session, ['approve'])
    assert marker.read_text() == 'approved'
    tool_result = bash_results(result)[0]
    assert tool_result.tool_call_id == 'bash-1'
    assert 'exit_code=0' in tool_result.content
    assert str(root.resolve()) in tool_result.content
    assert result['messages'][-1].content == 'done'
    assert command not in caplog.text
    assert 'approved' not in caplog.text


def test_host_bash_reject_has_no_side_effect(monkeypatch, tmp_path):
    marker = tmp_path / 'blocked.txt'
    app = make_app(monkeypatch, 'printf blocked > blocked.txt')
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['reject'])
    assert not marker.exists()
    assert bash_results(result)[0].status == 'error'


def test_host_bash_can_access_outside_workspace_after_approval(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    command = f'printf outside > "{outside}"'
    app = make_app(monkeypatch, command)
    session = app.create_workspace_session('A', root)
    app.run(session, [('user', 'run')])
    app.resume_tool_approval(session, ['approve'])
    assert outside.read_text() == 'outside'


def test_host_bash_nonzero_and_scrubbed_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-token')
    command = 'if [ -n "$OPENAI_API_KEY" ]; then echo leaked; exit 9; fi; echo clean; exit 7'
    app = make_app(monkeypatch, command)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert 'exit_code=7' in message.content
    assert message.status == 'error'
    assert 'clean' in message.content
    assert 'secret-test-token' not in str(result)


def test_host_bash_timeout_requires_manual_review(monkeypatch, tmp_path):
    app = make_app(monkeypatch, 'sleep 3', timeout=1)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    with pytest.raises(BashOutcomeUnknown):
        app.resume_tool_approval(session, ['approve'])
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'try again')])


def test_host_bash_output_is_bounded(monkeypatch, tmp_path):
    app = make_app(monkeypatch, 'printf "%010000d" 0')
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert len(message.content) < 10000
    assert 'truncated=true' in message.content


def test_bash_grant_is_one_use_and_exact(tmp_path):
    root = tmp_path.resolve()
    grant = BashApprovalGrants({'id-1': ('printf ok', root)})
    assert not grant.consume('id-2', 'printf ok', root)
    assert not grant.consume('id-1', 'printf changed', root)
    assert not grant.consume('id-1', 'printf ok', root.parent)
    assert grant.consume('id-1', 'printf ok', root)
    assert not grant.consume('id-1', 'printf ok', root)


def test_cli_host_bash_requires_workspace_and_displays_full_command(
    monkeypatch, tmp_path, capsys,
):
    with pytest.raises(SystemExit) as missing:
        cli.main(['--config', 'synthetic.json', '--host-bash'])
    assert missing.value.code == 2

    command = 'printf "hello\\n" > result.txt'
    model = BashFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'bash', 'id': 'cli-bash',
                                           'args': {'command': command}}]),
        AIMessage(content='finished'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    inputs = iter(('run shell', ':approve', ':quit'))
    target = tmp_path / 'result.txt'

    def read_input(_prompt=''):
        line = next(inputs)
        if line == ':approve':
            assert not target.exists()
        return line

    monkeypatch.setattr('builtins.input', read_input)
    assert cli.main(['--config', 'synthetic.json', '--workspace', str(tmp_path),
                     '--host-bash']) == 0
    output = capsys.readouterr().out
    assert '当前用户可访问的文件和网络' in output
    assert '命令=' in output and 'printf' in output
    assert 'finished' in output
    assert target.read_text() == 'hello\n'


@pytest.mark.parametrize('args', [
    {'timeout': 1},
    {'command': ''},
    {'command': 'printf ok', 'timeout': '3'},
    {'command': 'printf ok', 'unexpected': True},
])
def test_invalid_bash_args_return_tool_error_without_approval(monkeypatch, tmp_path, args):
    model = BashFake(responses=[
        AIMessage(content='', tool_calls=[{'name': 'bash', 'id': 'bad-args', 'args': args}]),
        AIMessage(content='I will correct the arguments'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    app = application.build_local_application(
        Path('synthetic.json'), tools=[create_session_bash_tool()],
        system_prompt='', response_format=None,
    )
    session = app.create_workspace_session('A', tmp_path)

    result = app.run(session, [('user', 'run')])
    assert 'approval_requests' not in result
    message = bash_results(result)[0]
    assert message.tool_call_id == 'bad-args'
    assert message.status == 'error'
    assert 'Invalid bash arguments' in message.content
    assert result['messages'][-1].content == 'I will correct the arguments'


def test_invalid_and_valid_bash_in_one_batch_only_valid_needs_approval(
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
    app = application.build_local_application(
        Path('synthetic.json'), tools=[create_session_bash_tool()],
        system_prompt='', response_format=None,
    )
    session = app.create_workspace_session('A', tmp_path)

    pending = app.run(session, [('user', 'run both')])
    assert [item['tool_call_id'] for item in pending['approval_requests']] == ['good']
    assert not (tmp_path / 'result.txt').exists()
    result = app.resume_tool_approval(session, ['approve'])
    assert (tmp_path / 'result.txt').read_text() == 'ok'
    assert [(item.tool_call_id, item.status) for item in bash_results(result)] == [
        ('bad', 'error'), ('good', 'success'),
    ]


def test_long_bash_output_keeps_failure_summary_at_tail(monkeypatch, tmp_path):
    command = 'python -c "print(\'A\'*5000); print(\'FAILED 1 test\')"'
    app = make_app(monkeypatch, command)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert 'truncated=true' in message.content
    assert 'FAILED 1 test' in message.content
    assert 'A' * 5000 not in message.content


def test_bash_tail_does_not_split_utf8_character(monkeypatch, tmp_path):
    command = 'python -c "import sys; sys.stdout.buffer.write(b\'A\'*4095 + \'中\'.encode())"'
    app = make_app(monkeypatch, command)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert 'truncated=true' in message.content
    assert '中' in message.content
    assert '\ufffd' not in message.content


@pytest.mark.parametrize(('source', 'expected_status', 'summary'), [
    ('def test_case():\n    assert 1 == 1\n', 'success', 'passed'),
    ('def test_case():\n    assert 1 == 2\n', 'error', 'failed'),
])
def test_actual_pytest_result_reaches_model_tool_message(
    monkeypatch, tmp_path, source, expected_status, summary,
):
    (tmp_path / 'test_generated.py').write_text(source, encoding='utf-8')
    app = make_app(monkeypatch, 'python -m pytest -q test_generated.py')
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run generated test')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0]
    assert message.status == expected_status
    assert summary in message.content
    assert message.tool_call_id == 'bash-1'


def test_timeout_keeps_bounded_partial_output_for_manual_review(
    monkeypatch, tmp_path, caplog,
):
    app = make_app(monkeypatch, 'printf "EARLY_DIAGNOSTIC\\n"; sleep 3', timeout=1)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])

    with caplog.at_level(logging.INFO), pytest.raises(BashOutcomeUnknown) as failure:
        app.resume_tool_approval(session, ['approve'])
    assert 'EARLY_DIAGNOSTIC' in failure.value.partial_stdout
    assert failure.value.partial_stderr == ''
    assert 'EARLY_DIAGNOSTIC' not in str(failure.value)
    assert 'EARLY_DIAGNOSTIC' not in caplog.text
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'retry')])


def test_timeout_partial_output_respects_each_stream_budget(monkeypatch, tmp_path):
    command = ('python -c "import sys; '
               "sys.stdout.write('A'*5000+'OUT_TAIL'); sys.stdout.flush(); "
               "sys.stderr.write('B'*5000+'ERR_TAIL'); sys.stderr.flush()"
               '"; sleep 3')
    app = make_app(monkeypatch, command, timeout=1)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])

    with pytest.raises(BashOutcomeUnknown) as failure:
        app.resume_tool_approval(session, ['approve'])
    assert failure.value.output_truncated
    assert len(failure.value.partial_stdout.encode('utf-8')) <= 4096
    assert len(failure.value.partial_stderr.encode('utf-8')) <= 4096
    assert failure.value.partial_stdout.endswith('OUT_TAIL')
    assert failure.value.partial_stderr.endswith('ERR_TAIL')


def test_cli_escapes_partial_output_after_timeout(monkeypatch, tmp_path, capsys):
    app = make_app(monkeypatch, 'printf "EARLY\\033[2J\\n"; sleep 3', timeout=1)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])

    cli._resume_and_print(app, session, ['approve'])
    output = capsys.readouterr().out
    assert '工具副作用无法确认' in output
    assert 'EARLY' in output
    assert '\\u001b[2J' in output
    assert '\x1b' not in output
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'retry')])


def test_bash_model_schema_explains_timeout_and_output_limit():
    schema = create_session_bash_tool().tool_call_schema.model_json_schema()
    description = schema['description']
    assert '60' in description
    assert '300' in description
    assert '4096' in description
    assert 'workspace' in description


def _archive_path(message: str, stream: str) -> str:
    match = re.search(rf'{stream}_path=(naiyue-agent-output/[0-9a-f]{{32}}-{stream}\.log)',
                      message)
    assert match is not None, message
    return match.group(1)


def test_long_bash_output_is_saved_and_readable_inside_workspace(monkeypatch, tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    app = make_app(monkeypatch, "python -c \"print('A'*5000); print('END')\"")
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    path = _archive_path(message, 'stdout')
    assert 'archive_complete=true' in message
    assert 'truncated=true' in message
    assert (tmp_path / path).read_text().endswith('END\n')
    assert 'A' * 5000 in create_read_tool(tmp_path).invoke({'path': path})
    output_dir = tmp_path / 'naiyue-agent-output'
    assert output_dir.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / path).stat().st_mode & 0o777 == 0o600
    assert subprocess.run(['git', '-C', str(tmp_path), 'check-ignore', '-q', path],
                          check=False).returncode == 0


def test_short_bash_output_creates_no_artifacts(monkeypatch, tmp_path):
    app = make_app(monkeypatch, 'printf short')
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    assert 'stdout_path=' not in bash_results(result)[0].content
    assert not (tmp_path / 'naiyue-agent-output').exists()


def test_archive_preserves_both_streams_without_claiming_order(monkeypatch, tmp_path):
    command = ('python -c "import sys; '
               "sys.stdout.write('O'*5000); sys.stderr.write('E'*5000)"
               '"')
    app = make_app(monkeypatch, command)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    assert (tmp_path / _archive_path(message, 'stdout')).read_text() == 'O' * 5000
    assert (tmp_path / _archive_path(message, 'stderr')).read_text() == 'E' * 5000


def test_archive_quota_is_explicit_and_command_still_finishes(monkeypatch, tmp_path):
    monkeypatch.setenv('NAIYUE_BASH_OUTPUT_COMMAND_BYTES', '5000')
    monkeypatch.setenv('NAIYUE_BASH_OUTPUT_WORKSPACE_BYTES', '6000')
    app = make_app(monkeypatch, "python -c \"print('Q'*10000)\"")
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    assert 'exit_code=0' in message
    assert 'archive_complete=false' in message
    assert 'archive_reason=command_limit' in message
    assert 'Q' * 10000 not in message
    assert (tmp_path / _archive_path(message, 'stdout')).stat().st_size <= 5000


def test_existing_unowned_output_directory_is_not_modified(monkeypatch, tmp_path):
    output_dir = tmp_path / 'naiyue-agent-output'
    output_dir.mkdir()
    marker = output_dir / 'user-file.txt'
    marker.write_text('keep')
    app = make_app(monkeypatch, "python -c \"print('A'*5000)\"")
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    result = app.resume_tool_approval(session, ['approve'])
    message = bash_results(result)[0].content
    assert 'archive_complete=false' in message
    assert 'archive_reason=directory_unavailable' in message
    assert marker.read_text() == 'keep'
    assert sorted(item.name for item in output_dir.iterdir()) == ['user-file.txt']


def test_timeout_keeps_archive_path_for_human_review(monkeypatch, tmp_path):
    app = make_app(monkeypatch, "python -c \"print('T'*5000, flush=True)\"; sleep 3",
                   timeout=1)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    with pytest.raises(BashOutcomeUnknown) as failure:
        app.resume_tool_approval(session, ['approve'])
    path = failure.value.output_paths['stdout']
    assert (tmp_path / path).read_text().startswith('T' * 5000)
    assert failure.value.archive_complete is True
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'retry')])


def test_workspace_archive_limit_applies_across_commands(monkeypatch, tmp_path):
    monkeypatch.setenv('NAIYUE_BASH_OUTPUT_COMMAND_BYTES', '10000')
    monkeypatch.setenv('NAIYUE_BASH_OUTPUT_WORKSPACE_BYTES', '7000')
    paths = []
    for _ in range(2):
        app = make_app(monkeypatch, "python -c \"print('W'*5000)\"")
        session = app.create_workspace_session('A', tmp_path)
        app.run(session, [('user', 'run')])
        message = bash_results(app.resume_tool_approval(session, ['approve']))[0].content
        paths.append(tmp_path / _archive_path(message, 'stdout'))
    assert sum(path.stat().st_size for path in paths) == 7000
    assert 'archive_complete=false' in message
    assert 'archive_reason=workspace_limit' in message


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
    assert '当前运行尚未结束，不能清理 Bash 输出' in capsys.readouterr().out


def test_timeout_cli_shows_saved_output_path_without_resuming_model(
    monkeypatch, tmp_path, capsys,
):
    app = make_app(monkeypatch, "python -c \"print('T'*5000, flush=True)\"; sleep 3",
                   timeout=1)
    session = app.create_workspace_session('A', tmp_path)
    app.run(session, [('user', 'run')])
    cli._resume_and_print(app, session, ['approve'])
    output = capsys.readouterr().out
    assert '工具副作用无法确认' in output
    assert '已保存输出：stdout=naiyue-agent-output/' in output
    with pytest.raises(application.SessionManualReviewRequiredError):
        app.run(session, [('user', 'retry')])
