"""The existing terminal input driver talks to the native async application."""

import asyncio
from threading import Event, Thread
from types import SimpleNamespace
import sys
import pytest

from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel


def test_cli_native_model_stop_keeps_new_input_for_explicit_resubmit(monkeypatch, capsys):
    entered = Event()

    class WaitingModel(StreamingToolModel):
        async def _next(self, messages):
            self.seen.append(messages)
            if len(self.seen) == 1:
                entered.set()
                await asyncio.Event().wait()
            return AIMessage(content='unexpected automatic run')

    model = WaitingModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter(['start', ':stop', 'next request', ':quit'])

    def read(_prompt=''):
        line = next(lines)
        if line == ':stop':
            assert entered.wait(5)
        return line

    monkeypatch.setattr('builtins.input', read)
    assert cli.main(['--no-session', '--config', 'unused']) == 0
    output = capsys.readouterr().out
    assert '已停止' in output and '待发送' in output
    assert 'next request' in output
    assert len(model.seen) == 1


@pytest.mark.parametrize('blocked', [False, True])
def test_explicit_resubmit_clears_only_admitted_pending_input(monkeypatch, capsys, blocked):
    entered = Event()

    class WaitingModel(StreamingToolModel):
        async def _next(self, messages):
            self.seen.append(messages)
            if len(self.seen) == 1:
                entered.set()
                await asyncio.Event().wait()
            return AIMessage(content='new answer')

    model, workers, apps = WaitingModel(replies=[]), [], []
    original_factory = cli.build_local_application

    def build(*args, **kwargs):
        app = original_factory(*args, **kwargs)
        apps.append(app)
        return app

    def worker(*args, **kwargs):
        instance = Thread(*args, **kwargs)
        workers.append(instance)
        return instance

    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(cli, 'build_local_application', build)
    monkeypatch.setattr(cli, 'Thread', worker)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter(['start', 'queued request', ':stop', 'next request', ':send', ':pending', ':quit'])

    def read(_prompt=''):
        line = next(lines)
        if line == 'queued request':
            assert entered.wait(5)
        if line == ':send':
            workers[0].join(5)
            assert not workers[0].is_alive()
            if blocked:
                bridge = apps[0]
                def require_review():
                    sessions = bridge._app.sessions._sessions
                    sid = sessions.loaded_session_ids()[0]
                    owner, observation_run = sessions._admit(sid, 'review-fault', 'message')
                    run_id, observation = observation_run.run_id, observation_run.observation
                    owner.mark_manual_review(observation_run)
                    owner._cleanup_run(observation_run)
                bridge._submit(require_review)
        if line == ':pending':
            workers[-1].join(5)
            assert not workers[-1].is_alive()
        return line

    monkeypatch.setattr('builtins.input', read)
    assert cli.main(['--no-session', '--config', 'unused']) == 0
    output = capsys.readouterr().out
    if blocked:
        assert len(model.seen) == 1 and '待发送内容仍保留' in output
        assert '待发送内容：\nqueued request\n\nnext request' in output
    else:
        assert len(model.seen) == 2
        assert model.seen[-1][-1].content == 'queued request\n\nnext request'
        assert 'new answer' in output and '没有待发送内容' in output
