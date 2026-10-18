"""Model callbacks publish finite facts and release in-flight references."""

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.callbacks.manager import CallbackManager
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData, AgentEventSource
from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
from native_application_helpers import async_test


def callback():
    from naiyue_test_agent.agent_runtime.model_usage_observation import ModelUsageCallback
    return ModelUsageCallback()


def metadata():
    from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
    return {MODEL_ID_METADATA_KEY: 'configured-model', 'unrelated': 'PRIVATE_METADATA'}


def output(usage):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content='PRIVATE_OUTPUT', usage_metadata=usage))]])


@async_test
@pytest.mark.parametrize('tags,purpose', [([], 'agent'), (['naiyue_summary'], 'summary'),
    (['naiyue_permission_review', 'naiyue_permission_review_fast'], 'auto_fast'),
    (['naiyue_permission_review', 'naiyue_permission_review_detailed'], 'auto_detailed')])
async def test_start_end_pairing_uses_framework_id_and_existing_scope(tags, purpose, caplog):
    events, identity = [], uuid4()
    publisher = RunEventPublisher('session', 'root-run', (events.append,))
    publisher.publish('run_started', RunStartedData('message'))
    with bind_run_services(RunServices(publisher=publisher)):
        handler = callback()
        await handler.on_chat_model_start({'PRIVATE': 'SERIALIZED'}, [['PRIVATE_PROMPT']],
            run_id=identity, metadata=metadata(), tags=tags)
        await handler.on_llm_end(output({'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105,
            'input_token_details': {'cache_read': 20, 'cache_creation': 30}}), run_id=identity, tags=tags)
    start, end = events[1:]
    assert start.event_type == 'model_invocation_started' and end.event_type == 'model_invocation_finished'
    assert start.data.invocation_id == end.data.invocation_id == str(identity)
    assert start.data.model_id == 'configured-model' and start.data.purpose == purpose
    assert end.data.status == 'returned' and end.data.usage_status == 'final'
    assert (end.data.input_tokens, end.data.output_tokens, end.data.cache_read_tokens, end.data.cache_write_tokens) == (100, 5, 20, 30)
    assert [e.sequence for e in events] == [1, 2, 3]
    assert 'PRIVATE' not in repr(events) + caplog.text
    assert handler._active == {}  # Completed calls release publisher/task references.


@async_test
@pytest.mark.parametrize('error,expected', [(RuntimeError('PRIVATE_ERROR'), 'failed'),
                                           (asyncio.CancelledError('PRIVATE_CANCEL'), 'cancelled')])
@pytest.mark.parametrize('partial', [True, False])
async def test_error_facts_preserve_partial_vs_unavailable_without_raising(error, expected, partial):
    events, identity = [], uuid4()
    publisher = RunEventPublisher('s', 'r', (events.append,))
    with bind_run_services(RunServices(publisher=publisher)):
        handler = callback()
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
        await handler.on_llm_error(error, run_id=identity, response=output({
            'input_tokens': 100, 'output_tokens': 0, 'total_tokens': 100}) if partial else None)
    data = events[-1].data
    assert data.status == expected and data.error_type == type(error).__name__
    assert data.usage_status == ('partial' if partial else 'unavailable')
    assert data.input_tokens == (100 if partial else None)
    assert data.output_tokens == (0 if partial else None)
    assert data.cache_read_tokens is None and data.cache_write_tokens is None
    assert 'PRIVATE' not in repr(events)


@async_test
async def test_child_facts_use_original_root_envelope_and_child_source():
    events = []
    root = RunEventPublisher('s', 'r', (events.append,))
    source = AgentEventSource('root/tester/child', 'child', 'child-run', 'r', 'delegate')
    handler, identity = callback(), uuid4()
    with bind_run_services(RunServices(publisher=root.for_agent(source))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
        await handler.on_llm_end(output(None), run_id=identity)
    assert events[0].session_id == 's' and events[0].run_id == 'r'
    assert events[0].source == source


@async_test
async def test_no_run_does_not_fabricate_a_session_and_unknown_identity_is_observable(caplog):
    await callback().on_chat_model_start({}, [], run_id=uuid4(), metadata=metadata())
    events = []
    handler, identity = callback(), uuid4()
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata={'PRIVATE': 'NOT_ID'})
        await handler.on_llm_end(output(None), run_id=identity)
    assert events[0].data.model_id is None
    assert 'model_usage_identity_unavailable' in caplog.text and 'PRIVATE' not in caplog.text


@async_test
async def test_observation_fault_is_diagnosed_without_provider_content(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime import model_usage_observation as module
    def broken(_):
        raise ValueError('PRIVATE_INTERNAL')
    monkeypatch.setattr(module, 'normalize_model_usage', broken)
    with bind_run_services(RunServices(publisher=RunEventPublisher('s', 'r', ()))):
        handler = callback()
        for status in ('returned', 'failed'):
            identity = uuid4()
            await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
            if status == 'returned':
                await handler.on_llm_end(output(None), run_id=identity)
            else:
                await handler.on_llm_error(RuntimeError('PRIVATE_PROVIDER'), run_id=identity)
        assert handler._active == {}
    assert 'model_usage_observation_failed' in caplog.text and 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('managed', [False, True])
def test_installation_preserves_other_callbacks_and_is_idempotent_for_shared_models(managed):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback, ModelUsageCallback
    sentinel = BaseCallbackHandler()
    model = FakeMessagesListChatModel(responses=[], callbacks=CallbackManager([sentinel]) if managed else [sentinel])
    catalog = ModelCatalog('A', {'A': model, 'B': model})
    install_model_usage_callback(catalog)
    install_model_usage_callback(catalog)
    handlers = model.callbacks.handlers if managed else model.callbacks
    assert handlers[0] is sentinel and sum(isinstance(h, ModelUsageCallback) for h in handlers) == 1
    assert len(handlers) == 2


@pytest.mark.parametrize('field,bad', [('invocation_id', ''), ('invocation_id', 'x' * 129),
    ('status', 'accepted'), ('usage_status', 'estimated'), ('input_tokens', True),
    ('cache_write_tokens', -1), ('error_type', 'PRIVATE\nBODY')])
def test_finished_event_contract_rejects_invalid_metadata(field, bad):
    from naiyue_test_agent.agent_runtime.events import ModelInvocationFinishedData
    values = {'invocation_id': 'call', 'status': 'returned', field: bad}
    with pytest.raises(ValueError):
        ModelInvocationFinishedData(**values)


def test_started_event_is_immutable_and_validates_purpose():
    from naiyue_test_agent.agent_runtime.events import ModelInvocationStartedData
    data = ModelInvocationStartedData('call', 'A', 'agent')
    with pytest.raises(FrozenInstanceError):
        data.model_id = 'B'
    with pytest.raises(ValueError):
        ModelInvocationStartedData('call', 'A', 'PRIVATE_PURPOSE')
