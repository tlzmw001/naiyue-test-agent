"""Close SDK clients created by a model catalog without logging credentials."""

import asyncio
import logging
import httpx

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama


logger = logging.getLogger(__name__)


def prepare_owned_clients(models, owned):
    seen = set()
    for model in models:
        if id(model) in seen:
            continue
        seen.add(id(model))
        if isinstance(model, ChatOllama):
            owned.extend(((model._async_client._client, True), (model._client._client, False)))
            continue
        if isinstance(model, ChatOpenAI):
            sync, asynchronous = model.root_client, model.root_async_client
            proxy = model.openai_proxy
            transport_module = httpx
        elif isinstance(model, ChatAnthropic):
            import httpx2
            sync, asynchronous = model._client, model._async_client
            proxy = model.anthropic_proxy
            transport_module = httpx2
        else:
            continue
        options = {'timeout': sync.timeout}
        if proxy:
            options['proxy'] = proxy
        sync_http = transport_module.Client(**options)
        owned.append((sync_http, False))
        async_http = transport_module.AsyncClient(**options)
        owned.append((async_http, True))
        sync = sync.with_options(http_client=sync_http)
        asynchronous = asynchronous.with_options(http_client=async_http)
        if isinstance(model, ChatOpenAI):
            model.root_client, model.root_async_client = sync, asynchronous
            model.client, model.async_client = sync.chat.completions, asynchronous.chat.completions
        else:
            # These are cached SDK properties in the pinned LangChain adapter.
            model.__dict__['_client'], model.__dict__['_async_client'] = sync, asynchronous


async def aclose_models(clients):
    seen, primary = set(), None
    for client, asynchronous in clients:
        if id(client) in seen:
            continue
        seen.add(id(client))
        try:
            if asynchronous:
                await client.aclose()
            else:
                await asyncio.to_thread(client.close)
        except Exception as error:
            logger.error('model_client_close_failed error_type=%s', type(error).__name__)
            primary = primary or error
    if primary is not None:
        raise primary
