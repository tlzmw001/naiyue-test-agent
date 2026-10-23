"""Usage HTTP reads preserve authentication, workspace boundaries and live work."""

from naiyue_test_agent.storage.queries import write_one

from native_application_helpers import async_test
from test_web_api import web_client, request
from test_async_application import StreamingToolModel
from test_model_usage_queries import record
from test_model_usage_storage import seed


@async_test
async def test_usage_http_is_authenticated_scoped_bounded_and_read_only(monkeypatch, tmp_path):
    model = StreamingToolModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model, usage_page_models=1) as (client, web):
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        store = web.state.service.application.sessions._persistence.storage
        for i, name in enumerate(['A', 'B']):
            record(store, 'c' + str(i), sid=sid, model=name)
        assert (await client.get(f'/api/sessions/{sid}/usage', headers={'Authorization': ''})).status_code == 401
        first = await client.get(f'/api/sessions/{sid}/usage')
        assert first.status_code == 200
        body = first.json()
        assert body['recorded_calls'] == 2 and len(body['models']['items']) == 1
        second = (await client.get(f'/api/sessions/{sid}/usage', params={'after_model': 'A'})).json()
        assert second['models']['items'][0]['model_id'] == 'B' and second['metrics'] == body['metrics']
        assert (await client.get(f'/api/sessions/{sid}/usage', params={'after_model': ''})).status_code == 422
        assert (await client.get('/api/sessions/missing/usage')).status_code == 404
        assert model.seen == []
        seed(store, 'foreign')
        with store.database.transaction(write=True) as connection:
            write_one(connection, "UPDATE naiyue_sessions SET workspace_root=?,execution_root_kind='local' WHERE session_id=?", (str(tmp_path / 'PRIVATE_WORKSPACE'), 'foreign'))
        denied = await client.get('/api/sessions/foreign/usage')
        assert denied.status_code == 404 and 'PRIVATE' not in denied.text


@async_test
async def test_usage_failure_does_not_stop_the_owned_run(monkeypatch, tmp_path, caplog):
    import asyncio
    from langchain_core.messages import AIMessage
    entered, release = asyncio.Event(), asyncio.Event()
    model = StreamingToolModel(replies=[AIMessage(content='done')], entered=entered, release=release)
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/sessions/{sid}/runs', json=request('task'))).json()['run_id']
        try:
            await asyncio.wait_for(entered.wait(), 5)
            def fail(*args, **kwargs):
                raise OSError('PRIVATE_SQL_ERROR')
            monkeypatch.setattr(web.state.service.application.sessions._persistence.storage.usage, 'read_model_usage', fail)
            response = await client.get(f'/api/sessions/{sid}/usage')
            assert response.status_code == 503 and response.json()['error']['code'] == 'usage_view_unavailable'
            assert not web.state.delivery.get(rid).task.done()
            release.set()
            await web.state.delivery.wait(rid)
            assert web.state.delivery.get(rid).status == 'completed'
        finally:
            release.set()
    assert 'model_usage_view_read_failed' in caplog.text and 'PRIVATE_SQL_ERROR' not in caplog.text


@async_test
async def test_usage_frame_budget_and_cold_read_never_load_graph(monkeypatch, tmp_path):
    model = StreamingToolModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        app = web.state.service.application
        seed(app.sessions._persistence.storage, 'cold')
        def forbidden(*args, **kwargs):
            raise AssertionError('read attempted a graph operation')
        monkeypatch.setattr(app.runs._agent, 'aget_state', forbidden)
        response = await client.get('/api/sessions/cold/usage')
        assert response.status_code == 200 and response.json()['recorded_calls'] == 0
        assert not app.sessions.is_loaded('cold') and model.seen == []
        service = web.state.service
        service.settings = service.settings.model_copy(update={'max_frame_bytes': 100})
        response = await client.get('/api/sessions/cold/usage')
        assert response.status_code == 409 and response.json()['error']['code'] == 'view_too_large'
