"""Probe-only request limits at the owned SDK HTTP boundary, before transmission."""

import re
import logging
from contextlib import asynccontextmanager
from unittest.mock import patch
from threading import Lock

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langgraph.errors import GraphBubbleUp
from openai import OpenAIError
from anthropic import AnthropicError


class AcceptanceBudgetStop(OpenAIError, AnthropicError, GraphBubbleUp):
    """Stop acceptance without being retried by an SDK or converted into Auto Ask."""


class RequestBudget:
    def __init__(self, limit, parent=None):
        if type(limit) is not int or limit <= 0 or (parent is not None and limit > parent.limit):
            raise ValueError('Acceptance request limits must be positive integers within the parent limit')
        self.limit, self.parent = limit, parent
        self._root = parent._root if parent is not None else self
        if parent is None:
            self._lock, self._stopped = Lock(), False
        self._used, self._models = 0, {}

    def child(self, limit):
        return RequestBudget(limit, self)

    def reserve(self, model_id):
        if not isinstance(model_id, str) or re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', model_id) is None:
            raise ValueError('Invalid acceptance model label')
        levels, level = [], self
        while level is not None:
            levels.append(level)
            level = level.parent
        with self._root._lock:
            if self._root._stopped or any(level._used >= level.limit for level in levels):
                self._root._stopped = True
                raise AcceptanceBudgetStop('acceptance_request_budget_exhausted')
            for level in levels:
                level._used += 1
                level._models[model_id] = level._models.get(model_id, 0) + 1

    def snapshot(self):
        with self._root._lock:
            return {'limit': self.limit, 'used': self._used, 'stopped': self._root._stopped,
                    'models': dict(self._models)}


def guard_catalog(catalog, budget):
    """Install once on probe-owned SDK clients, before making model copies."""
    if getattr(catalog, '_acceptance_guard_installed', False):
        raise ValueError('Acceptance request guard is already installed')
    models = [(identity, catalog.get(identity)) for identity in catalog.model_ids]
    for identity, model in models:
        if not isinstance(model, (ChatOpenAI, ChatAnthropic, ChatOllama)):
            raise ValueError('This acceptance guard supports only OpenAI-compatible, Anthropic and Ollama models')
        if re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', identity) is None:
            raise ValueError('Invalid acceptance model label')
    catalog.prepare_async_clients()
    owned = {id(client) for client, _ in catalog._owned_http_clients}
    clients, sdks, seen = [], [], set()
    for identity, model in models:
        if isinstance(model, ChatOpenAI):
            pair = (model.root_client, model.root_async_client)
        else:
            pair = (model._client, model._async_client)
        if not isinstance(model, ChatOllama):
            sdks.append((model, pair))
        for sdk, asynchronous in zip(pair, (False, True)):
            client = sdk._client
            if id(client) not in owned:
                raise ValueError('Acceptance SDK client ownership is unconfirmed')
            if id(client) not in seen:
                clients.append((identity, client, asynchronous))
                seen.add(id(client))
    for model, pair in sdks:
        model.max_retries = 0
        for sdk in pair:
            sdk.max_retries = 0
    for identity, client, asynchronous in clients:
        if asynchronous:
            async def before_send(request, label=identity):
                budget.reserve(label)
        else:
            def before_send(request, label=identity):
                budget.reserve(label)
        client.event_hooks['request'].append(before_send)
    catalog._acceptance_guard_installed = True


@asynccontextmanager
async def guard_application_catalog(budget):
    """Wrap only a probe's loader; the application owns successful catalogs."""
    from naiyue_test_agent import application
    from naiyue_test_agent.agent_runtime.async_utils import await_settled
    from naiyue_test_agent.contracts.failures import safe_error_type

    original, failed = application.load_model_catalog, []

    def load(path):
        catalog = original(path)
        try:
            guard_catalog(catalog, budget)
        except BaseException:
            failed.append(catalog)
            raise
        return catalog

    try:
        with patch.object(application, 'load_model_catalog', load):
            yield
    finally:
        # A failed loader cannot hand ownership to the application's acquire step.
        for catalog in failed:
            try:
                await await_settled(catalog.aclose())
            except BaseException as error:
                logging.getLogger(__name__).error(
                    'probe_catalog_close_failed error_type=%s', safe_error_type(error))
