"""Auto models validate before graph construction and modes survive safe restore."""

from native_application_helpers import async_test, build_application


import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.application.session_storage import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import ToolSettings
from test_auto_permission_review import model, settings, verdict
from test_dynamic_tools import DynamicModel, definition, call


@pytest.mark.parametrize('entry', ['local', 'persistent'])
@async_test
async def test_unknown_review_id_fails_before_graph_even_in_default_mode(monkeypatch, tmp_path, entry):
    reached = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('main', {'main': model([]), 'fast': model([])}))
    monkeypatch.setattr(application.SessionService, 'assemble_runtime', lambda *a, **kw: reached.append(True))
    options = settings().model_copy(update={'permissions': settings().permissions.model_copy(update={'mode': 'Default'})})
    args = dict(tools=[], settings=options, system_prompt='', response_format=None)
    with pytest.raises(ValueError):
        if entry == 'local':
            await build_application('synthetic', **args)
        else:
            async with open_async_persistent_application('synthetic', database_path=tmp_path / 'sessions.sqlite', **args):
                pytest.fail('Invalid configuration must not acquire an application')
    assert reached == []


@async_test
async def test_auto_mode_restores_with_models_and_rejects_restore_without_them(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'sessions.sqlite'
        catalog = ModelCatalog('main', {'main': DynamicModel(responses=[call(), AIMessage(content='done')]),
            'fast': model([verdict()]), 'detail': model([])})
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
        args = dict(tools=[definition(approved=True)], settings=settings(), system_prompt='', response_format=None)
        async with open_async_persistent_application('synthetic', database_path=path, **args) as app:
            sid = await app.acreate_session('main')
            await app.arun(sid, [('user', 'run')])
            assert app._storage.get(sid)['permission_mode'] == 'Auto'
        async with open_async_persistent_application('synthetic', database_path=path, **args) as restored:
            await restored.arestore_session(sid)
            assert restored.get_permission_mode(sid) == 'Auto'
            assert restored._sessions.resolve_permission_context(sid)['session_allow'] == []
            assert restored._sessions.resolve_permission_context(sid)['project_instructions'] is None
        args['settings'] = ToolSettings()
        async with open_async_persistent_application('synthetic', database_path=path, **args) as unavailable:
            with pytest.raises(SessionPersistenceError, match='invalid_permission_mode'):
                await unavailable.arestore_session(sid)
            assert not unavailable._sessions.has_session(sid)
    await scenario()
