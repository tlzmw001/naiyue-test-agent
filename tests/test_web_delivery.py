"""Presentation/request cancellation never loses the running application's handle."""

import asyncio
from uuid import uuid4

import pytest
from test_web_api import web_client, request
from test_async_application import StreamingToolModel


def test_cancelled_start_waiter_still_has_queryable_run(monkeypatch, tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        model = StreamingToolModel(replies=[], entered=entered, release=release)
        async with web_client(monkeypatch, tmp_path, model) as (client, app):
            session = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            service = app.state.service
            holder = {}

            def abort_request(event):
                if event.event_type == 'run_started':
                    holder['run_id'] = event.run_id
                    holder['task'].cancel()

            unsubscribe = service.application.observability.subscribe(abort_request)
            body = request('start')
            from naiyue_test_agent.web.schemas import MessageInput, ControlInput
            task = asyncio.create_task(service.start(session, MessageInput(**body)))
            holder['task'] = task
            try:
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(entered.wait(), 5)
                run_id = holder['run_id']
                assert app.state.delivery.get(run_id).view()['status'] == 'running'
                again = await service.start(session, MessageInput(**body))
                assert again['run_id'] == run_id and len(model.seen) == 1
                await app.state.delivery.control(run_id, ControlInput(request_id=str(uuid4()), kind='stop'))
                await app.state.delivery.wait(run_id)
                assert app.state.delivery.get(run_id).view()['status'] == 'stopped'
            finally:
                unsubscribe()
                release.set()
    asyncio.run(scenario())
