"""Real Agent/tool/checkpoint wiring for explicitly backgrounded Shell commands."""

import asyncio
from contextlib import asynccontextmanager
import json
import shlex
import sys

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools
from test_async_application import StreamingToolModel, call
from test_background_tasks import wait_status


def command(script):
    return shlex.quote(sys.executable) + ' -u -c ' + shlex.quote(script)


@asynccontextmanager
async def background_app(monkeypatch, tmp_path, model, *, persistent=False, mode='BypassApprovals', archive_bytes=67108864, max_seconds=0, wait_seconds=120, notice_items=20, notice_bytes=8192):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'permissions': {'mode': mode},
        'shell_tasks': {'max_background_archive_bytes': archive_bytes, 'max_execution_seconds': max_seconds,
                        'foreground_wait_seconds': wait_seconds,
                        'max_notice_items': notice_items, 'max_notice_bytes': notice_bytes}})
    specs = [spec for spec in create_builtin_tools(settings.tools, shell_task_settings=settings.shell_tasks)
             if spec.tool.name in ('bash', 'task_list', 'task_output', 'task_stop')]
    options = dict(tools=specs, system_prompt='', response_format=None, settings=settings)
    if persistent:
        async with open_async_persistent_application('unused', database_path=tmp_path / 'sessions.sqlite', **options) as app:
            yield app
    else:
        app = await build_async_local_application('unused', **options)
        try:
            yield app
        finally:
            await app.aclose()


def tool_result(result, name):
    return next(item for item in reversed(result['messages']) if isinstance(item, ToolMessage) and item.name == name)


@pytest.mark.parametrize('persistent', [False, True])
def test_real_service_survives_final_reply_and_next_round_can_query(monkeypatch, tmp_path, persistent):
    script = ("from http.server import HTTPServer,BaseHTTPRequestHandler\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200);self.end_headers();self.wfile.write(b'healthy')\n"
        " def log_message(self,*args): pass\n"
        "server=HTTPServer(('127.0.0.1',0),Handler)\n"
        "print(server.server_port,flush=True)\nserver.serve_forever()")
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command(script), 'background': True}), AIMessage(content='started')])
        async with background_app(monkeypatch, tmp_path, model, persistent=persistent) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user', 'start service')])
            message = tool_result(result, 'bash')
            assert message.status == 'success'
            receipt = json.loads(message.content)
            assert receipt['status'] in ('starting', 'running', 'finished')
            assert result['messages'][-1].content == 'started'
            assert not app.sessions._sessions.require(sid).is_busy()
            ready = await wait_status(app.sessions._sessions.background_tasks, receipt['task_id'], lambda row: bool(row['output']))
            port = int(ready['output'])
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            try:
                writer.write(b'GET / HTTP/1.0\r\nHost: localhost\r\n\r\n')
                await writer.drain()
                body = await reader.read()
                assert b'200 OK' in body and body.endswith(b'healthy')
            finally:
                writer.close()
                await writer.wait_closed()
            model.replies.extend([call('task_list', {}, 'list'),
                call('task_output', {'task_id': receipt['task_id']}, 'output'), AIMessage(content='still running')])
            second = await app.runs.start(sid, [('user', 'query service')])
            assert json.loads(tool_result(second, 'task_list').content)['items'][0]['task_id'] == receipt['task_id']
            assert json.loads(tool_result(second, 'task_output').content)['output'] == ready['output']
            count = len(model.seen)
            stopped = await app.tasks.stop(receipt['task_id'], sid)
            assert stopped['termination'] == 'user_stop' and stopped['cleanup_confirmed']
            assert len(model.seen) == count  # Completion never starts another model request.
            assert len([m for m in second['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 't']) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_background_starts_only_after_exact_command_approval(monkeypatch, tmp_path, decision):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command("print('approved')"), 'background': True}), AIMessage(content='done')])
        async with background_app(monkeypatch, tmp_path, model, mode='Default') as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            pending = await app.runs.start(sid, [('user', 'run')])
            assert pending['approval_requests'][0]['target'].startswith('[background] ')
            assert (await app.tasks.list())['items'] == []
            result = await app.runs.resume_approval(sid, [decision])
            message = tool_result(result, 'bash')
            if decision == 'reject':
                assert message.status == 'error' and (await app.tasks.list())['items'] == []
            else:
                receipt = json.loads(message.content)
                done = await wait_status(app.sessions._sessions.background_tasks, receipt['task_id'], lambda row: row['status'] == 'finished')
                assert done['exit_code'] == 0 and 'approved' in done['output']
    asyncio.run(scenario())


def test_remembered_foreground_command_does_not_authorize_background(tmp_path):
    from naiyue_test_agent.tools.permissions import execution_environment, remembered_rule, matches_session_allow
    subject = {'tool_name': 'bash', 'operation': 'execute', 'workspace': str(tmp_path),
        'execution_environment': execution_environment(), 'args': {'command': 'same', 'background': False}}
    rule = remembered_rule(subject)
    assert matches_session_allow([rule], subject)
    background = {**subject, 'args': {'command': 'same', 'background': True}}
    assert not matches_session_allow([rule], background)


def test_stop_tool_approval_and_cross_session_query_are_enforced(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command('import time;time.sleep(60)'), 'background': True}), AIMessage(content='started')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            first = await app.runs.start(sid, [('user', 'start')])
            identity = json.loads(tool_result(first, 'bash').content)['task_id']
            other = await app.sessions.create_workspace('A', tmp_path)
            model.replies.extend([call('task_output', {'task_id': identity}), AIMessage(content='blocked')])
            result = await app.runs.start(other, [('user', 'query foreign task')])
            assert tool_result(result, 'task_output').status == 'error'
            assert 'scope' in tool_result(result, 'task_output').content
            await app.sessions.select_permission_mode(sid, 'Default')
            model.replies.extend([call('task_stop', {'task_id': identity}), AIMessage(content='stopped')])
            pending = await app.runs.start(sid, [('user', 'stop')])
            assert pending['approval_requests'][0]['tool_name'] == 'task_stop'
            result = await app.runs.resume_approval(sid, ['approve'])
            stopped = json.loads(tool_result(result, 'task_stop').content)
            assert stopped['status'] == 'finished' and stopped['cleanup_confirmed']
    asyncio.run(scenario())


@pytest.mark.parametrize('limit', ['time', 'output'])
def test_real_shell_uses_background_execution_and_log_settings(monkeypatch, tmp_path, limit):
    async def scenario():
        script = "import os,time;os.write(1,b'A'*1000);time.sleep(60)"
        model = StreamingToolModel(replies=[call('bash', {'command': command(script), 'background': True, 'timeout': 600}), AIMessage(content='submitted')])
        async with background_app(monkeypatch, tmp_path, model, archive_bytes=100 if limit == 'output' else 67108864,
                                  max_seconds=1 if limit == 'time' else 0) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user', 'run')])
            identity = json.loads(tool_result(result, 'bash').content)['task_id']
            done = await wait_status(app.sessions._sessions.background_tasks, identity, lambda r: r['status'] == 'finished')
            assert done['termination'] == ('timeout' if limit == 'time' else 'output_limit')
            assert done['max_execution_seconds'] == (1 if limit == 'time' else 600)
            assert done['cleanup_confirmed']
            if limit == 'output':
                assert done['archive_saved_bytes'] == 100
    asyncio.run(scenario())


def test_application_close_reaps_a_task_after_root_run_has_finished(monkeypatch, tmp_path):
    import os
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command('import os,time;print(os.getpid(),flush=True);time.sleep(60)'), 'background': True}), AIMessage(content='submitted')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user', 'run')])
            identity = json.loads(tool_result(result, 'bash').content)['task_id']
            manager = app.sessions._sessions.background_tasks
            ready = await wait_status(manager, identity, lambda r: bool(r['output']))
            pid = int(ready['output'])
        row = await manager.output(identity)
        assert row['termination'] == 'app_close' and row['cleanup_confirmed']
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(scenario())


def test_stopping_a_later_root_run_does_not_stop_existing_background(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command("import time;print('ready',flush=True);time.sleep(60)"), 'background': True}), AIMessage(content='submitted')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user', 'run')])
            identity = json.loads(tool_result(result, 'bash').content)['task_id']
            await wait_status(app.sessions._sessions.background_tasks, identity, lambda r: bool(r['output']))
            entered, release = asyncio.Event(), asyncio.Event()
            original = StreamingToolModel._next
            async def gated(self, messages):
                if self is model:
                    entered.set()
                    await release.wait()
                return await original(self, messages)
            monkeypatch.setattr(StreamingToolModel, '_next', gated)
            model.replies.append(AIMessage(content='finished'))
            task = asyncio.create_task(app.runs.start(sid, [('user', 'another task')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                app.runs.stop(sid)
                release.set()
                await asyncio.wait_for(task, 5)
                assert (await app.tasks.output(identity))['status'] == 'running'
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
