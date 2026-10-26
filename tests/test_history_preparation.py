"""Preparation captures durable facts without owning the model/tool driver."""

import asyncio
from threading import Event

from langchain_core.messages import AIMessage
import pytest

from test_async_application import StreamingToolModel
from test_web_api import web_client, request


def test_failed_cold_prepare_does_not_report_history_ready(monkeypatch, tmp_path, caplog):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='saved')])) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('human'))).json()['run_id']
            await web.state.delivery.wait(rid)
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, web):
            storage = web.state.service.application.sessions._persistence.storage
            before = storage.conversation.chat_heads(sid)
            def fail(*args):
                raise OSError('PRIVATE_ARCHIVE_IO')
            with monkeypatch.context() as patch:
                patch.setattr(storage.conversation, 'save_chat_updates', fail)
                response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare')
            assert response.status_code == 409
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            assert view['history_ready'] is False and view['messages'][-1]['content'] == 'saved'
            assert storage.conversation.chat_heads(sid) == before
            assert 'PRIVATE_ARCHIVE_IO' not in response.text + caplog.text
            assert (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare')).json()['history_ready']
            assert (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).status_code == 201
    asyncio.run(scenario())


def test_prepare_fixed_coverage_does_not_pause_model_and_finish_advances_after_it(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[AIMessage(id='answer', content='completed')], entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            app = web.state.service.application
            p = app.sessions._persistence
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('human'))).json()['run_id']
            await asyncio.wait_for(entered.wait(), 5)
            committed, finish_entered, unblock = asyncio.Event(), asyncio.Event(), Event()
            loop = asyncio.get_running_loop()
            save, finish = p.storage.conversation.save_chat_updates, p.afinish
            def paused(*args):
                save(*args)
                loop.call_soon_threadsafe(committed.set)
                assert unblock.wait(5)
            async def finishing(*args):
                finish_entered.set()
                return await finish(*args)
            monkeypatch.setattr(p.storage.conversation, 'save_chat_updates', paused)
            monkeypatch.setattr(p, 'afinish', finishing)
            preparing = asyncio.create_task(client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare'))
            try:
                await asyncio.wait_for(committed.wait(), 5)
                frozen = p.storage.conversation.chat_heads(sid)[0]
                assert p.storage.sessions.get(sid)['state'] == 'running'
                release.set()
                await asyncio.wait_for(finish_entered.wait(), 5)
                assert len(model.seen) == 1 and not web.state.delivery.get(rid).task.done()
                assert p.storage.conversation.chat_heads(sid)[0] == frozen
                assert (await client.delete(f'/api/v1/scopes/chats/sessions/{sid}')).status_code == 409
            finally:
                unblock.set()
                release.set()
                await preparing
                await web.state.delivery.wait(rid)
            final = p.storage.conversation.chat_heads(sid)[0]
            assert final['revision'] > frozen['revision']
            assert final['checkpoint_id'] == p.storage.sessions.get(sid)['checkpoint_id']
            assert p.storage.sessions.get(sid)['state'] == 'ready'
            messages = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/messages')).json()['messages']
            assert [m['content'] for m in messages] == ['human', 'completed']
    asyncio.run(scenario())


def test_cancelled_prepare_waits_for_its_owned_commit(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            storage = web.state.service.application.sessions._persistence.storage
            entered, release = asyncio.Event(), Event()
            loop = asyncio.get_running_loop()
            original = storage.conversation.save_chat_updates
            def blocked(*args):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                return original(*args)
            monkeypatch.setattr(storage.conversation, 'save_chat_updates', blocked)
            operation = asyncio.create_task(client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare'))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                operation.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['history_ready']
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
    asyncio.run(scenario())
