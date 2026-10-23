"""Startup and shutdown retain resource ownership and the original exception."""
import asyncio

import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application import assembly
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.memory import create_memory_tool
from test_async_application import StreamingToolModel


def test_failed_graph_assembly_closes_already_started_memory_jobs(monkeypatch, tmp_path, caplog):
    async def scenario():
        from naiyue_test_agent.application.memory import memory_jobs
        closed = []
        failure = RuntimeError('PRIVATE_GRAPH')
        class Memory:
            def __init__(self, *args):
                pass
            def close(self):
                closed.append('memory')
        class Jobs:
            def __init__(self, *args, **kwargs):
                pass
            async def aclose(self):
                closed.append('jobs')
        def fail(*args, **kwargs):
            raise failure
        catalog = ModelCatalog('A', {'A': StreamingToolModel(replies=[])})
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
        monkeypatch.setattr(assembly, 'MemoryAccess', Memory)
        monkeypatch.setattr(memory_jobs, 'MemoryJobs', Jobs)
        monkeypatch.setattr(application.SessionService, 'assemble_runtime', fail)
        with pytest.raises(RuntimeError) as caught:
            await assembly.build_application('unused', database_path=tmp_path / 'state.sqlite',
                tools=[create_memory_tool()], system_prompt='', response_format=None, settings=AppSettings())
        assert caught.value is failure
        assert closed == ['jobs', 'memory']
        assert 'PRIVATE_GRAPH' not in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('model_close_fails', [False, True])
def test_close_releases_every_resource_and_keeps_first_exception(monkeypatch, tmp_path, caplog, model_close_fails):
    async def scenario():
        from test_temporary_application_storage import build
        app, _ = await build(monkeypatch, tmp_path, [])
        expected = RuntimeError('PRIVATE_MODEL' if model_close_fails else 'PRIVATE_DIAGNOSTIC')
        order = []
        close_model, close_usage = app.models._catalog.aclose, app.observability._usage.aclose
        close_diagnostics, close_store = app.observability._diagnostics.aclose, app.sessions._persistence.storage.close
        async def model():
            await close_model()
            order.append('model')
            if model_close_fails:
                raise expected
        async def diagnostic():
            await close_diagnostics()
            order.append('diagnostic')
            raise ValueError('PRIVATE_SECONDARY') if model_close_fails else expected
        async def usage():
            await close_usage()
            order.append('usage')
            raise OSError('PRIVATE_USAGE')
        def store():
            close_store()
            order.append('store')
            raise OSError('PRIVATE_STORE')
        monkeypatch.setattr(app.models._catalog, 'aclose', model)
        monkeypatch.setattr(app.observability._diagnostics, 'aclose', diagnostic)
        monkeypatch.setattr(app.observability._usage, 'aclose', usage)
        monkeypatch.setattr(app.sessions._persistence.storage, 'close', store)
        with pytest.raises(RuntimeError) as caught:
            await app.aclose()
        assert caught.value is expected
        assert order == ['model', 'diagnostic', 'usage', 'store']
        assert app.sessions._persistence.storage.database._closed
        assert app.observability._usage._task.done() and app.observability._diagnostics._task.done()
        with pytest.raises(ValueError, match='no active connection'):
            await app.runs._agent.checkpointer.conn.execute('SELECT 1')
        assert 'PRIVATE' not in caplog.text
        assert 'cleanup_secondary_failed' in caplog.text
    asyncio.run(scenario())
