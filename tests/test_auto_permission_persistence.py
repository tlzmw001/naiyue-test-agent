"""Auto models validate before graph construction and modes survive safe restore."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.persistent import build_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import ToolSettings
from test_auto_permission_review import model, settings, verdict
from test_dynamic_tools import DynamicModel, definition, call


@pytest.mark.parametrize('entry', ['local', 'persistent', 'async', 'async_persistent'])
def test_unknown_review_id_fails_before_graph_even_in_default_mode(monkeypatch, tmp_path, entry):
    reached = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('main', {'main': model([]), 'fast': model([])}))
    monkeypatch.setattr(application, '_assemble_application', lambda *a, **kw: reached.append(True))
    options = settings().model_copy(update={'permissions': settings().permissions.model_copy(update={'mode': 'Default'})})
    args = dict(tools=[], settings=options, system_prompt='', response_format=None)
    async def scenario():
        if entry == 'async':
            await build_async_local_application('synthetic', **args)
        else:
            async with open_async_persistent_application('synthetic', database_path=tmp_path / 'async.sqlite', **args):
                pytest.fail('Invalid models must not open an application')
    with pytest.raises(ValueError):
        if entry == 'local':
            application.build_local_application('synthetic', **args)
        elif entry == 'persistent':
            with SessionStorage(tmp_path / 'sync.sqlite') as storage:
                build_persistent_application('synthetic', storage=storage, **args)
        else:
            asyncio.run(scenario())
    assert reached == []


@pytest.mark.parametrize('asynchronous', [False, True])
def test_auto_mode_restores_with_models_and_rejects_restore_without_them(monkeypatch, tmp_path, asynchronous):
    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        catalog = ModelCatalog('main', {'main': DynamicModel(responses=[call(), AIMessage(content='done')]),
            'fast': model([verdict()]), 'detail': model([])})
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
        args = dict(tools=[definition(approved=True)], settings=settings(), system_prompt='', response_format=None)
        if asynchronous:
            async with open_async_persistent_application('synthetic', database_path=path, **args) as app:
                sid = await app.acreate_session('main')
                await app.arun(sid, [('user', 'run')])
            async with open_async_persistent_application('synthetic', database_path=path, **args) as restored:
                await restored.arestore_session(sid)
                assert restored.get_permission_mode(sid) == 'Auto'
                assert restored._sessions._sessions[sid]['session_allow'] == []
                assert restored._sessions._sessions[sid]['run_review_project_instructions'] is None
            args['settings'] = ToolSettings()
            async with open_async_persistent_application('synthetic', database_path=path, **args) as unavailable:
                with pytest.raises(SessionPersistenceError, match='invalid_permission_mode'):
                    await unavailable.arestore_session(sid)
                assert not unavailable._sessions.has_session(sid)
        else:
            with SessionStorage(path) as storage:
                app = build_persistent_application('synthetic', storage=storage, **args)
                sid = app.create_session('main')
                app.run(sid, [('user', 'run')])
                assert storage.get(sid)['permission_mode'] == 'Auto'
            with SessionStorage(path) as storage:
                restored = build_persistent_application('synthetic', storage=storage, **args)
                restored.restore_session(sid)
                assert restored.get_permission_mode(sid) == 'Auto'
                assert restored._sessions._sessions[sid]['session_allow'] == []
                assert restored._sessions._sessions[sid]['run_review_project_instructions'] is None
            args['settings'] = ToolSettings()
            with SessionStorage(path) as storage:
                unavailable = build_persistent_application('synthetic', storage=storage, **args)
                with pytest.raises(SessionPersistenceError, match='invalid_permission_mode'):
                    unavailable.restore_session(sid)
                assert not unavailable._sessions.has_session(sid)
    asyncio.run(scenario())
