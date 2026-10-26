"""Service resources are lazy; temporary hosts share only root capabilities."""
import asyncio
from langchain_core.messages import AIMessage, HumanMessage
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel
from test_service_ownership import options


def test_service_assembly_does_not_load_models_or_create_business_databases(monkeypatch, tmp_path):
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.config_paths import StorageSettings
    calls = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda p: calls.append(p))
    async def scenario():
        settings = AppSettings(storage=StorageSettings(data_dir=tmp_path / 'data'))
        async with open_service_application(tmp_path / 'missing.toml', tools=(), system_prompt='',
                response_format=None, settings=settings) as app:
            assert app._lifetime._children == []
            assert calls == []
        assert calls == []
        assert not list(tmp_path.rglob('*.sqlite*'))
    asyncio.run(scenario())


def test_temporary_host_uses_ram_and_is_removed_from_root_on_close(monkeypatch, tmp_path):
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.config_paths import StorageSettings
    model = StreamingToolModel(replies=[AIMessage(content='answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda p: ModelCatalog('A', {'A': model}))
    async def scenario():
        settings = AppSettings(storage=StorageSettings(data_dir=tmp_path / 'data'))
        async with open_service_application('unused', tools=(), system_prompt='', response_format=None, settings=settings) as app:
            host = await app.temporary.open('temp-test')
            sid = await host.sessions.create_workspace('A', tmp_path)
            result = await host.runs.start(sid, [HumanMessage(content='question')])
            assert result['messages'][-1].content == 'answer'
            assert host.project_id is None and host.sessions._sessions.memory_jobs is None
            assert len(app._lifetime._children) == 1
            await app.temporary.close('temp-test')
            assert app._lifetime._children == []
            assert host._lifetime._closed
        assert not list(tmp_path.rglob('*.sqlite*'))
        assert not (tmp_path / '.naiyue/workspace.json').exists()
    asyncio.run(scenario())


def test_model_prepare_primary_is_preserved_when_close_also_fails(monkeypatch):
    from naiyue_test_agent.application.service_application import DeferredModels
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import pytest
    primary = ValueError('prepare')
    def prepare(): raise primary
    value = SimpleNamespace(prepare_async_clients=prepare, aclose=AsyncMock(side_effect=OSError('close')))
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: value)
    async def scenario():
        loader = DeferredModels('unused')
        with pytest.raises(ValueError) as caught:
            await loader.get()
        assert caught.value is primary and loader.value is None
        value.aclose.assert_awaited_once()
    asyncio.run(scenario())
