"""Manual compaction shares policy but does not execute an ordinary model turn."""

from runtime_context_helpers import typed_context
import asyncio
import json
import pytest
from uuid import uuid4
from typing import Any

from langchain_core.messages import AIMessage

from naiyue_test_agent.model_access import (MODEL_LIMITS_METADATA_KEY, COMPACTION_RESERVE_METADATA_KEY,
    COMPACTION_KEEP_RECENT_METADATA_KEY, COMPACTION_SUMMARY_OUTPUT_METADATA_KEY)
from test_async_application import StreamingToolModel
from test_web_api import web_client, request
from test_web_history_paging import read_pages


def compact_model():
    return StreamingToolModel(replies=[AIMessage(content='older details ' * 500), AIMessage(content='recent answer'),
        AIMessage(content='Earlier work was completed.')], metadata={
        MODEL_LIMITS_METADATA_KEY: {'context_window_tokens': 128000, 'max_output_tokens': 1000},
        COMPACTION_RESERVE_METADATA_KEY: {'tokens': 2000}, COMPACTION_KEEP_RECENT_METADATA_KEY: {'tokens': 20000},
        COMPACTION_SUMMARY_OUTPUT_METADATA_KEY: {'tokens': 256}})


async def history(client):
    sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
    for text in ('old task', 'recent task'):
        rid = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request(text))).json()['run_id']
        await client.get(f'/api/v1/scopes/chats/runs/{rid}/events')
    return sid


def stream_data(response):
    return [json.loads(line.removeprefix('data: ')) for line in response.text.splitlines() if line.startswith('data: ')]


def test_manual_compaction_summarizes_once_saves_and_is_idempotent(monkeypatch, tmp_path):
    async def scenario():
        model = compact_model()
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = await history(client)
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            before = (await client.get(endpoint)).json()['messages']
            body = {'request_id': str(uuid4())}
            response = await client.post(endpoint + '/compact', json=body)
            assert response.status_code == 202
            rid = response.json()['run_id']
            stream = await client.get(f'/api/v1/scopes/chats/runs/{rid}/events')
            operations = [event['data'] for event in stream_data(stream) if event['event_type'] == 'compaction_finished']
            assert len(operations) == 1 and operations[0]['trigger'] == 'manual' and operations[0]['status'] == 'applied'
            after = (await client.get(endpoint)).json()
            assert after['has_compaction'] and after['can_run'] and not after['busy']
            # Live presentation keeps the readable transcript; model history is compacted.
            assert after['messages'] == before
            saved = await web.state.service.application.history.messages(sid)
            assert saved[0].additional_kwargs['naiyue_compaction_summary']
            assert [message.id for message in saved[1:]] == [message['id'] for message in before[-2:]]
            assert len(model.seen) == 3
            assert (await client.post(endpoint + '/compact', json=body)).json() == response.json()
            assert len(model.seen) == 3
            assert (await client.post(endpoint + '/runs', json={**body, 'text': 'cannot reuse'})).status_code == 409
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            assert (await client.post(endpoint + '/prepare')).status_code == 200
            reopened = (await client.get(endpoint)).json()
            assert [message['id'] for message in reopened['messages']] == [message['id'] for message in before]
            assert all(not message['is_context_summary'] for message in reopened['messages'])
            assert [message.id for message in await web.state.service.application.history.messages(sid)] == [message.id for message in saved]
            assert reopened['has_compaction'] and reopened['can_run']
            assert len(model.seen) == 3
    asyncio.run(scenario())


def test_too_small_history_skips_without_summary_or_new_message(monkeypatch, tmp_path):
    async def scenario():
        model = compact_model()
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/compact', json={'request_id': str(uuid4())})
            assert response.status_code == 202
            stream = await client.get(f'/api/v1/scopes/chats/runs/{response.json()["run_id"]}/events')
            assert any(event['event_type'] == 'compaction_finished' and event['data']['status'] == 'skipped' for event in stream_data(stream))
            assert model.seen == []
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            assert view['messages'] == [] and view['can_run']
    asyncio.run(scenario())


def test_summary_failure_keeps_history_and_allows_continuing(monkeypatch, tmp_path):
    async def scenario():
        model = compact_model()
        model.replies[-1] = AIMessage(content='')
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = await history(client)
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            before = (await client.get(endpoint)).json()['messages']
            response = await client.post(endpoint + '/compact', json={'request_id': str(uuid4())})
            assert response.status_code == 202
            stream = await client.get(f'/api/v1/scopes/chats/runs/{response.json()["run_id"]}/events')
            assert any(event['event_type'] == 'compaction_finished' and event['data']['status'] == 'failed' for event in stream_data(stream))
            after = (await client.get(endpoint)).json()
            assert after['messages'] == before and after['can_run']
    asyncio.run(scenario())


class GatedSummaryModel(StreamingToolModel):
    summary_entered: Any = None
    summary_release: Any = None

    async def _next(self, messages):
        if len(self.seen) == 2 and self.summary_entered is not None:
            self.summary_entered.set()
            await self.summary_release.wait()
        return await super()._next(messages)


def test_busy_compaction_rejects_new_work_and_stop_keeps_checkpoint(monkeypatch, tmp_path):
    async def scenario():
        base = compact_model()
        model = GatedSummaryModel(replies=base.replies, metadata=base.metadata,
            summary_entered=asyncio.Event(), summary_release=asyncio.Event())
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = await history(client)
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            before = await web.state.service.application.history.messages(sid)
            started = await client.post(endpoint + '/compact', json={'request_id': str(uuid4())})
            rid = started.json()['run_id']
            await asyncio.wait_for(model.summary_entered.wait(), 5)
            try:
                assert (await client.post(endpoint + '/compact', json={'request_id': str(uuid4())})).status_code == 409
                control = {'request_id': str(uuid4()), 'kind': 'steering', 'text': 'do not queue'}
                assert (await client.post(f'/api/v1/scopes/chats/runs/{rid}/controls', json=control)).status_code == 409
                stop = await client.post(f'/api/v1/scopes/chats/runs/{rid}/controls', json={'request_id': str(uuid4()), 'kind': 'stop'})
                assert stop.status_code == 202
                await web.state.delivery.wait(rid)
            finally:
                model.summary_release.set()
            after = await web.state.service.application.history.messages(sid)
            assert after == before and len(model.seen) == 2
            result = (await client.get(f'/api/v1/scopes/chats/runs/{rid}')).json()
            assert result['mode'] == 'compact' and result['status'] == 'stopped' and not result['manual_review_required']
            assert (await client.get(endpoint)).json()['can_run']
    asyncio.run(scenario())


@pytest.mark.parametrize('failure_step', (1, 2))
def test_replacement_failure_is_not_reported_as_summary_failure(monkeypatch, tmp_path, failure_step):
    async def scenario():
        model = compact_model()
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = await history(client)
            app = web.state.service.application
            original = type(app.runs._agent).aupdate_state
            writes = 0

            async def fail_write(self, *args, **kwargs):
                nonlocal writes
                writes += 1
                if writes == failure_step:
                    raise OSError('synthetic checkpoint failure')
                return await original(self, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(type(app.runs._agent), 'aupdate_state', fail_write)
                response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/compact', json={'request_id': str(uuid4())})
                rid = response.json()['run_id']
                stream = await client.get(f'/api/v1/scopes/chats/runs/{rid}/events')
            assert type(app.runs._agent).aupdate_state is original
            events = stream_data(stream)
            assert not any(e['event_type'] == 'compaction_finished' and e['data']['status'] == 'applied' for e in events)
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            assert view['manual_review_required'] and not view['can_run']
            result = (await client.get(f'/api/v1/scopes/chats/runs/{rid}')).json()
            assert result['status'] == 'failed' and result['failure']['error_type'] == 'ManualCompactionCommitError'
    asyncio.run(scenario())


def test_pending_approval_cannot_be_compacted_or_implicitly_approved(monkeypatch, tmp_path):
    from test_async_application import call

    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('not read without approval')
        model = compact_model()
        model.replies = [call('read', {'path': str(outside)}, 'outside-call')]
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            rid = (await client.post(endpoint + '/runs', json=request('read outside'))).json()['run_id']
            await web.state.delivery.wait(rid)
            pending = (await client.get(endpoint)).json()
            assert pending['approval_batches'] and pending['pending_approval'] and not pending['busy']
            assert (await client.post(endpoint + '/compact', json={'request_id': str(uuid4())})).status_code == 409
            after = (await client.get(endpoint)).json()
            assert after['approval_batches'] == pending['approval_batches'] and after['messages'] == pending['messages']
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_stop_accepted_before_empty_session_compaction_still_finishes_safely(monkeypatch, tmp_path):
    async def scenario():
        model = compact_model()
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            app = web.state.service.application
            def stop_at_start(event):
                if event.event_type == 'run_started' and event.data.mode == 'compact':
                    app.runs.stop(sid, run_id=event.run_id)
            unsubscribe = app.observability.subscribe(stop_at_start)
            try:
                response = await client.post(f'/api/v1/scopes/chats/sessions/{sid}/compact', json={'request_id': str(uuid4())})
                rid = response.json()['run_id']
                await web.state.delivery.wait(rid)
            finally:
                unsubscribe()
            result = (await client.get(f'/api/v1/scopes/chats/runs/{rid}')).json()
            assert result['status'] == 'stopped' and result['failure'] is None
            view = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()
            assert view['can_run'] and view['messages'] == [] and model.seen == []
    asyncio.run(scenario())


def test_small_model_manual_compaction_never_splits_the_latest_user_turn():
    from langchain_core.messages import HumanMessage, ToolMessage
    from naiyue_test_agent.agent_runtime.context_compaction import _acompact_messages
    from naiyue_test_agent.agent_runtime.errors import ContextCompactionError

    async def scenario():
        model = compact_model()
        model.replies = [AIMessage(content='short summary')]
        model.metadata[MODEL_LIMITS_METADATA_KEY] = {'context_window_tokens': 1200, 'max_output_tokens': 600}
        model.metadata[COMPACTION_SUMMARY_OUTPUT_METADATA_KEY] = {'tokens': 32}
        messages = [HumanMessage(content='old request', id='old-user'), AIMessage(content='old answer', id='old-ai'),
            HumanMessage(content='recent request ' * 180, id='recent-user'),
            AIMessage(content='', id='recent-call', tool_calls=[{'id': 'c', 'name': 'read', 'args': {}}]),
            ToolMessage(content='file result', tool_call_id='c', id='recent-result'), AIMessage(content='done', id='recent-ai')]
        with pytest.raises(ContextCompactionError, match='compacted_request_over_budget'):
            await _acompact_messages(messages, model, model_id='A', context=typed_context(), system_messages=[], tools=[], force=True, trigger='manual')
        assert model.seen == []
        assert messages[2].id == 'recent-user' and len(messages) == 6
    asyncio.run(scenario())


@pytest.mark.parametrize('automatic', [False, True])
def test_multi_page_compaction_keeps_original_chat_after_reopen(monkeypatch, tmp_path, automatic):
    async def scenario():
        model = compact_model()
        model.metadata[COMPACTION_KEEP_RECENT_METADATA_KEY]['tokens'] = 1
        model.replies = [AIMessage(id=f'answer-{n}', content=f'ANSWER_{n} ' + 'x' * 2000) for n in range(4)]
        async with web_client(monkeypatch, tmp_path, model, history_page_messages=2) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            for n in range(4):
                run = (await client.post(endpoint + '/runs', json=request(f'USER_{n} ' + 'y' * 2000))).json()['run_id']
                await web.state.delivery.wait(run)
            original = await read_pages(client, sid)
            first = (await client.get(endpoint + '/messages')).json()
            model.replies = [AIMessage(content='Short summary of older work.'), AIMessage(id='following', content='following answer')]
            if automatic:
                estimate = (await client.get(endpoint + '/context')).json()['current']['estimate']['estimated_input_tokens']
                window = model.metadata[MODEL_LIMITS_METADATA_KEY]['context_window_tokens']
                model.metadata[COMPACTION_RESERVE_METADATA_KEY]['tokens'] = window - estimate + 100
                response = await client.post(endpoint + '/runs', json=request('new task'))
            else:
                response = await client.post(endpoint + '/compact', json={'request_id': str(uuid4())})
            assert response.status_code == 202
            await web.state.delivery.wait(response.json()['run_id'])
            app = web.state.service.application
            saved = await app.history.messages(sid)
            assert saved[0].additional_kwargs['naiyue_compaction_summary']
            assert not any('USER_0' in str(message.content) for message in saved)
            if not automatic:
                assert len(model.seen) == 5
                following = await client.post(endpoint + '/runs', json=request('new task'))
                await web.state.delivery.wait(following.json()['run_id'])
            assert len(model.seen) == 6
            assert any(m.additional_kwargs.get('naiyue_compaction_summary') for m in model.seen[-1])
            assert not any('USER_0' in str(m.content) or 'ANSWER_0' in str(m.content) for m in model.seen[-1])
            older = await client.get(endpoint + '/messages', params={'before': first['next_cursor']})
            assert older.status_code == 200 and older.json()['coverage']['upper'] == first['coverage']['upper']
            before_close = await read_pages(client, sid)
            assert before_close[:len(original)] == original
            saved_ids = [m.id for m in await app.history.messages(sid)]
        async with web_client(monkeypatch, tmp_path, model, history_page_messages=2) as (client, web):
            assert (await client.post(endpoint + '/prepare')).status_code == 200
            writes = web.state.service.application.sessions._persistence.storage.database.connection.total_changes
            assert await read_pages(client, sid) == before_close
            assert [m.id for m in await web.state.service.application.history.messages(sid)] == saved_ids
            assert web.state.service.application.sessions._persistence.storage.database.connection.total_changes == writes
            assert len(model.seen) == 6
    asyncio.run(scenario())
