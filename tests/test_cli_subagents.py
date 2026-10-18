"""CLI input remains available while a child waits inside its parent's call."""

from threading import Event
from threading import Thread
from types import SimpleNamespace
import sys

import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.application import cli
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_subagent_execution import ChildModel, role
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition


@pytest.mark.parametrize('exit_kind', ['approve', 'eof', 'quit'])
def test_pipe_child_approval_and_exit_never_join_an_unresolved_child(monkeypatch, capsys, exit_kind):
    requested = Event()
    effects, events, apps = [], [], []
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'outer'), AIMessage(content='root done')])
    child = ChildModel(replies=[call('probe', {'value': 'once'}, 'inner'), AIMessage(content='child report')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    settings = AppSettings(subagents=(role(),))
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(cli, 'create_builtin_tools', lambda _, *, process_settings, output_settings, progress_settings: [create_subagent_tool(), definition(calls=effects, approved=True)])
    build = cli.build_local_application
    def factory(*args, **kwargs):
        app = build(*args, **kwargs)
        apps.append(app)
        def receive(event):
            events.append(event)
            if event.source and event.event_type == 'approval_requested': requested.set()
        app.subscribe_events(receive)
        return app
    monkeypatch.setattr(cli, 'build_local_application', factory)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    entries = iter(['task', ':approve', ':quit'] if exit_kind == 'approve' else ['task', ':quit'] if exit_kind == 'quit' else ['task'])
    def read(_prompt=''):
        try: line = next(entries)
        except StopIteration: raise EOFError from None
        if line != 'task': assert requested.wait(5)
        return line
    monkeypatch.setattr('builtins.input', read)
    assert cli.main(['--no-session', '--config', 'unused']) == 0
    assert requested.is_set()
    output = capsys.readouterr().out
    assert 'root/tester/' in output and 'probe' in output
    assert 'child report' not in output
    assert len({event.run_id for event in events}) == 1
    if exit_kind == 'approve':
        assert effects == [('probe', 'once')] and 'root done' in output
    else:
        assert effects == [] and len(child.seen) == len(root.seen) == 1
        assert '已停止' in output
    assert all(app._closed for app in apps)


def test_pipe_approval_waits_for_preceding_queued_request(monkeypatch, capsys):
    entered, release = Event(), Event()
    effects = []
    class FirstModel(StreamingToolModel):
        async def _next(self, messages):
            if not self.seen:
                entered.set()
                import asyncio
                await asyncio.to_thread(release.wait)
            return await super()._next(messages)
    root = FirstModel(replies=[AIMessage(content='first done'),
        call('subagent', {'agent': 'tester', 'task': 'second task'}, 'outer'), AIMessage(content='second done')])
    child = ChildModel(replies=[call('probe', {'value': 'second'}, 'inner'), AIMessage(content='child report')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    monkeypatch.setattr(cli, 'load_settings', lambda: AppSettings(subagents=(role(),)))
    monkeypatch.setattr(cli, 'create_builtin_tools', lambda _, *, process_settings, output_settings, progress_settings: [create_subagent_tool(), definition(calls=effects, approved=True)])
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    lines = iter(['first', 'second', ':approve', ':quit'])
    def read(_prompt=''):
        line = next(lines)
        if line == 'second': assert entered.wait(5)
        if line == ':approve': release.set()
        return line
    monkeypatch.setattr('builtins.input', read)
    try:
        assert cli.main(['--no-session', '--config', 'unused']) == 0
        assert effects == [('probe', 'second')] and len(root.seen) == 3 and len(child.seen) == 2
        output = capsys.readouterr().out
        assert '当前没有待审批的工具调用' not in output and 'second done' in output
    finally:
        release.set()


def test_pipe_does_not_reselect_submitted_root_batch_before_resume_starts(monkeypatch, capsys):
    entered, release, resuming = Event(), Event(), Event()
    effects = []
    class FirstModel(StreamingToolModel):
        async def _next(self, messages):
            if not self.seen:
                entered.set()
                import asyncio
                await asyncio.to_thread(release.wait)
            return await super()._next(messages)
    root = FirstModel(replies=[call('probe', {'value': 'first'}, 'first-tool'), AIMessage(content='first done'),
        call('subagent', {'agent': 'tester', 'task': 'second'}, 'outer'), AIMessage(content='second done')])
    child = ChildModel(replies=[call('probe', {'value': 'second'}, 'inner'), AIMessage(content='report')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    monkeypatch.setattr(cli, 'load_settings', lambda: AppSettings(subagents=(role(),)))
    monkeypatch.setattr(cli, 'create_builtin_tools', lambda _, *, process_settings, output_settings, progress_settings: [create_subagent_tool(), definition(calls=effects, approved=True)])
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    resume = cli._resume_and_print
    def delayed(*args, **kwargs):
        resuming.set()
        release.clear()
        assert release.wait(5)
        return resume(*args, **kwargs)
    monkeypatch.setattr(cli, '_resume_and_print', delayed)
    class OrderedThread(Thread):
        def join(self, timeout=None):
            if resuming.is_set(): release.set()
            return super().join(timeout)
    monkeypatch.setattr(cli, 'Thread', OrderedThread)
    lines = iter(['first', 'second', ':approve', ':approve', ':quit'])
    approvals = 0
    def read(_prompt=''):
        nonlocal approvals
        line = next(lines)
        if line == 'second': assert entered.wait(5)
        if line == ':approve':
            approvals += 1
            if approvals == 1: release.set()
            else: assert resuming.wait(5)
        return line
    monkeypatch.setattr('builtins.input', read)
    try:
        assert cli.main(['--no-session', '--config', 'unused']) == 0
        assert effects == [('probe', 'first'), ('probe', 'second')]
        assert len(root.seen) == 4 and len(child.seen) == 2
        assert '需要为 1 个调用分别给出决定' not in capsys.readouterr().out
    finally:
        release.set()
