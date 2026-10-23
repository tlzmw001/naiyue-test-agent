"""Application archives survive losing the directory in which a process runs."""

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.workspaces import prepare_workspace
from test_async_application import StreamingToolModel, call
from test_background_tasks import wait_status
from test_background_tools import command, tool_result


def test_running_background_task_keeps_project_archive_after_execution_root_disappears(monkeypatch, tmp_path):
    root, data = tmp_path / 'execution', tmp_path / 'data'
    root.mkdir()
    location = prepare_workspace(root, data_dir=data)
    # The network handshake releases the second write only after the directory moves.
    script = ("import socket,time\n"
        "server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1)\n"
        "print(server.getsockname()[1],flush=True)\n"
        "connection,_=server.accept();connection.recv(1);connection.close();server.close()\n"
        "print('after-directory-disappeared',flush=True)\n"
        "time.sleep(60)\n")
    model = StreamingToolModel(replies=[call('bash', {'command': command(script), 'background': True}),
                                       AIMessage(content='started')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'storage': {'data_dir': data},
        'permissions': {'mode': 'BypassApprovals'}})
    tools = [spec for spec in create_builtin_tools(settings.tools, shell_task_settings=settings.shell_tasks)
             if spec.tool.name == 'bash']

    async def scenario():
        async with open_async_persistent_application('unused', database_path=location.database_path,
                workspace_binding={'workspace_id': location.workspace_id, 'root_path': location.root},
                tools=tools, system_prompt='', response_format=None, settings=settings) as app:
            sid = await app.sessions.create_workspace('A', root)
            result = await app.runs.start(sid, [('user', 'start')])
            receipt = tool_result(result, 'bash')
            assert receipt.status == 'success'
            identity = json.loads(receipt.content)['task_id']
            manager = app.sessions._sessions.background_tasks
            ready = await wait_status(manager, identity, lambda row: bool(row['output']))
            port = int(ready['output'].strip())
            root.rename(tmp_path / 'moved-execution')
            assert not root.exists()
            _reader, writer = await asyncio.open_connection('127.0.0.1', port)
            try:
                writer.write(b'1')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
            later = await wait_status(manager, identity,
                lambda row: 'after-directory-disappeared' in row['output'])
            rows = await app.tasks.list(sid, workspace_root=root)
            stopped = await app.tasks.stop(identity, sid, workspace_root=root)
            saved = await app.tasks.output(identity, sid, workspace_root=root)
            assert [row['task_id'] for row in rows['items']] == [identity]
            assert later['status'] == 'running' and later['output_available']
            assert stopped['termination'] == 'user_stop' and stopped['cleanup_confirmed']
            assert saved['archive_complete'] and saved['output_available']
            assert saved['archive_saved_bytes'] > ready['archive_saved_bytes']
            expected = data / 'workspaces' / location.workspace_id / 'artifacts' / 'naiyue-agent-output'
            assert saved['archive_paths']
            for value in saved['archive_paths'].values():
                path = Path(value)
                assert path.is_absolute() and path.parent == expected
                assert b'after-directory-disappeared' in path.read_bytes()
            assert len(model.seen) == 2

    asyncio.run(scenario())


def test_archive_cleanup_rejects_active_task_with_noncanonical_data_dir(monkeypatch, tmp_path):
    from naiyue_test_agent.application.async_application import build_async_local_application
    root, data = tmp_path / 'execution', tmp_path / 'data'
    root.mkdir()
    (tmp_path / 'alias').mkdir()
    model = StreamingToolModel(replies=[call('bash', {
        'command': command("import time;print('ready',flush=True);time.sleep(60)"),
        'background': True}), AIMessage(content='started')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'storage': {'data_dir': tmp_path / 'alias' / '..' / 'data'},
        'permissions': {'mode': 'BypassApprovals'}})
    tools = [spec for spec in create_builtin_tools(settings.tools, shell_task_settings=settings.shell_tasks)
             if spec.tool.name == 'bash']

    async def scenario():
        app = await build_async_local_application('unused', tools=tools, settings=settings,
                                                 system_prompt='', response_format=None)
        try:
            sid = await app.sessions.create_workspace('A', root)
            result = await app.runs.start(sid, [('user', 'start')])
            identity = json.loads(tool_result(result, 'bash').content)['task_id']
            await wait_status(app.sessions._sessions.background_tasks, identity, lambda row: bool(row['output']))
            assert not app.sessions._sessions.require(sid).is_busy()
            with pytest.raises(ValueError, match='active'):
                await app.tasks.preview_outputs(root)
            await app.tasks.stop(identity)
            plan = await app.tasks.preview_outputs(root)
            assert plan.root == data / 'sessions' / sid / 'artifacts' and plan.file_count == 1
        finally:
            await app.aclose()

    asyncio.run(scenario())


def test_ephemeral_session_archives_without_project_identity_or_project_tools(monkeypatch, tmp_path):
    from naiyue_test_agent.application.async_application import build_async_local_application
    root, data = tmp_path / 'execution', tmp_path / 'data'
    root.mkdir()
    model = StreamingToolModel(replies=[
        call('bash', {'command': command("print('x' * 1000)")}),
        call('history', {'action': 'list_sessions'}), call('memory', {'action': 'list'}), AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'storage': {'data_dir': data},
        'permissions': {'mode': 'BypassApprovals'}, 'tools': {'bash': {'max_output_bytes': 32}}})
    tools = [spec for spec in create_builtin_tools(settings.tools, shell_task_settings=settings.shell_tasks)
             if spec.tool.name in ('bash', 'history', 'memory')]

    async def scenario():
        app = await build_async_local_application('unused', tools=tools, system_prompt='',
                                                 response_format=None, settings=settings)
        try:
            sid = await app.sessions.create_workspace('A', root)
            result = await app.runs.start(sid, [('user', 'run')])
            assert tool_result(result, 'bash').status == 'success'
            for name in ('history', 'memory'):
                item = tool_result(result, name)
                assert item.status == 'error' and name + '_unavailable' in item.content
            plan = await app.tasks.preview_outputs(root)
            assert plan.root == data / 'sessions' / sid / 'artifacts'
            assert plan.file_count == 1 and plan.total_bytes == 1001
            assert not (root / '.naiyue').exists()
            assert not list(data.rglob('*.sqlite'))
        finally:
            await app.aclose()

    asyncio.run(scenario())
