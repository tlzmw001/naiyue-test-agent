"""Session overrides use validated capabilities without mutating shared adapters."""

from legacy_store_schema import remove_reply_schema

import asyncio
import sqlite3

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA

from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.application.sessions.session_service import SessionService
from naiyue_test_agent.storage.project_store import ProjectStore
from test_model_access import config_for_all_kinds, write_config
from test_async_application import StreamingToolModel
from test_web_api import web_client


def catalog_with_thinking(tmp_path):
    config = config_for_all_kinds()
    for name in config['models']:
        config['models'][name]['thinking'] = {'supported_levels': ['off', 'low', 'high'], 'default_level': 'off'}
    return load_model_catalog(write_config(tmp_path, config))


def test_overrides_reach_all_adapters_and_do_not_mutate_defaults(tmp_path):
    catalog = catalog_with_thinking(tmp_path)
    try:
        for name in catalog.model_ids:
            original = catalog.get(name)
            assert catalog.thinking_options(name) == {'supported_levels': ['off', 'low', 'high'], 'default_level': 'off'}
            high = catalog.with_thinking(name, 'high')
            assert high is not original and type(high) is type(original)
            if name == 'local':
                assert high.reasoning == 'high' and original.reasoning is False
                assert high._async_client is original._async_client
            else:
                assert high.reasoning_effort == 'high'
                if name == 'claude':
                    assert high._async_client is original._async_client
                    assert high.thinking is None and original.thinking == {'type': 'disabled'}
                    assert catalog.with_thinking(name, 'off').reasoning_effort is None
                else:
                    assert high.async_client is original.async_client
                    assert original.reasoning_effort == 'none'
            assert catalog.with_thinking(name, 'model_default') is original
            with pytest.raises(ValueError):
                catalog.with_thinking(name, 'xhigh')
    finally:
        asyncio.run(catalog.aclose())


def test_session_selection_is_local_and_model_change_resets_override(tmp_path):
    catalog = catalog_with_thinking(tmp_path)
    try:
        sessions = SessionService(catalog)
        first, second = sessions.create_session('official'), sessions.create_session('official')
        sessions.require(first).select_thinking('official', 'high')
        assert sessions.require(first).describe_session()['thinking_level'] == 'high'
        assert sessions.resolve_model(first)[1].reasoning_effort == 'high'
        assert sessions.resolve_model(second)[1] is catalog.get('official')
        sessions.require(first).select_model('claude')
        assert sessions.require(first).describe_session()['thinking_level'] == 'model_default'
        with pytest.raises(ValueError):
            sessions.require(first).select_thinking('official', 'low')
        assert sessions.resolve_model(first)[1] is catalog.get('claude')
    finally:
        asyncio.run(catalog.aclose())


def test_current_schema_four_is_upgraded_without_losing_sessions(tmp_path):
    path = tmp_path / 'current.sqlite'
    # Build a complete real metadata row; migration now also preserves its display order.
    with ProjectStore(path) as store:
        store.sessions.create('kept', {'thread_id': 'kept-thread', 'model_id': 'official', 'workspace_root': None,
                             'active_tool_names': (), 'permission_mode': 'Default'})
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('''DROP TABLE naiyue_model_invocations; DROP TABLE naiyue_model_usage_runs;
            DROP TABLE naiyue_chat_messages; DROP TABLE naiyue_chat_sync;
            ALTER TABLE naiyue_sessions DROP COLUMN thinking_level;
            ALTER TABLE naiyue_sessions DROP COLUMN title;
            ALTER TABLE naiyue_sessions DROP COLUMN pinned;
            ALTER TABLE naiyue_sessions DROP COLUMN sort_order;
            UPDATE naiyue_store_info SET version=4;''')
    with ProjectStore(path) as store:
        row = store.database.connection.execute('SELECT * FROM naiyue_sessions').fetchone()
        assert row['session_id'] == 'kept' and row['thinking_level'] == 'model_default'
        assert store.database.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == CURRENT_SCHEMA


def test_web_selection_is_validated_authenticated_and_restored(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            catalog = web.state.service.application._catalog
            catalog._thinking_capabilities['A'] = {'kind': 'openai', 'levels': ('low', 'high')}
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/sessions/{sid}/thinking'
            body = {'model_id': 'A', 'thinking_level': 'high'}
            assert (await client.patch(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
            assert (await client.patch(endpoint, json=body)).json() == body
            for bad in ({**body, 'thinking_level': 'max'}, {**body, 'model_id': 'other'}, {**body, 'extra': 1}):
                assert (await client.patch(endpoint, json=bad)).status_code == 422
            assert (await client.get(f'/api/sessions/{sid}')).json()['thinking_level'] == 'high'
            assert model.seen == []
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            web.state.service.application._catalog._thinking_capabilities['A'] = {'kind': 'openai', 'levels': ('low', 'high')}
            assert (await client.post(f'/api/sessions/{sid}/prepare')).status_code == 200
            assert (await client.get(f'/api/sessions/{sid}')).json()['thinking_level'] == 'high'
            assert (await client.patch(f'/api/sessions/{sid}/model', json={'model_id': 'A'})).status_code == 200
            assert (await client.get(f'/api/sessions/{sid}')).json()['thinking_level'] == 'model_default'
    asyncio.run(scenario())
