"""Web removal closes idle hosts while retaining user files and project history."""
import asyncio
import pytest
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel
from test_multi_project_runtime import projects
from test_service_protocol import command, service_clients


@pytest.mark.parametrize('missing', [False, True])
def test_web_removes_open_idle_project_and_keeps_data(monkeypatch, tmp_path, missing):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, app, _):
            (project, host), _ = await projects(app.state.application, tmp_path)
            sid = await host.sessions.create('A')
            base = f'/api/v1/scopes/{project.project_id}'
            assert (await a.get(base + '/sessions')).status_code == 200
            root = tmp_path / 'a'
            if missing: root.rmdir()
            else: (root / 'keep.txt').write_text('keep')
            headers = command()
            result = await a.delete(f'/api/v1/projects/{project.project_id}', headers=headers)
            assert result.status_code == 200, result.text
            assert result.json() == {'project_id': project.project_id, 'data_retained': True}
            assert (await b.delete(f'/api/v1/projects/{project.project_id}', headers=headers)).json() == result.json()
            assert project.project_id not in app.state.scopes.services
            assert project.project_id not in [p['project_id'] for p in (await b.get('/api/v1/projects')).json()['items']]
            assert (await b.get(base + '/sessions')).status_code == 404
            from naiyue_test_agent.storage.project_store import ProjectStore
            with ProjectStore(project.database_path) as store:
                assert store.sessions.get(sid)['session_id'] == sid
            if not missing: assert (root / 'keep.txt').read_text() == 'keep'
    asyncio.run(scenario())


def test_web_removal_does_not_stop_running_task(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='answer')], entered=entered, release=release)
        async with service_clients(monkeypatch, tmp_path, model) as (a, _, app, _):
            (project, host), _ = await projects(app.state.application, tmp_path)
            sid = await host.sessions.create('A')
            run = asyncio.create_task(host.runs.start(sid, [('user', 'continue')]))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                result = await a.delete(f'/api/v1/projects/{project.project_id}', headers=command())
                assert result.json()['error']['code'] == 'project_in_use'
                assert (await app.state.application.projects.get(project.project_id)).lifecycle == 'active'
                assert not run.done()
            finally: release.set()
            await run
    asyncio.run(scenario())


def test_web_removal_blocks_reopen_and_releases_gate(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, b, app, _):
            (project, _), _ = await projects(app.state.application, tmp_path)
            await app.state.scopes.get(project.project_id)
            entered, release = asyncio.Event(), asyncio.Event()
            original = app.state.application.projects.close
            async def close(identity):
                entered.set()
                await release.wait()
                return await original(identity)
            monkeypatch.setattr(app.state.application.projects, 'close', close)
            task = asyncio.create_task(a.delete(f'/api/v1/projects/{project.project_id}', headers=command()))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                blocked = await b.get(f'/api/v1/scopes/{project.project_id}/sessions')
                assert blocked.json()['error']['code'] == 'project_in_use'
                assert (await b.get('/api/v1/scopes/chats/sessions')).status_code == 200
            finally: release.set()
            assert (await task).status_code == 200
            assert project.project_id not in app.state.scopes._removing
    asyncio.run(scenario())


def test_explicit_reconcile_can_resume_only_requested_removal(tmp_path):
    from test_project_registry import service
    root = tmp_path / 'source'; root.mkdir()
    with service(tmp_path) as api:
        project = api.add_local(root, trusted=True, operation_id='add')
        api.registry.reserve('remove', 'remove', {'project_id': project.project_id}, project,
            lifecycle='deleting', facts={'database_path': str(project.database_path)})
        assert api.reconcile('remove') == ({'operation_id': 'remove', 'error': None},)
        assert api.list() == () and project.database_path.exists() and root.is_dir()


def test_pending_removal_stays_visible_and_can_be_reconciled(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from naiyue_test_agent.application.projects import operations
    from naiyue_test_agent.contracts.projects import ProjectError
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            (project, _), _ = await projects(app.state.application, tmp_path)
            original = operations.deletion_guard
            @contextmanager
            def unavailable(project):
                raise ProjectError('project_in_use')
                yield
            monkeypatch.setattr(operations, 'deletion_guard', unavailable)
            headers = command()
            response = await a.delete(f'/api/v1/projects/{project.project_id}', headers=headers)
            assert response.json()['error']['code'] == 'project_in_use'
            catalog = (await a.get('/api/v1/projects')).json()['items']
            assert next(row for row in catalog if row['project_id'] == project.project_id)['lifecycle'] == 'deleting'
            monkeypatch.setattr(operations, 'deletion_guard', original)
            result = await a.post('/api/v1/projects/reconcile', headers=command(), json={'operation_id': headers['X-Request-ID']})
            assert result.json()['items'] == [{'operation_id': headers['X-Request-ID'], 'error': None}]
            assert project.database_path.is_file()
    asyncio.run(scenario())


def test_cloud_binding_blocks_web_removal_and_retains_registration(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    from uuid import uuid4
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            root = tmp_path / 'cloud'; root.mkdir()
            project = await app.state.application.projects.add_local(root, trusted=True, operation_id='add')
            with ProjectStore(project.database_path) as store:
                sid = 's'; store.sessions.create(sid, dict(thread_id='t', model_id='A', workspace_root=str(root), active_tool_names=[], permission_mode='Default'))
                source = store.cloud_tasks.add_source(sid, project.project_id, dict(input_id='a'*64, directory='/private/cache'))
                store.cloud_tasks.reserve(project.project_id, dict(command_id=uuid4().hex, local_session_id=sid,
                    profile_id='work', source_handle=source['source_handle'], template_id='test'),
                    dict(instance_id=uuid4().hex, project_name='code'))
            base = f'/api/v1/scopes/{project.project_id}/sessions'
            assert (await a.get(base)).status_code == 200
            result = await a.delete(f'/api/v1/projects/{project.project_id}', headers=command())
            assert result.json()['error']['code'] == 'project_cloud_tasks_retained'
            assert (await app.state.application.projects.get(project.project_id)).lifecycle == 'active'
            assert project.project_id not in app.state.scopes._removing
            assert (await a.get(base)).status_code == 200
    asyncio.run(scenario())


def test_close_failure_after_resources_close_does_not_leave_dead_service(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            (project, _), _ = await projects(app.state.application, tmp_path)
            old = await app.state.scopes.get(project.project_id)
            original = old.application._lifetime._resources.aclose
            async def fail():
                await original()
                raise OSError('fixture_cleanup_failure')
            monkeypatch.setattr(old.application._lifetime._resources, 'aclose', fail)
            result = await a.delete(f'/api/v1/projects/{project.project_id}', headers=command())
            assert result.status_code == 500
            assert (await app.state.application.projects.get(project.project_id)).lifecycle == 'active'
            assert project.project_id not in app.state.scopes.services
            reopened = await app.state.scopes.get(project.project_id)
            assert reopened is not old and not reopened.application._lifetime._closed
            assert (await a.get(f'/api/v1/scopes/{project.project_id}/sessions')).status_code == 200
    asyncio.run(scenario())


@pytest.mark.parametrize('identity', ['chats', 'temp-not-a-project', 'bad-id'])
def test_remove_rejects_non_project_scope_before_closing_delivery(monkeypatch, tmp_path, identity):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            ordinary = await app.state.scopes.get('chats')
            # A cached foreign scope must not be interpreted as a project ID.
            app.state.scopes.services[identity] = ordinary
            try:
                result = await a.delete(f'/api/v1/projects/{identity}', headers=command())
                assert result.status_code == 404
                assert app.state.scopes.services[identity] is ordinary
                assert not ordinary.delivery._closing
            finally:
                if identity != 'chats': app.state.scopes.services.pop(identity, None)
    asyncio.run(scenario())
