"""Native graph/CLI integration keeps early replies and original error behavior."""

import os
import sys

from contextlib import closing
from pathlib import Path
from naiyue_test_agent.tools import create_session_bash_tool
from test_host_bash import BashFake, bash_results

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException
import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application import cli
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.application.cli_display import CliEventDisplay
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.tools.processes import ProcessCleanupError
from test_application_run_events import ToolModel


def test_cli_displays_first_reply_before_later_failure(monkeypatch, capsys):
    early, captured = [], []
    class Model(FakeMessagesListChatModel):
        calls: int = 0
        def bind_tools(self, tools, **kwargs):
            return self
        def _generate(self, messages, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                captured.append(capsys.readouterr().out)
                early.append('FIRST_VERIFIED_REPLY' in captured[-1])
                raise RuntimeError('PRIVATE_EXCEPTION_BODY')
            return super()._generate(messages, *args, **kwargs)

    @tool
    def probe() -> str:
        """Return synthetic information without side effects."""
        return 'ok'
    model = Model(responses=[AIMessage(content='FIRST_VERIFIED_REPLY', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'probe-1'},
    ])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    app = cli.build_local_application('unused', tools=[ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))], system_prompt='', response_format=None)
    monkeypatch.setattr(cli, 'build_local_application', lambda *_a, **_k: app)
    entries = iter(['question', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(entries))
    assert cli.main(['--no-session', '--config', 'unused']) == 0
    output = ''.join(captured) + capsys.readouterr().out
    assert early == [True]
    assert output.count('FIRST_VERIFIED_REPLY') == 1
    assert output.count('运行失败（RuntimeError）') == 1
    assert 'PRIVATE_EXCEPTION_BODY' not in output
    assert '本次运行完成' not in output


def test_cli_unsubscribes_after_quit_even_when_no_run(monkeypatch, capsys):
    lifecycle = []
    class App:
        default_model_id = 'A'
        def create_workspace_session(self, _model, root):
            return 'session'
        def pending_approval_batches(self, _session):
            return []
        def subscribe_events(self, handler):
            lifecycle.append('subscribe')
            return lambda: lifecycle.append('unsubscribe')
    monkeypatch.setattr(cli, 'build_local_application', lambda *_a, **_k: App())
    monkeypatch.setattr('builtins.input', lambda _p='': ':quit')
    assert cli.main(['--no-session', '--config', 'unused']) == 0
    assert lifecycle == ['subscribe', 'unsubscribe']


def test_startup_output_error_still_unsubscribes(monkeypatch):
    lifecycle = []
    class App:
        default_model_id = 'A'
        def create_workspace_session(self, _model, root):
            return 'session'
        def pending_approval_batches(self, _session):
            return []
        def subscribe_events(self, handler):
            lifecycle.append('subscribe')
            return lambda: lifecycle.append('unsubscribe')
    def broken_output(self, text):
        raise BrokenPipeError('PRIVATE_OUTPUT_DETAILS')
    monkeypatch.setattr(cli, 'build_local_application', lambda *_a, **_k: App())
    monkeypatch.setattr(cli.CliEventDisplay, 'write', broken_output)
    with pytest.raises(BrokenPipeError):
        cli.main(['--no-session', '--config', 'unused'])
    assert lifecycle == ['subscribe', 'unsubscribe']


def test_observer_output_failure_does_not_change_run_and_result_can_fill_gap(monkeypatch, cli_app, caplog):
    app = cli_app( ToolModel(responses=[AIMessage(content='visible answer', id='a')]))
    session = app.create_session('A')
    lines, failures = [], []
    def writer(text):
        if text == 'visible answer' and not failures:
            failures.append(True)
            raise OSError('PRIVATE_OUTPUT_ERROR')
        lines.append(text)
    view = CliEventDisplay(session, writer=writer)
    app.subscribe_events(view)
    result = app.run(session, [HumanMessage(content='q', id='h')])
    assert result['messages'][-1].content == 'visible answer'
    view.finish_result(result, input_message_id='h')
    assert lines.count('visible answer') == 1
    assert '未观察到终局事件' in '\n'.join(lines)
    assert 'PRIVATE_OUTPUT_ERROR' not in caplog.text


def test_recoverable_tool_error_displays_reason_and_agent_continues(monkeypatch, cli_app):
    @tool
    def retryable() -> str:
        """Return a safe actionable synthetic error."""
        raise ToolException('CORRECTABLE_REASON: use a different argument')
    app = cli_app( ToolModel(responses=[
        AIMessage(content='first reply', tool_calls=[{'name': 'retryable', 'args': {}, 'id': 't'}]),
        AIMessage(content='recovered reply'),
    ]), [ToolSpec(retryable, retryable.args_schema, permissions=ToolPermission(default='allow'))])
    session = app.create_session('A')
    lines = []
    view = CliEventDisplay(session, writer=lines.append)
    app.subscribe_events(view)
    cli._run_and_print(app, session, 'q', display=view)
    out = '\n'.join(lines)
    assert lines.count('first reply') == lines.count('recovered reply') == 1
    assert out.count('CORRECTABLE_REASON') == 1
    assert out.count('本次运行完成') == 1 and '运行失败' not in out


def test_approval_resume_then_new_approval_and_reject_do_not_replay_old_messages(monkeypatch, cli_app, tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    first, second = tmp_path / 'one.txt', tmp_path / 'two.txt'
    first.write_text('FIRST_PRIVATE_BODY')
    second.write_text('SECOND_PRIVATE_BODY')
    model = ToolModel(responses=[
        AIMessage(content='before approval', id='a', tool_calls=[
            {'name': 'read', 'args': {'path': str(first)}, 'id': 'first'},
        ]), AIMessage(content='before second approval', id='b', tool_calls=[
            {'name': 'read', 'args': {'path': str(second)}, 'id': 'second'},
        ]), AIMessage(content='final reply', id='c'),
    ])
    app = cli_app( model, [create_session_read_tool()])
    session = app.create_workspace_session('A', workspace)
    lines = []
    view = CliEventDisplay(session, writer=lines.append)
    app.subscribe_events(view)
    cli._run_and_print(app, session, 'q', display=view)
    cli._resume_and_print(app, session, ['approve'], display=view)
    cli._resume_and_print(app, session, ['reject'], display=view)
    out = '\n'.join(lines)
    assert lines.count('before approval') == lines.count('before second approval') == lines.count('final reply') == 1
    assert out.count('运行已暂停，等待审批') == 2
    assert out.count('本次运行完成') == 1
    assert out.count(str(first)) == out.count(str(second)) == 1
    assert 'FIRST_PRIVATE_BODY' not in out and 'SECOND_PRIVATE_BODY' not in out
    assert app.pending_tool_approvals(session) == []


def test_cleanup_failure_keeps_manual_review_once(monkeypatch, cli_app):
    @tool
    def uncertain() -> str:
        """Inject unknown side effects without executing a real command."""
        raise ProcessCleanupError('PRIVATE_EXCEPTION_BODY', facts={'process_reaped': False})
    app = cli_app( ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'uncertain', 'args': {}, 'id': 'u'},
    ])]), [ToolSpec(uncertain, uncertain.args_schema, permissions=ToolPermission(default='allow'))])
    session = app.create_session('A')
    lines = []
    view = CliEventDisplay(session, writer=lines.append)
    app.subscribe_events(view)
    cli._run_and_print(app, session, 'q', display=view)
    out = '\n'.join(lines)
    assert out.count('请先检查执行结果，勿直接重试') == 1
    assert out.count('工具 uncertain：副作用无法确认') == 1
    assert 'facts=' not in out
    assert 'PRIVATE_EXCEPTION_BODY' not in out and '本次运行完成' not in out


def test_returned_answer_can_fill_missing_message_events(monkeypatch, cli_app):
    app = cli_app( ToolModel(responses=[AIMessage(content='fallback answer')]))
    session = app.create_session('A')
    lines = []
    view = CliEventDisplay(session, writer=lines.append)
    app.subscribe_events(lambda e: view(e) if e.event_type not in ('message_delta', 'message_completed', 'message_revised') else None)
    cli._run_and_print(app, session, 'q', display=view)
    assert lines.count('fallback answer') == 1
    assert lines.count('本次运行完成') == 1
    assert '消息观察不完整' in '\n'.join(lines)


@pytest.mark.parametrize('termination', ['eof', 'exception'])
def test_cli_unsubscribes_on_input_termination(monkeypatch, termination):
    lifecycle = []
    class App:
        default_model_id = 'A'
        def create_workspace_session(self, _model, root):
            return 'session'
        def pending_approval_batches(self, _session):
            return []
        def subscribe_events(self, handler):
            return lambda: lifecycle.append('unsubscribe')
    def end_input(_p=''):
        raise EOFError if termination == 'eof' else RuntimeError('PRIVATE_INPUT')
    monkeypatch.setattr(cli, 'build_local_application', lambda *_a, **_k: App())
    monkeypatch.setattr('builtins.input', end_input)
    if termination == 'eof':
        assert cli.main(['--no-session', '--config', 'unused']) == 0
    else:
        with pytest.raises(RuntimeError):
            cli.main(['--no-session', '--config', 'unused'])
    assert lifecycle == ['unsubscribe']


@pytest.fixture
def cli_app(monkeypatch):
    opened = []
    def create(model, tools=None):
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = cli.build_local_application('synthetic', tools=tools or [], system_prompt='', response_format=None)
        opened.append(app)
        return app
    try:
        yield create
    finally:
        for app in reversed(opened):
            app.close()


def test_cli_backend_tool_selection_and_approval_displays_full_command(
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
    original_build = cli.build_persistent_application
    def build_selected(*args, **kwargs):
        app = original_build(*args, **kwargs)
        create = app.create_workspace_session
        def create_selected(model_id, root):
            sid = create(model_id, root)
            app.set_active_tools(sid, [*app.get_active_tools(sid), 'bash'])
            return sid
        app.create_workspace_session = create_selected
        return app
    monkeypatch.setattr(cli, 'build_persistent_application', build_selected)
    inputs = iter(('run shell', ':approve', ':quit'))
    target = tmp_path / 'result.txt'

    def read_input(_prompt=''):
        line = next(inputs)
        if line == ':approve':
            assert not target.exists()
        return line

    monkeypatch.setattr('builtins.input', read_input)
    assert cli.main(['--config', 'synthetic.json', '--workspace', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '命令=' in output and 'printf' in output
    assert 'finished' in output
    assert target.read_text() == 'hello\n'


def test_timeout_returns_raw_output_to_model_without_cli_control_sequences(monkeypatch, tmp_path, capsys):
    with closing(make_cli_app(monkeypatch, 'printf "EARLY\\033[2J\\n"; sleep 3', timeout=1)) as app:
        session = app.create_workspace_session('A', tmp_path)
        app.run(session, [('user', 'run')])

        results = []
        resume = app.resume_tool_approval
        def capture(*args, **kwargs):
            result = resume(*args, **kwargs)
            results.append(result)
            return result
        monkeypatch.setattr(app, 'resume_tool_approval', capture)
        cli._resume_and_print(app, session, ['approve'])
        output = capsys.readouterr().out
        assert 'done' in output
        assert '工具副作用无法确认' not in output
        assert '\x1b' not in output
        assert 'output:\nEARLY\n' in bash_results(results[0])[0].content
        assert '\x1b' not in bash_results(results[0])[0].content
        assert not app._app._sessions.require(session).describe_session()['manual_review_required']


def test_timeout_cli_resumes_model_and_archive_remains_in_tool_result(
    monkeypatch, tmp_path, capsys,
):
    with closing(make_cli_app(monkeypatch, "python -c \"print('T'*70000, flush=True)\"; sleep 3",
                   timeout=1)) as app:
        session = app.create_workspace_session('A', tmp_path)
        app.run(session, [('user', 'run')])
        cli._resume_and_print(app, session, ['approve'])
        output = capsys.readouterr().out
        assert 'done' in output and '工具副作用无法确认' not in output
        assert not app._app._sessions.require(session).describe_session()['manual_review_required']


def make_cli_app(monkeypatch, command: str, *, timeout: int | None = None, settings=None, output_settings=None):
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
    return cli.build_local_application(
        Path('synthetic.json'), tools=[create_session_bash_tool(settings=settings, output_settings=output_settings)],
        system_prompt='', response_format=None,
    )
