"""Terminal scope controls and interactive exit settle the actual async run."""

import asyncio
import sys
from threading import Event, Thread
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_subagent_execution import ChildModel, role
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition


def test_interactive_quit_stops_running_child_before_joining_worker(monkeypatch, capsys):
    entered, finished = Event(), Event()
    apps, events, outcomes = [], [], []
    release = None
    child = ChildModel(replies=[AIMessage(content='late report')])
    async def wait_for_stop(messages):
        nonlocal release
        release = asyncio.Event()
        entered.set()
        await release.wait()
    child.on_request = wait_for_stop
    root = StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'outer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    monkeypatch.setattr(cli, 'load_settings', lambda: AppSettings(subagents=(role(),)))
    monkeypatch.setattr(cli, 'create_builtin_tools', lambda _, *, process_settings, shell_task_settings, progress_settings, network_settings, retry_settings: [create_subagent_tool(), definition()])
    build = cli.build_local_application
    def factory(*args, **kwargs):
        app = build(*args, **kwargs)
        apps.append(app)
        app.subscribe_events(events.append)
        return app
    monkeypatch.setattr(cli, 'build_local_application', factory)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter(['task', ':quit'])
    def read(_prompt=''):
        line = next(lines)
        if line == ':quit': assert entered.wait(5)
        return line
    monkeypatch.setattr('builtins.input', read)
    def execute():
        try: outcomes.append(cli.main(['--no-session', '--config', 'unused']))
        finally: finished.set()
    terminal = Thread(target=execute)
    terminal.start()
    try:
        assert finished.wait(5), 'Interactive quit must request stop before joining'
        assert outcomes == [0] and apps[0]._closed
        assert [e.data.status for e in events if e.event_type == 'run_finished'] == ['stopped', 'stopped']
        assert 'late report' not in capsys.readouterr().out
    finally:
        if not finished.is_set() and release is not None:
            bridge = apps[0]
            bridge._loop.call_soon_threadsafe(release.set)
        terminal.join(5)
