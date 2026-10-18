"""The terminal entry uses one local application for ordinary chat turns."""

from pathlib import Path
from threading import Event
from types import SimpleNamespace
import sys

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from naiyue_test_agent import application
from naiyue_test_agent.application import cli
from naiyue_test_agent.application.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.model_access import ModelCatalog, ModelConfigError


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[tuple[str, str]]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([(message.type, message.content) for message in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def feed(monkeypatch, *lines):
    entries = iter(lines)
    monkeypatch.setattr('builtins.input', lambda _prompt='': next(entries))


def test_cli_reuses_application_history_and_switches_model(monkeypatch, capsys):
    a = RecordingFake(responses=[AIMessage(content='A-one')])
    b = RecordingFake(responses=[AIMessage(content='B-two')])
    loaded = []

    def load(path):
        loaded.append(path)
        return ModelCatalog('A', {'A': a, 'B': b})

    monkeypatch.setattr(application, 'load_model_catalog', load)
    feed(monkeypatch, 'private-first', ':model B', 'private-second', ':quit')
    config = Path('synthetic.json')

    assert cli.main(['--no-session', '--config', str(config)]) == 0
    output = capsys.readouterr().out
    assert 'A-one' in output
    assert 'B-two' in output
    assert output.count('A-one') == 1
    assert output.count('B-two') == 1
    assert loaded == [config]
    assert a.seen[0][0][1].startswith(BASE_SYSTEM_PROMPT)
    assert 'Available tools:' in a.seen[0][0][1]
    assert b.seen == [[
        a.seen[0][0], ('human', 'private-first'),
        ('ai', 'A-one'), ('human', 'private-second'),
    ]]
    assert 'private-first' not in output


def test_cli_invalid_workspace_fails_before_model_call(monkeypatch, capsys, tmp_path):
    model = RecordingFake(responses=[AIMessage(content='unused')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))

    assert cli.main(['--no-session', '--config', 'synthetic.json', '--workspace',
                     str(tmp_path / 'missing')]) == 1
    assert '初始化失败' in capsys.readouterr().out
    assert model.seen == []


def test_cli_defaults_to_current_workspace_and_selects_default_tools(monkeypatch, capsys):
    tools_seen, choices = [], []

    class MinimalApp:
        default_model_id = 'A'

        def create_workspace_session(self, _model_id, root):
            assert root == Path.cwd()
            return 'session'

        def pending_approval_batches(self, _session_id):
            return []

        def run(self, _session_id, messages):
            return {'messages': [messages[0], AIMessage(content='answer')]}

    def build(_config, *, tools, system_prompt, response_format, initial_active_tools, settings):
        tools_seen.extend(tools)
        choices.append(initial_active_tools)
        return MinimalApp()

    monkeypatch.setattr(cli, 'build_local_application', build)
    feed(monkeypatch, 'question', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    assert 'answer' in capsys.readouterr().out
    assert [spec.tool.name for spec in tools_seen] == ['read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell', 'subagent']
    assert choices == [['read', 'ls', 'find', 'grep', 'write', 'edit', 'subagent']]


def test_cli_invalid_model_empty_line_and_unknown_command_do_not_call_model(monkeypatch, capsys):
    a = RecordingFake(responses=[AIMessage(content='A-one')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': a}))
    feed(monkeypatch, ':model', ':model missing', '  ', ':unknown', 'question', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert '请输入模型 ID' in output
    assert '模型 ID 不可用' in output
    assert '未知命令' in output
    assert 'A-one' in output
    assert len(a.seen) == 1


def test_cli_configuration_failure_does_not_print_exception_body(monkeypatch, capsys):
    def fail(_path):
        raise ModelConfigError('private-config-data')

    monkeypatch.setattr(application, 'load_model_catalog', fail)
    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 1
    output = capsys.readouterr().out
    assert '初始化失败' in output
    assert 'private-config-data' not in output


def test_cli_model_failure_is_safe_and_next_turn_can_run(monkeypatch, capsys, caplog):
    class FlakyFake(RecordingFake):
        attempts: int = 0

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError('private-model-data')
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = FlakyFake(responses=[AIMessage(content='recovered')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'first', 'second', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert '运行失败' in output
    assert 'recovered' in output
    assert 'private-model-data' not in output
    assert 'private-model-data' not in caplog.text
    assert model.attempts == 2


def test_cli_rejected_model_response_has_no_answer_and_next_turn_recovers(
    monkeypatch, capsys, caplog,
):
    model = RecordingFake(responses=[
        AIMessage(content='private-rejected-answer', response_metadata={'status': 'failed'}),
        AIMessage(content='recovered', response_metadata={'finish_reason': 'stop'}),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'first', 'second', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert '运行失败（ModelResponseRejected）' in output
    assert 'private-rejected-answer' not in output + caplog.text
    assert 'recovered' in output
    assert model.seen[1] == [
        model.seen[0][0], ('human', 'first'), ('human', 'second'),
    ]


def test_cli_eof_exits_without_a_model_call(monkeypatch):
    model = RecordingFake(responses=[AIMessage(content='unused')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))

    def end_of_input(_prompt=''):
        raise EOFError

    monkeypatch.setattr('builtins.input', end_of_input)
    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    assert model.seen == []


def test_cli_empty_final_text_has_no_fabricated_answer(monkeypatch, capsys):
    model = RecordingFake(responses=[AIMessage(content='')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, 'question', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    assert '本轮没有可展示的回答' in capsys.readouterr().out


def test_tty_queues_steering_and_follow_up_during_model_call(monkeypatch, capsys):
    entered, release, finished = Event(), Event(), Event()

    class BlockingModel(RecordingFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if not self.seen:
                entered.set()
                assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[
        AIMessage(content='first answer'), AIMessage(content='steered answer'),
        AIMessage(content='followed answer'),
    ])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    build = cli.build_local_application
    def factory(*args, **kwargs):
        app = build(*args, **kwargs)
        app.subscribe_events(lambda event: finished.set() if event.event_type == 'run_finished' else None)
        return app
    monkeypatch.setattr(cli, 'build_local_application', factory)
    entries = iter(['initial', 'private steering', ':follow private follow-up', ':quit'])

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == 'private steering':
            assert entered.wait(timeout=3)
        if line == ':quit':
            release.set()
            assert finished.wait(5)
        return line

    monkeypatch.setattr('builtins.input', terminal_input)

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert output.count('first answer') == 1
    assert output.count('steered answer') == 1
    assert output.count('followed answer') == 1
    assert 'private steering' not in output
    assert 'private follow-up' not in output
    assert [entry[-1] for entry in model.seen] == [
        ('human', 'initial'), ('human', 'private steering'),
        ('human', 'private follow-up'),
    ]


def test_tty_follow_without_text_does_not_enqueue(monkeypatch, capsys):
    model = RecordingFake(responses=[AIMessage(content='one')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    feed(monkeypatch, ':follow', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    assert '请输入 Follow-up 内容' in capsys.readouterr().out
    assert model.seen == []


def test_tty_stop_uses_application_signal_and_reports_completed_stop(monkeypatch, capsys):
    entered, release = Event(), Event()

    class BlockingModel(RecordingFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = BlockingModel(responses=[AIMessage(content='current answer'),
                                     AIMessage(content='must not run')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    entries = iter(['initial', 'queued steering', ':stop', ':quit'])

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == 'queued steering':
            assert entered.wait(timeout=3)
        if line == ':quit':
            release.set()
        return line

    monkeypatch.setattr('builtins.input', terminal_input)

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert 'Steering 已排队' in output
    assert '已请求停止，正在等待收尾' in output
    assert '已停止' in output
    assert output.count('current answer') == 1
    assert 'must not run' not in output
    assert len(model.seen) == 1
    assert 'queued steering' not in str(model.seen)


def test_tty_stop_request_does_not_mask_model_failure(monkeypatch, capsys):
    entered, release = Event(), Event()

    class FailingModel(RecordingFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            entered.set()
            assert release.wait(timeout=3)
            raise RuntimeError('private failure')

    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': FailingModel(responses=[])}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    entries = iter(['initial', ':stop', ':quit'])

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == ':stop':
            assert entered.wait(timeout=3)
        if line == ':quit':
            release.set()
        return line

    monkeypatch.setattr('builtins.input', terminal_input)

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert '已请求停止，正在等待收尾' in output
    assert '运行失败（RuntimeError）' in output
    assert '已停止' not in output
    assert 'private failure' not in output


def test_cli_stop_when_idle_does_not_start_a_run(monkeypatch, capsys):
    model = RecordingFake(responses=[AIMessage(content='unused')])
    monkeypatch.setattr(application, 'load_model_catalog',
                        lambda _path: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, ':stop', ':quit')

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    assert '当前没有运行' in capsys.readouterr().out
    assert model.seen == []


def test_tty_stop_rejection_is_not_reported_as_accepted(monkeypatch, capsys):
    entered, release = Event(), Event()

    class ClosingApp:
        default_model_id = 'A'

        def create_workspace_session(self, _model_id, root):
            assert root == Path.cwd()
            return 'session'

        def pending_approval_batches(self, _session_id):
            return []

        def run(self, _session_id, messages):
            entered.set()
            assert release.wait(timeout=3)
            return {'messages': [messages[0], AIMessage(content='answer')],
                    'run_stopped': False}

        def request_stop(self, _session_id):
            raise application.SessionBusyError('Window closed')

    monkeypatch.setattr(cli, 'build_local_application',
                        lambda *_args, **_kwargs: ClosingApp())
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    entries = iter(['initial', ':stop', ':quit'])

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == ':stop':
            assert entered.wait(timeout=3)
        if line == ':quit':
            release.set()
        return line

    monkeypatch.setattr('builtins.input', terminal_input)

    assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    output = capsys.readouterr().out
    assert '停止请求未被接受' in output
    assert '已请求停止，正在等待收尾' not in output
    assert '已停止' not in output
    assert 'answer' in output


def test_tty_input_after_acceptance_window_closes_starts_next_run(monkeypatch, capsys):
    entered, release, second_entered, second_release = (Event() for _ in range(4))
    workers = []
    original_thread = cli.Thread
    def track_worker(*args, **kwargs):
        worker = original_thread(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(cli, 'Thread', track_worker)

    class ClosingApp:
        default_model_id = 'A'

        def __init__(self):
            self.requests = []
            self.steer_attempts = self.stop_attempts = 0

        def create_workspace_session(self, _model_id, root):
            assert root == Path.cwd()
            return 'session'

        def pending_approval_batches(self, _session_id):
            return []

        def run(self, _session_id, messages):
            self.requests.append(messages[0].content)
            if len(self.requests) == 1:
                entered.set()
                assert release.wait(timeout=3)
            else:
                second_entered.set()
                assert second_release.wait(timeout=3)
            return {'messages': [messages[0], AIMessage(content=self.requests[-1] + ' answer')]}

        def steer(self, _session_id, _text):
            self.steer_attempts += 1
            release.set()
            raise application.SessionBusyError('Window already closed')

        def request_stop(self, _session_id):
            self.stop_attempts += 1
            second_release.set()
            workers[-1].join(timeout=5)
            assert not workers[-1].is_alive()
            raise application.SessionBusyError('Window already closed')

    app = ClosingApp()
    monkeypatch.setattr(cli, 'build_local_application', lambda *_args, **_kwargs: app)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    entries = iter(['first', 'second', ':quit'])

    def terminal_input(_prompt=''):
        line = next(entries)
        if line == 'second':
            assert entered.wait(timeout=3)
        if line == ':quit':
            assert second_entered.wait(timeout=3)
        return line

    monkeypatch.setattr('builtins.input', terminal_input)

    try:
        assert cli.main(['--no-session', '--config', 'synthetic.json']) == 0
    finally:
        release.set()
        second_release.set()
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive()
    output = capsys.readouterr().out
    assert app.steer_attempts == 1
    assert app.stop_attempts == 1
    assert app.requests == ['first', 'second']
    assert output.count('first answer') == 1
    assert output.count('second answer') == 1
