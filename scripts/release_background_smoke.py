"""Real background Shell acceptance, shared by source tests and installed smoke."""

import asyncio
import json
import os
import shlex
import sys
from unittest.mock import patch

if __package__:
    from .release_mcp_smoke import process_alive
else:
    from release_mcp_smoke import process_alive


def shell_command(script):
    if os.name == 'nt':
        return "& '" + sys.executable.replace("'", "''") + "' -u -c '" + script.replace("'", "''") + "'"
    return shlex.quote(sys.executable) + ' -u -c ' + shlex.quote(script)


async def wait_task(app, identity, predicate):
    async def poll():
        while True:
            row = await app.aget_background_task_output(identity)
            if predicate(row):
                return row
            await asyncio.sleep(.01)
    return await asyncio.wait_for(poll(), 15)


def tool_result(result, name):
    from langchain_core.messages import ToolMessage
    return next(message for message in reversed(result['messages'])
                if isinstance(message, ToolMessage) and message.name == name)


async def background_scenario(root, model_type, *, automatic=False, executable=None):
    from langchain_core.messages import AIMessage, SystemMessage
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools

    directory = root / ('background-auto' if automatic else 'background-explicit')
    directory.mkdir()
    shell = 'powershell' if os.name == 'nt' else 'bash'
    settings = AppSettings.model_validate({'permissions': {'mode': 'BypassApprovals'},
        'shell_tasks': {'foreground_wait_seconds': 1},
        'tools': {shell: {'executable': executable}}})
    tools = [spec for spec in create_builtin_tools(settings.tools, shell_task_settings=settings.shell_tasks)
             if spec.tool.name in (shell, 'task_list', 'task_output', 'task_stop')]
    assert {spec.tool.name for spec in tools} == {shell, 'task_list', 'task_output', 'task_stop'}
    def call(name, args, identity):
        return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': identity}])
    service = ("import os,json\nfrom http.server import HTTPServer,BaseHTTPRequestHandler\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200);self.end_headers();self.wfile.write(b'healthy')\n"
        " def log_message(self,*args): pass\n"
        "server=HTTPServer(('127.0.0.1',0),Handler)\n"
        "print(json.dumps({'pid':os.getpid(),'port':server.server_port}),flush=True)\nserver.serve_forever()")
    model = model_type(replies=[call(shell, {'command': shell_command(service), 'background': not automatic}, 'service'),
                               AIMessage(content='service submitted')])
    options = dict(database_path=directory / 'sessions.sqlite', tools=tools, system_prompt='',
                   response_format=None, settings=settings)
    with patch.object(application, 'load_model_catalog', lambda _: ModelCatalog('offline', {'offline': model})):
        async with open_async_persistent_application('unused', **options) as app:
            sid = await app.acreate_workspace_session('offline', directory)
            events = []
            unsubscribe = app.subscribe_events(events.append)
            try:
                result = await app.arun(sid, [('user', 'start service')])
                receipt = json.loads(tool_result(result, shell).content)
                identity = receipt['task_id']
                ready = await wait_task(app, identity, lambda row: bool(row['output']))
                address = json.loads(ready['output'].splitlines()[0])
                assert not app._sessions.require(sid).is_busy()
                health = ('import urllib.request;print(urllib.request.urlopen('
                    + repr('http://127.0.0.1:' + str(address['port'])) + ',timeout=5).read().decode())')
                model.replies.extend([call(shell, {'command': shell_command(health)}, 'health'), AIMessage(content='healthy')])
                result = await app.arun(sid, [('user', 'use service in another turn')])
                assert 'healthy' in tool_result(result, shell).content
                assert (await app.aget_background_task_output(identity))['status'] == 'running'
                model.replies.extend([call('task_stop', {'task_id': identity}, 'stop'), AIMessage(content='service stopped')])
                result = await app.arun(sid, [('user', 'stop service')])
                stopped = json.loads(tool_result(result, 'task_stop').content)
                assert stopped['termination'] == 'user_stop' and stopped['cleanup_confirmed']
                assert not process_alive(address['pid']), 'background service process survived stop'
                model.replies.extend([call(shell, {'command': shell_command("print('RESULT-background')"), 'background': True}, 'result'),
                                      AIMessage(content='result submitted')])
                result = await app.arun(sid, [('user', 'start result job')])
                job = json.loads(tool_result(result, shell).content)['task_id']
                completed = await wait_task(app, job, lambda row: row['status'] == 'finished')
                calls = len(model.seen)
                await asyncio.sleep(0)
                assert len(model.seen) == calls
                model.replies.extend([call('task_output', {'task_id': job}, 'output'), AIMessage(content='result checked')])
                result = await app.arun(sid, [('user', 'read result next turn')])
                system = '\n'.join(m.content for m in model.seen[calls] if isinstance(m, SystemMessage))
                assert job in system and 'RESULT-background' not in system
                assert 'RESULT-background' in json.loads(tool_result(result, 'task_output').content)['output']
                assert completed['exit_code'] == 0 and completed['cleanup_confirmed']
                returned = [m for m in result['messages'] if getattr(m, 'tool_call_id', None) == 'service']
                assert len(returned) == 1
                starts = [event for event in events if event.event_type == 'run_started']
                finishes = [event for event in events if event.event_type == 'run_finished']
                assert len(starts) == len(finishes) == 5
                paths = [directory / path for path in completed['archive_paths'].values()]
                before = [path.read_bytes() for path in paths]
            finally:
                unsubscribe()
        calls = len(model.seen)
        async with open_async_persistent_application('unused', **options) as reopened:
            assert (await reopened.alist_background_tasks())['items'] == []
            await reopened.arestore_session(sid)
            history = await reopened.aget_messages(sid)
            assert any('result checked' == m.content for m in history)
            assert len(model.seen) == calls
            assert paths and [path.read_bytes() for path in paths] == before
    return {'shell': shell, 'automatic': automatic, 'cross_turn_service': 'passed',
        'completion_and_output': 'passed', 'reopen_empty_registry': 'passed',
        'archives_preserved': 'passed', 'cleanup': 'passed', 'provider_requests': 0}
