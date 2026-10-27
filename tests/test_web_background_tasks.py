"""Real application registry behind the authenticated HTTP boundary."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_background_tasks import submit, wait_status
from test_web_api import web_client, request


def test_web_background_scope_paging_auth_and_stop(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path) as (client, web):
            app = web.state.service.application
            manager = app.sessions._sessions.background_tasks
            first = await app.sessions.create_workspace('A', tmp_path)
            second = await app.sessions.create_workspace('A', tmp_path)
            foreign = tmp_path / 'foreign'
            foreign.mkdir()
            rows = []
            for i, (sid, root) in enumerate(((first, tmp_path), (second, tmp_path), ('foreign', foreign))):
                row = await submit(manager, root, "import time;print('READY',flush=True);time.sleep(60)", call=str(i), sid=sid)
                await wait_status(manager, row['task_id'], lambda value: bool(value['output']))
                rows.append(row)
            endpoint = '/api/v1/scopes/chats/background-tasks'
            assert (await client.get(endpoint, headers={'Authorization': ''})).status_code == 401
            assert (await client.get(endpoint, headers={'Host': 'evil.example'})).status_code == 403
            page = (await client.get(endpoint, params={'limit': 1})).json()
            assert page['active_count'] == 2 and page['items'][0]['session_id'] == second
            assert page['revision'] and page['terminal_revision'] == 0
            assert (await client.get(endpoint, params={'limit': 1})).json()['revision'] == page['revision']
            older = (await client.get(endpoint, params={'before': page['next_before'], 'limit': 1})).json()
            assert older['items'][0]['task_id'] == rows[0]['task_id'] and older['active_count'] == 2
            assert older['next_before'] is None
            scoped = (await client.get(endpoint, params={'session_id': first})).json()
            assert len(scoped['items']) == 1
            for suffix, method in (('/output', client.get), ('/stop', client.post)):
                denied = await method(f'{endpoint}/{rows[2]["task_id"]}{suffix}')
                assert denied.status_code == 404 and denied.json()['error']['code'] == 'task_not_found'
                denied = await method(f'{endpoint}/{rows[0]["task_id"]}{suffix}?session_id={second}')
                assert denied.status_code == 404
            stopped_url = f'{endpoint}/{rows[0]["task_id"]}/stop'
            assert (await client.post(stopped_url, headers={'Origin': 'https://evil.example'})).status_code == 403
            output = (await client.get(f'{endpoint}/{rows[0]["task_id"]}/output')).json()
            assert 'READY' in output['output'] and output['output_available']
            stopped = await client.post(stopped_url)
            assert stopped.status_code == 200 and stopped.json()['cleanup_confirmed']
            assert stopped.json()['termination'] == 'user_stop'
            assert (await client.post(stopped_url)).json() == stopped.json()
            after = (await client.get(endpoint)).json()
            assert after['active_count'] == 1 and after['terminal_revision'] > 0 and after['revision'] != page['revision']
            assert model.seen == []
            # A Web observer never consumes the next-model notification.
            assert manager.notice_snapshot(session_id=first, workspace_root=tmp_path).versions
    asyncio.run(scenario())


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'before=bad', 'session_id='])
def test_web_background_invalid_query(monkeypatch, tmp_path, query):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=tmp_path) as (client, _):
            response = await client.get('/api/v1/scopes/chats/background-tasks?' + query)
            assert response.status_code == 422
    asyncio.run(scenario())


def test_web_background_unbound_and_unknown_cursor(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, web):
            row = await submit(web.state.service.application.sessions._sessions.background_tasks, tmp_path, "print('done')")
            assert (await client.get('/api/v1/scopes/chats/background-tasks')).json()['items'] == []
            assert (await client.get(f'/api/v1/scopes/chats/background-tasks/{row["task_id"]}/output')).status_code == 404
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=tmp_path) as (client, _):
            response = await client.get('/api/v1/scopes/chats/background-tasks?before=' + 'a' * 32)
            assert response.status_code == 422 and response.json()['error']['code'] == 'task_cursor_invalid'
    asyncio.run(scenario())


def test_web_background_missing_output_and_failure_projection(monkeypatch, tmp_path, caplog):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=tmp_path) as (client, web):
            app = web.state.service.application
            manager = app.sessions._sessions.background_tasks
            row = await submit(manager, tmp_path, "print('TAIL')")
            detail = await wait_status(manager, row['task_id'], lambda value: value['status'] == 'finished')
            for path in detail['archive_paths'].values():
                (tmp_path / path).unlink()
            endpoint = f'/api/v1/scopes/chats/background-tasks/{row["task_id"]}'
            result = (await client.get(endpoint + '/output')).json()
            assert result['output_available'] is False and 'TAIL' in result['output']
            async def broken(*args, **kwargs):
                raise OSError('PRIVATE_PATH_OR_TOKEN')
            monkeypatch.setattr(app.tasks, 'output', broken)
            failure = await client.get(endpoint + '/output')
            assert failure.status_code == 503 and failure.json()['error']['code'] == 'background_tasks_unavailable'
            assert 'PRIVATE_PATH_OR_TOKEN' not in failure.text + caplog.text
            async def uncertain(*args, **kwargs):
                return {**detail, 'status': 'lost', 'cleanup_confirmed': False}
            monkeypatch.setattr(app.tasks, 'stop', uncertain)
            failure = await client.post(endpoint + '/stop')
            assert failure.status_code == 409 and failure.json()['error']['code'] == 'task_cleanup_unconfirmed'
    asyncio.run(scenario())


def test_web_background_management_during_root_run(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='done')], entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path) as (client, web):
            app = web.state.service.application
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            row = await submit(app.sessions._sessions.background_tasks, tmp_path, 'import time;time.sleep(60)', sid=sid)
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('work'))).json()['run_id']
            await asyncio.wait_for(entered.wait(), 5)
            assert (await client.get('/api/v1/scopes/chats/background-tasks')).json()['active_count'] == 1
            stopped = await client.post(f'/api/v1/scopes/chats/background-tasks/{row["task_id"]}/stop')
            assert stopped.json()['cleanup_confirmed']
            assert (await client.get(f'/api/v1/scopes/chats/runs/{run}')).json()['status'] == 'running'
            assert 'bash' not in app.sessions.active_tools(sid)
            release.set()
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_web_background_frame_budget_preserves_utf8_tail_and_registry(monkeypatch, tmp_path):
    from naiyue_test_agent.web.serialization import encoded_size
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=tmp_path,
                              max_frame_bytes=2048) as (client, web):
            manager = web.state.service.application.sessions._sessions.background_tasks
            row = await submit(manager, tmp_path, "print('中\\n'*800)")
            original = await wait_status(manager, row['task_id'], lambda value: value['status'] == 'finished')
            response = await client.get(f'/api/v1/scopes/chats/background-tasks/{row["task_id"]}/output')
            assert response.status_code == 200
            value = response.json()
            assert value['truncated'] and encoded_size(value) <= 2048 and value['output']
            assert original['output'].endswith(value['output'])
            assert '\ufffd' not in value['output'] and value['output_available']
            assert (await manager.output(row['task_id']))['output'] == original['output']
            web.state.service.settings = web.state.service.settings.model_copy(update={'max_frame_bytes': 128})
            response = await client.get('/api/v1/scopes/chats/background-tasks')
            assert response.status_code == 409 and response.json()['error']['code'] == 'view_too_large'
    asyncio.run(scenario())
