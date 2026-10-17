"""Application-owned factories and SDK clients close across failure/cancel."""

import asyncio
import threading

import pytest
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel


def test_catalog_closes_shared_sdk_clients_once(monkeypatch):
    async def scenario():
        model = ChatOpenAI(model='synthetic', api_key='synthetic-test-key', base_url='https://example.invalid')
        catalog = ModelCatalog('A', {'A': model, 'B': model})
        catalog._prepare_async_clients()
        counts = []
        sync_close, async_close = model.root_client._client.close, model.root_async_client._client.aclose

        def close_sync():
            counts.append('sync')
            sync_close()

        async def close_async():
            counts.append('async')
            await async_close()

        monkeypatch.setattr(model.root_client._client, 'close', close_sync)
        monkeypatch.setattr(model.root_async_client._client, 'aclose', close_async)
        await catalog.aclose()
        assert counts == ['sync', 'async']
        assert model.root_client._client.is_closed and model.root_async_client._client.is_closed
    asyncio.run(scenario())


def test_cancelled_catalog_acquisition_disposes_result(monkeypatch):
    async def scenario():
        entered, release, closed = asyncio.Event(), threading.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        catalog = ModelCatalog('A', {'A': StreamingToolModel(replies=[])})

        async def close():
            closed.set()

        def load(_path):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return catalog

        monkeypatch.setattr(catalog, 'aclose', close)
        monkeypatch.setattr(application, 'load_model_catalog', load)
        task = asyncio.create_task(build_async_local_application('unused', tools=[], system_prompt='', response_format=None))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_factory_cleanup_does_not_replace_assembly_error(monkeypatch, caplog):
    async def scenario():
        catalog = ModelCatalog('A', {'A': StreamingToolModel(replies=[])})
        failure = ValueError('synthetic assembly failure')
        closed = []

        async def close():
            closed.append(True)
            raise RuntimeError('PRIVATE_CLOSE')

        def assemble(*args, **kwargs):
            raise failure
        monkeypatch.setattr(catalog, 'aclose', close)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
        monkeypatch.setattr(application, '_assemble_application', assemble)
        with pytest.raises(ValueError, match='synthetic assembly failure') as caught:
            await build_async_local_application('unused', tools=[], system_prompt='', response_format=None)
        assert caught.value is failure and closed == [True]
        assert 'model_cleanup_secondary_failed' in caplog.text
        assert 'PRIVATE_CLOSE' not in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('klass', [ChatOpenAI, ChatAnthropic])
def test_two_async_applications_do_not_share_owned_transports(monkeypatch, klass):
    async def scenario():
        first = klass(model='synthetic', api_key='synthetic-test-key', base_url='https://example.invalid')
        second = klass(model='synthetic', api_key='synthetic-test-key', base_url='https://example.invalid')
        catalogs = [ModelCatalog('A', {'A': first}), ModelCatalog('A', {'A': second})]
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalogs.pop(0))
        a = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None)
        b = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None)
        try:
            x = first.root_async_client if isinstance(first, ChatOpenAI) else first._async_client
            y = second.root_async_client if isinstance(second, ChatOpenAI) else second._async_client
            assert x._client is not y._client
            await a.aclose()
            assert x._client.is_closed and not y._client.is_closed
        finally:
            await a.aclose()
            await b.aclose()
    asyncio.run(scenario())
