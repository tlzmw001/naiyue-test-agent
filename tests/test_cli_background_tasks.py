"""The terminal queries the shared service while its root run remains busy."""
from terminal_protocol_stubs import protocol_stub

import asyncio
from threading import Thread

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from terminal_service_helpers import terminal_runtime
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel
from test_background_tasks import submit, wait_status


def test_cli_background_bridge_during_root_run(monkeypatch, tmp_path):
    from naiyue_test_agent.cli.cli_background import handle_background_command
    model = StreamingToolModel(replies=[AIMessage(content='done')], entered=asyncio.Event(), release=asyncio.Event())
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    output, results = [], []
    with terminal_runtime(tmp_path, root=tmp_path) as (client, probe):
        sid = client.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
        manager = probe.host.sessions._sessions.background_tasks
        row = probe.inspect(lambda _: submit(manager, tmp_path, "import time;print('CLI_TAIL',flush=True);time.sleep(60)", sid=sid))
        probe.inspect(lambda _: wait_status(manager, row['task_id'], lambda value: bool(value['output'])))
        worker = Thread(target=lambda: results.append(client.run(sid, 'work')))
        worker.start()
        try:
            probe.inspect(lambda _: asyncio.wait_for(model.entered.wait(), 5))
            handle_background_command(client, ':tasks', output.append)
            handle_background_command(client, f':task output {row["task_id"]}', output.append)
            handle_background_command(client, f':task stop {row["task_id"]}', output.append)
            assert worker.is_alive() and results == []
            assert sid in '\n'.join(output) and 'CLI_TAIL' in '\n'.join(output)
            assert client.request('GET', f'/background-tasks/{row["task_id"]}/output')['cleanup_confirmed']
            assert client.request('GET', '/background-tasks')['active_count'] == 0
            assert len(model.seen) == 1
        finally:
            probe.loop.call_soon_threadsafe(model.release.set)
            worker.join(5)
        assert not worker.is_alive() and len(results) == 1


@pytest.mark.parametrize('line', [':task', ':task stop bad', ':task output ../private', ':tasks a b', ':task invalid ' + 'a' * 32])
def test_cli_background_invalid_never_calls_application(line):
    from naiyue_test_agent.cli.cli_background import handle_background_command
    messages = []
    handle_background_command(object(), line, messages.append)
    assert '用法' in '\n'.join(messages)


def test_cli_background_failure_is_observable_and_private(caplog):
    from naiyue_test_agent.cli.cli_background import handle_background_command
    class Broken:
        def request(self, *args, **kwargs):
            raise OSError('PRIVATE_CREDENTIAL')
    messages = []
    handle_background_command(Broken(), ':tasks', messages.append)
    assert '未确认' in '\n'.join(messages) and 'cli_background_task_failed' in caplog.text
    assert 'PRIVATE_CREDENTIAL' not in '\n'.join(messages) + caplog.text


def test_cli_main_dispatches_commands_without_model(monkeypatch, tmp_path, capsys):
    model = StreamingToolModel(replies=[])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    entries = iter([':tasks', ':task output ' + 'a' * 32, ':quit'])
    monkeypatch.setattr('builtins.input', lambda _: next(entries))
    assert cli.main(['--no-session', '--workspace', str(tmp_path), '--config', 'unused']) == 0
    text = capsys.readouterr().out
    assert '没有后台任务' in text and '任务不存在' in text and model.seen == []


def test_cli_pagination_and_unconfirmed_stop_preserve_terminal_safety():
    from naiyue_test_agent.cli.cli_background import handle_background_command
    from types import SimpleNamespace
    identity = 'a' * 32
    row = dict(task_id=identity, session_id='s', status='lost', shell='bash', command_preview='bad\x1b[2J',
        exit_code=None, termination='cleanup_unconfirmed', cleanup_confirmed=False)
    app = protocol_stub(tasks=SimpleNamespace(list=lambda **kw: dict(items=[row], active_count=0, next_before=identity), stop=lambda _: row))
    messages = []
    handle_background_command(app, ':tasks', messages.append)
    handle_background_command(app, ':task stop ' + identity, messages.append)
    assert f'下一页：:tasks {identity}' in '\n'.join(messages)
    assert '进程清理尚未确认' in '\n'.join(messages) and '\x1b' not in '\n'.join(messages)


def test_cli_memory_output_reports_result_without_process_or_archive_details():
    from naiyue_test_agent.cli.cli_background import handle_background_command
    from types import SimpleNamespace
    identity = 'c' * 32
    row = dict(task_id=identity, session_id='s', kind='memory', status='finished', shell='memory',
        command_preview='更新概览', result_status='partial', termination='completed', cleanup_confirmed=True,
        output='<script>summary</script>\x1b[2J', truncated=False, output_available=True,
        archive_paths={}, exit_code=None)
    app = protocol_stub(tasks=SimpleNamespace(output=lambda _: row))
    messages = []
    handle_background_command(app, ':task output ' + identity, messages.append)
    text = '\n'.join(messages)
    assert '记忆整理' in text and '部分完成' in text and '<script>summary</script>' in text
    assert '退出码' not in text and '归档引用' not in text and '\x1b' not in text


def test_cli_memory_unconfirmed_stop_does_not_call_it_process_cleanup():
    from naiyue_test_agent.cli.cli_background import handle_background_command
    from types import SimpleNamespace
    identity = 'd' * 32
    row = dict(task_id=identity, session_id='s', kind='memory', status='lost', shell='memory',
        command_preview='更新概览', result_status='stopped', termination='user_stop', cleanup_confirmed=False)
    messages = []
    handle_background_command(protocol_stub(tasks=SimpleNamespace(stop=lambda _: row)), ':task stop ' + identity, messages.append)
    assert '记忆整理收尾尚未确认' in '\n'.join(messages) and '进程' not in '\n'.join(messages)


def test_background_transport_error_is_not_reported_as_bad_pagination(caplog):
    from naiyue_test_agent.cli.cli_background import handle_background_command
    from naiyue_test_agent.service.settings import ServiceError
    class Broken:
        def request(self, *args, **kwargs): raise ServiceError('service_connection_failed')
    output = []
    handle_background_command(Broken(), ':task stop ' + 'a' * 32, output.append)
    assert '未确认' in '\n'.join(output) and '分页' not in '\n'.join(output)
    assert 'cli_background_task_failed' in caplog.text and 'service_connection_failed' in caplog.text
