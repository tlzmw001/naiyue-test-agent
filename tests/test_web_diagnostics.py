"""Diagnostic queries are scoped, bounded, cold and independent of execution."""

from naiyue_test_agent.storage.queries import write_one

import asyncio

from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_async_application import StreamingToolModel
from test_diagnostic_storage import fact, end, limits
from test_model_usage_storage import seed
from test_web_api import web_client, request


@async_test
async def test_http_queries_enforce_auth_scope_cursor_and_read_only_pagination(monkeypatch, tmp_path):
    model = StreamingToolModel(replies=[])
    async with web_client(monkeypatch, tmp_path, model, diagnostic_page_size=1) as (client, web):
        app = web.state.service.application
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        store, cfg = app.sessions._persistence.storage, limits()
        for rid in ('one', 'two'):
            store.diagnostics.save_diagnostic_event(fact(sid=sid, rid=rid), cfg)
            store.diagnostics.finish_diagnostic_run(end(sid=sid, rid=rid), cfg)
        base = f'/api/sessions/{sid}/diagnostics'
        assert (await client.get(base, headers={'Authorization': ''})).status_code == 401
        first = (await client.get(base)).json()
        assert [r['run_id'] for r in first['runs']] == ['two']
        second = (await client.get(base, params={'before': first['next_before']})).json()
        assert [r['run_id'] for r in second['runs']] == ['one']
        events = (await client.get(base + '/one')).json()
        assert [e['sequence'] for e in events['events']] == [1]
        tail = (await client.get(base + '/one', params={'after': events['next_after']})).json()
        assert tail['events'][0]['event_type'] == 'run_finished'
        assert (await client.get(base + '/missing')).status_code == 404
        assert (await client.get(base, params={'before': 0})).status_code == 422
        assert (await client.get(base + '/one', params={'after': -1})).status_code == 422
        assert (await client.get(base + '/one', params={'after': 2**64})).status_code == 422
        seed(store, 'foreign')
        with store.database.transaction(write=True) as connection:
            write_one(connection, "UPDATE naiyue_sessions SET workspace_root=?,execution_root_kind='local' WHERE session_id=?", (str(tmp_path / 'PRIVATE_ROOT'), 'foreign'))
        response = await client.get('/api/sessions/foreign/diagnostics')
        assert response.status_code == 404 and 'PRIVATE_ROOT' not in response.text
        seed(store, 'cold')
        def forbidden(*args, **kwargs):
            raise AssertionError('Diagnostic query loaded the graph')
        monkeypatch.setattr(app.runs._agent, 'aget_state', forbidden)
        store.database.connection.execute('PRAGMA query_only=ON')
        assert (await client.get('/api/sessions/cold/diagnostics')).json()['runs'] == []
        assert not app.sessions.is_loaded('cold') and model.seen == []
        store.database.connection.execute('PRAGMA query_only=OFF')
        web.state.service.settings = web.state.service.settings.model_copy(update={'max_frame_bytes': 100})
        assert (await client.get(base)).json()['error']['code'] == 'view_too_large'


@async_test
async def test_read_failure_does_not_stop_active_run_and_hides_sql_error(monkeypatch, tmp_path, caplog):
    model = StreamingToolModel(replies=[AIMessage(content='done')], entered=asyncio.Event(), release=asyncio.Event())
    async with web_client(monkeypatch, tmp_path, model) as (client, web):
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        rid = (await client.post(f'/api/sessions/{sid}/runs', json=request('task'))).json()['run_id']
        await asyncio.wait_for(model.entered.wait(), 5)
        try:
            def fail(*args, **kwargs):
                raise OSError('PRIVATE_SQL_BODY')
            monkeypatch.setattr(web.state.service.application.sessions._persistence.storage.diagnostics, 'read_diagnostic_runs', fail)
            response = await client.get(f'/api/sessions/{sid}/diagnostics')
            assert response.status_code == 503 and response.json()['error']['code'] == 'diagnostic_view_unavailable'
            assert not web.state.delivery.get(rid).task.done()
        finally:
            model.release.set()
            await web.state.delivery.wait(rid)
        assert web.state.delivery.get(rid).status == 'completed'
    assert 'diagnostic_view_read_failed' in caplog.text and 'PRIVATE_SQL_BODY' not in caplog.text
