"""Slow scope assembly must not serialize unrelated projects or sidebars."""
import asyncio
from uuid import uuid4

import pytest

from naiyue_test_agent.application.projects import host_manager
from naiyue_test_agent.service.scopes import ScopeServices
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.web.settings import WebSettings
from test_async_application import StreamingToolModel
from test_multi_project_runtime import multi_app


async def register(app, tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    return await app.projects.add_local(root, trusted=True, operation_id=str(uuid4()))


def gate_assembly(monkeypatch, project_id):
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = host_manager.assemble_host
    calls = []
    async def assemble(**options):
        project = options.get('registered_project')
        if project is not None and project.project_id == project_id:
            calls.append(project_id)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return await original(**options)
    monkeypatch.setattr(host_manager, 'assemble_host', assemble)
    return entered, release, cancelled, calls


def test_slow_project_does_not_block_other_hosts_lists_or_close(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a, b = await register(app, tmp_path, 'a'), await register(app, tmp_path, 'b')
            entered, release, _, calls = gate_assembly(monkeypatch, a.project_id)
            opening = asyncio.create_task(app.projects.open(a.project_id))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert await asyncio.wait_for(app.projects.list_sessions(b.project_id), 2) == []
                host = await asyncio.wait_for(app.projects.open(b.project_id), 2)
                assert await asyncio.wait_for(app.projects.list_sessions(b.project_id), 2) == []
                assert await asyncio.wait_for(app.chats.list_sessions(), 2) == []
                await asyncio.wait_for(app.chats.open(), 2)
                await asyncio.wait_for(app.temporary.open('temp-other'), 2)
                await asyncio.wait_for(app.temporary.close('temp-other'), 2)
                await asyncio.wait_for(app.projects.close(b.project_id), 2)
                assert host._lifetime._closed
                assert not opening.done() and calls == [a.project_id]
            finally:
                release.set()
                await asyncio.gather(opening, return_exceptions=True)
    asyncio.run(scenario())


def test_same_scope_shares_assembly_after_first_waiter_cancels(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a = await register(app, tmp_path, 'a')
            entered, release, cancelled, calls = gate_assembly(monkeypatch, a.project_id)
            first = asyncio.create_task(app.projects.open(a.project_id))
            await asyncio.wait_for(entered.wait(), 5)
            second = asyncio.create_task(app.projects.open(a.project_id))
            await asyncio.sleep(0)  # allow the second request to join the shared task
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            try:
                release.set()
                host = await asyncio.wait_for(second, 5)
                assert calls == [a.project_id] and not cancelled.is_set()
                assert await app.projects.open(a.project_id) is host
            finally:
                release.set()
                await asyncio.gather(first, second, return_exceptions=True)
    asyncio.run(scenario())


def test_service_scopes_open_independently_and_share_delivery(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a, b = await register(app, tmp_path, 'a'), await register(app, tmp_path, 'b')
            settings = WebSettings(database_path=tmp_path / 'unused.sqlite', frontend_dist=tmp_path,
                                   runtime_settings=AppSettings())
            scopes = ScopeServices(app, settings, settings_path=tmp_path / 'unused.toml')
            entered, release, _, calls = gate_assembly(monkeypatch, a.project_id)
            first = asyncio.create_task(scopes.get(a.project_id))
            duplicate = asyncio.create_task(scopes.get(a.project_id))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                other = await asyncio.wait_for(scopes.get(b.project_id), 2)
                assert other is scopes.services[b.project_id]
                await asyncio.wait_for(scopes.close_project(b.project_id), 2)
                assert b.project_id not in scopes.services
                release.set()
                left, right = await asyncio.gather(first, duplicate)
                assert left is right and calls == [a.project_id]
            finally:
                release.set()
                await asyncio.gather(first, duplicate, return_exceptions=True)
                await scopes.drain()
    asyncio.run(scenario())


def test_root_shutdown_cancels_pending_host_without_late_publication(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a = await register(app, tmp_path, 'a')
            entered, release, cancelled, _ = gate_assembly(monkeypatch, a.project_id)
            opening = asyncio.create_task(app.projects.open(a.project_id))
            closing = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                closing = asyncio.create_task(app.aclose())
                await asyncio.wait_for(cancelled.wait(), 2)
                await asyncio.wait_for(asyncio.shield(closing), 5)
                assert cancelled.is_set() and not release.is_set()
                assert app._lifetime._children == []
                with pytest.raises(RuntimeError, match='owning event loop'):
                    await opening
            finally:
                release.set()
                await asyncio.gather(opening, return_exceptions=True)
                if closing is not None:
                    await closing
    asyncio.run(scenario())


def test_failed_assembly_releases_store_and_next_open_retries(monkeypatch, tmp_path):
    from naiyue_test_agent.mcp_client import tools as mcp_tools
    from naiyue_test_agent.storage.project_store import ProjectStore
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a = await register(app, tmp_path, 'a')
            primary = OSError('private MCP failure')
            entered, release = asyncio.Event(), asyncio.Event()
            original = mcp_tools.register_mcp_tools
            async def fail(*args, **kwargs):
                entered.set()
                await release.wait()
                raise primary
            monkeypatch.setattr(mcp_tools, 'register_mcp_tools', fail)
            first = asyncio.create_task(app.projects.open(a.project_id))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                second = asyncio.create_task(app.projects.open(a.project_id))
                # Complete a loop round so the second request joins the pending open.
                await asyncio.sleep(0)
                release.set()
                results = await asyncio.gather(first, second, return_exceptions=True)
                assert results == [primary, primary]
                with ProjectStore(a.database_path, require_existing=True, runtime_profile=a.project_id):
                    pass
                monkeypatch.setattr(mcp_tools, 'register_mcp_tools', original)
                host = await app.projects.open(a.project_id)
                assert not host._lifetime._closed
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)
    asyncio.run(scenario())


def test_shutdown_during_mcp_discovery_releases_already_opened_database(monkeypatch, tmp_path):
    from naiyue_test_agent.mcp_client import tools as mcp_tools
    from naiyue_test_agent.storage.project_store import ProjectStore
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a = await register(app, tmp_path, 'a')
            entered = asyncio.Event()
            async def discover(*args, **kwargs):
                entered.set()
                await asyncio.Event().wait()
            monkeypatch.setattr(mcp_tools, 'register_mcp_tools', discover)
            opening = asyncio.create_task(app.projects.open(a.project_id))
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(app.aclose(), 5)
            with pytest.raises(RuntimeError, match='owning event loop'):
                await opening
            with ProjectStore(a.database_path, require_existing=True, runtime_profile=a.project_id):
                pass
            assert not app._lifetime._opening_tasks and not app._lifetime._children
    asyncio.run(scenario())


def test_close_waits_for_same_project_open_without_blocking_other_projects(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a, b = await register(app, tmp_path, 'a'), await register(app, tmp_path, 'b')
            entered, release, _, _ = gate_assembly(monkeypatch, a.project_id)
            opening = asyncio.create_task(app.projects.open(a.project_id))
            closing = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                closing = asyncio.create_task(app.projects.close(a.project_id))
                await asyncio.wait_for(app.projects.list_sessions(b.project_id), 2)
                assert not closing.done()
                release.set()
                host = await opening
                await closing
                assert host._lifetime._closed
                assert await app.projects.open(a.project_id) is not host
            finally:
                release.set()
                await asyncio.gather(*(t for t in (opening, closing) if t is not None), return_exceptions=True)
    asyncio.run(scenario())


def test_http_sidebar_and_other_scope_respond_while_project_is_opening(monkeypatch, tmp_path):
    from test_service_protocol import service_clients
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (left, right, server, _):
            app = server.state.application
            a, b = await register(app, tmp_path, 'a'), await register(app, tmp_path, 'b')
            entered, release, _, _ = gate_assembly(monkeypatch, a.project_id)
            opening = asyncio.create_task(left.get(f'/api/v1/scopes/{a.project_id}/models'))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                for route in (f'/api/v1/navigation/{b.project_id}/sessions', '/api/v1/scopes/chats/models'):
                    response = await asyncio.wait_for(right.get(route), 2)
                    assert response.status_code == 200, response.text
                assert not opening.done()
            finally:
                release.set()
                response = await opening
                assert response.status_code == 200
    asyncio.run(scenario())


def test_new_request_during_abandoned_open_cleanup_waits_then_rebuilds(monkeypatch, tmp_path):
    from naiyue_test_agent.mcp_client import tools as mcp_tools
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            a = await register(app, tmp_path, 'a')
            entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            original = mcp_tools.register_mcp_tools
            calls = []
            async def discover(*args, **kwargs):
                calls.append(True)
                if len(calls) == 1:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaning.set()
                        await release.wait()
                return await original(*args, **kwargs)
            monkeypatch.setattr(mcp_tools, 'register_mcp_tools', discover)
            first = asyncio.create_task(app.projects.open(a.project_id))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                first.cancel()
                await asyncio.wait_for(cleaning.wait(), 5)
                second = asyncio.create_task(app.projects.open(a.project_id))
                await asyncio.sleep(0)  # join while the first task is still cleaning up
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await first
                host = await asyncio.wait_for(second, 5)
                assert len(calls) == 2 and not host._lifetime._closed
            finally:
                release.set()
                await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
    asyncio.run(scenario())


def test_same_legacy_directory_selection_serializes_before_project_id_is_known(monkeypatch, tmp_path):
    from threading import Event
    from naiyue_test_agent.application.projects import selection
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.workspaces import prepare_workspace
    root = tmp_path / 'old-code'
    root.mkdir()
    location = prepare_workspace(root, data_dir=tmp_path / 'data')
    with ProjectStore(location.database_path) as store:
        store.sessions.bind_workspace(location.workspace_id, root)
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            selector = selection.ProjectSelection(app.projects)
            entered, release = asyncio.Event(), Event()
            loop = asyncio.get_running_loop()
            def hold_binding(*args, **kwargs):
                store = ProjectStore(*args, **kwargs)
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(10)
                return store
            monkeypatch.setattr(selection, 'ProjectStore', hold_binding)
            first = asyncio.create_task(selector.prepare(root=root, trusted=True, operation_id='first'))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 5)
                second = asyncio.create_task(selector.prepare(root=root, trusted=True, operation_id='second'))
                # A separate selection must remain usable while this old store is held.
                b = await register(app, tmp_path, 'other')
                await asyncio.wait_for(selector.prepare(project_id=b.project_id, operation_id='other'), 2)
                assert not second.done(), 'same-directory selection must wait before opening the old store'
                release.set()
                left, right = await asyncio.gather(first, second)
                assert left['status'] == right['status'] == 'ready'
                assert left['scope_id'] == right['scope_id'] == location.workspace_id
            finally:
                release.set()
                await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('explicit_id', [False, True])
def test_registered_selection_does_not_depend_on_legacy_marker(monkeypatch, tmp_path, explicit_id):
    from naiyue_test_agent.application.projects.selection import ProjectSelection
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            project = await register(app, tmp_path, 'code')
            marker = project.source.path / '.naiyue/workspace.json'
            marker.parent.mkdir()
            marker.write_text('invalid old marker')
            result = await ProjectSelection(app.projects).prepare(root=project.source.path,
                project_id=project.project_id if explicit_id else None, operation_id='registered')
            assert result['status'] == 'ready' and result['scope_id'] == project.project_id
    asyncio.run(scenario())
