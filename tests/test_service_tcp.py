"""Real TCP broadcasts and server shutdown ordering with an unfinished model call."""
import asyncio
from contextlib import asynccontextmanager
import socket
import time
from uuid import uuid4

from langchain_core.messages import AIMessage
import pytest
import uvicorn

from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.client import ServiceClient
from naiyue_test_agent.service.ownership import service_running
from naiyue_test_agent.service.server import ServiceServer
from naiyue_test_agent.storage.project_store import ProjectStore
from test_async_application import StreamingToolModel
from test_multi_project_runtime import multi_app
from test_service_ownership import options


@asynccontextmanager
async def tcp_service(monkeypatch, tmp_path, model, **changes):
    assets = tmp_path / 'assets'
    assets.mkdir()
    @asynccontextmanager
    async def factory():
        async with multi_app(monkeypatch, tmp_path, model) as (app, _):
            yield app
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        config = options(tmp_path, port=listener.getsockname()[1], **changes)
        app = create_service_app(config, application_factory=factory,
            shutdown=lambda: setattr(server, 'should_exit', True))
        server = ServiceServer(uvicorn.Config(app, host=config.host, port=config.port,
            log_level='critical', access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if serving.done():
                    await serving
                    pytest.fail('server exited before startup')
                if time.monotonic() > deadline:
                    pytest.fail('server startup timed out')
                await asyncio.sleep(0.01)
            yield app, config, server, serving
        finally:
            if model.release is not None:
                model.release.set()
            server.should_exit = True
            await asyncio.wait_for(asyncio.shield(serving), 10)


def test_active_sse_does_not_block_shutdown_or_lock_release(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        async with tcp_service(monkeypatch, tmp_path, model) as (app, config, server, serving):
            a, b = await ServiceClient.connect(config), await ServiceClient.connect(config)
            stream = None
            try:
                base = '/api/v1/scopes/chats'
                sid = (await a.request('POST', base + '/sessions', json={'model_id': 'A'}))['session_id']
                run = await a.request('POST', f'{base}/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'waiting'})
                await asyncio.wait_for(entered.wait(), 5)
                stream = a.events('chats', run['run_id'])
                first = await asyncio.wait_for(anext(stream), 5)
                assert first['data']['event_type'] == 'run_started'
                assert await b.request('POST', '/api/v1/shutdown') == {'accepted': True}
                remaining = [event async for event in stream]
                await asyncio.wait_for(asyncio.shield(serving), 10)
                assert not release.is_set(), 'test must not release the unfinished model to enable shutdown'
                assert any(e['data'].get('event_type') == 'run_finished' for e in remaining)
                assert not service_running(config)
                with ProjectStore(config.data_dir / 'chats/sessions.sqlite') as store:
                    assert store.sessions.get(sid)['state'] != 'running'
            finally:
                if stream is not None:
                    await stream.aclose()
                await a.aclose()
                await b.aclose()
    asyncio.run(scenario())


def test_active_broadcast_disconnect_and_intermediate_cursor_resume(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(content='answer')], entered=entered, release=release)
        async with tcp_service(monkeypatch, tmp_path, model) as (_, config, server, serving):
            a, b = await ServiceClient.connect(config), await ServiceClient.connect(config)
            left = right = None
            try:
                base = '/api/v1/scopes/chats'
                sid = (await a.request('POST', base + '/sessions', json={'model_id': 'A'}))['session_id']
                run = await a.request('POST', f'{base}/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'question'})
                await asyncio.wait_for(entered.wait(), 5)
                left, right = a.events('chats', run['run_id']), b.events('chats', run['run_id'])
                first, same = await asyncio.gather(anext(left), anext(right))
                assert first == same
                cursor = int(first['id'])
                await left.aclose()
                release.set()
                remaining = [e async for e in right]
                resumed = [e async for e in a.events('chats', run['run_id'], after=cursor)]
                assert resumed == remaining and resumed
                assert all(int(e['id']) > cursor for e in resumed)
                assert any(e['data'].get('event_type') == 'run_finished' for e in resumed)
                assert (await b.request('GET', f'{base}/runs/{run["run_id"]}'))['status'] == 'completed'
            finally:
                for stream in (left, right):
                    if stream is not None:
                        await stream.aclose()
                await a.aclose()
                await b.aclose()
    asyncio.run(scenario())
