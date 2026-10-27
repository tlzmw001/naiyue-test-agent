"""Async IO must preserve compaction budgets, history rules and error identity."""

from runtime_context_helpers import RunIdentity, RunObservationPorts, typed_context
import asyncio
import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult

from naiyue_test_agent.agent_runtime import context_compaction
from naiyue_test_agent.agent_runtime.context_compaction import (
    ContextCompactionError, SUMMARY_MARKER, SUMMARY_PREFIX,
)
from naiyue_test_agent.agent_runtime.context_operation_observation import ContextOperationObserver
from naiyue_test_agent.agent_runtime.context_recovery import recover_input_overflow
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from test_async_context_chain import model, history, graph_for, execute, CONFIG, overflow_error
from test_context_compaction import _model
from test_async_context_chain import AsyncContextModel


async def compact(chosen, messages=None, *, force=False, context=None):
    return await context_compaction._acompact_messages(
        history() if messages is None else messages, chosen, model_id='A', context=context if context is not None else typed_context(),
        system_messages=[SystemMessage(content='BASEPROJECT')], tools=[], force=force,
    )


@pytest.mark.parametrize('result', [
    httpx.ReadTimeout('PRIVATE'),
    APIConnectionError(message='PRIVATE', request=httpx.Request('POST', 'https://example.invalid')),
    AIMessage(content=''), AIMessage(content='partial', response_metadata={'finish_reason': 'length'}),
])
def test_known_summary_failure_within_send_budget_falls_back(result, caplog):
    async def scenario():
        chosen = model(summaries=[result])
        with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
            response, _ = await execute(graph_for({'A': chosen}), history(), 'invoke')
        assert response['messages'][:-1] == history()
        assert len(chosen.summary_inputs) == len(chosen.normal_inputs) == 1
        assert any(getattr(record, 'context_compaction', {}).get('event') == 'context_compaction_fallback'
                   for record in caplog.records)
        assert 'PRIVATE' not in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('result', [httpx.ReadTimeout('PRIVATE'), AIMessage(content='')])
def test_summary_failure_over_send_budget_cannot_fall_back(result):
    async def scenario():
        messages = history()
        messages[0] = HumanMessage(content='PRIVATE_' + 'x' * 800, id='old-user')
        chosen = model(window=800, output=600, reserve=600, summaries=[result])
        assert count_tokens_approximately([SystemMessage(content='BASEPROJECT'), *messages]) > 200
        graph = graph_for({'A': chosen})
        with pytest.raises(ContextCompactionError) as caught:
            await execute(graph, messages, 'invoke')
        assert str(caught.value) == ('summary_failed' if isinstance(result, Exception) else 'summary_invalid')
        assert len(chosen.summary_inputs) == 1 and chosen.normal_inputs == []
        assert (await graph.aget_state(CONFIG)).values['messages'] == messages
    asyncio.run(scenario())


@pytest.mark.parametrize('error', [RuntimeError('PRIVATE'), httpx.ConnectError('PRIVATE')])
def test_unknown_summary_exception_does_not_fall_back(error):
    async def scenario():
        chosen = model(summaries=[error])
        with pytest.raises(ContextCompactionError, match='summary_failed') as caught:
            await compact(chosen)
        assert caught.value.__cause__ is error and len(chosen.summary_inputs) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize(('summary', 'reason'), [
    (AIMessage(content='', tool_calls=[{'id': 'bad', 'name': 'read', 'args': {}}]), 'summary_invalid'),
    (AIMessage(content='PRIVATE', response_metadata={'finish_reason': 'content_filter'}), 'summary_invalid'),
    (AIMessage(content='x' * 4096), 'compacted_request_over_budget'),
])
def test_invalid_or_oversized_summary_is_not_committed(summary, reason):
    async def scenario():
        chosen = model(summaries=[summary])
        graph = graph_for({'A': chosen})
        with pytest.raises(ContextCompactionError, match=reason):
            await execute(graph, history(), 'invoke')
        assert chosen.normal_inputs == [] and len(chosen.summary_inputs) == 1
        assert (await graph.aget_state(CONFIG)).values['messages'] == history()
    asyncio.run(scenario())


def test_native_and_sync_sdk_compaction_share_cut_and_previous_summary():
    async def scenario():
        previous = HumanMessage(content=SUMMARY_PREFIX + 'previous fact', id='previous',
                                additional_kwargs={SUMMARY_MARKER: True})
        messages = [previous, *history()]
        native = model()
        sync = _model([], window=500, reserve=450, keep=10,
                      summaries=[AIMessage(content='brief summary')])
        expected = await context_compaction._acompact_messages(
            messages, sync, model_id='A', context=typed_context(),
            system_messages=[SystemMessage(content='BASEPROJECT')], tools=[],
        )
        actual = await compact(native, messages)
        assert [(m.type, m.content, m.additional_kwargs) for m in actual['messages']] == [
            (m.type, m.content, m.additional_kwargs) for m in expected['messages']]
        assert '<previous-summary>\nprevious fact\n</previous-summary>' in native.summary_inputs[0][-1].content
        assert [m.id for m in actual['messages'][2:]] == ['recent-user', 'recent-ai', 'new-user']
    asyncio.run(scenario())


def test_old_tool_pair_can_be_summarized_without_tool_execution():
    async def scenario():
        messages = history()
        messages[1:1] = [
            AIMessage(content='', tool_calls=[{'id': 'old-read', 'name': 'read', 'args': {'path': 'old.txt'}}]),
            ToolMessage(content='old observed file', tool_call_id='old-read'),
        ]
        chosen = model()
        update = await compact(chosen, messages)
        assert '<tool_call id="old-read"' in chosen.summary_inputs[0][-1].content
        assert '<message type="tool" tool_call_id="old-read"' in chosen.summary_inputs[0][-1].content
        assert update['messages'][2:] == history()[2:]
        assert chosen.normal_inputs == []
    asyncio.run(scenario())


@pytest.mark.parametrize('force', [False, True])
def test_force_disables_transient_failure_fallback(force):
    async def scenario():
        chosen = model(summaries=[httpx.ReadTimeout('PRIVATE')])
        if force:
            with pytest.raises(ContextCompactionError, match='summary_failed'):
                await compact(chosen, force=True)
        else:
            assert await compact(chosen) is None
        assert len(chosen.summary_inputs) == 1
    asyncio.run(scenario())


def test_cancelled_summary_closes_context_observation_and_preserves_signal(caplog):
    async def scenario():
        events = []
        pub = RunEventPublisher('s', 'r', (events.append,))
        pub.publish('run_started', RunStartedData('message'))
        observer = ContextOperationObserver(pub)
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = model(summary_entered=entered, summary_release=release)
        task = asyncio.create_task(compact(chosen, context=typed_context(identity=RunIdentity(session_id='s', run_id='r'), observation=RunObservationPorts(publisher=observer.publisher, context_operations=observer))))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel('PRIVATE')
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ('PRIVATE',)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert [e.event_type for e in events] == ['run_started', 'compaction_started', 'compaction_finished']
        assert events[-1].data.status == 'failed'
        assert events[-1].data.error_type == 'CancelledError'
        assert not observer._records
        assert 'PRIVATE' not in str(events) + caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('native_model', [False, True])
def test_retry_request_construction_failure_is_classified_as_retry(native_model, caplog):
    async def scenario():
        chosen = (model(window=2000, reserve=20) if native_model else
                  _model([], window=2000, reserve=20, summaries=[AIMessage(content='brief summary')]))
        error, calls = ValueError('PRIVATE'), []
        request = SimpleNamespace(runtime=SimpleNamespace(context=typed_context()), model=chosen,
                                  messages=history(), system_message=SystemMessage(content='BASE'), tools=[])

        def override(**kwargs):
            raise error

        request.override = override

        async def sync_handler(received):
            calls.append(received)
            raise overflow_error()

        async def async_handler(received):
            return await sync_handler(received)

        with pytest.raises(ValueError) as caught:
            await recover_input_overflow.awrap_model_call(request, async_handler)
        assert caught.value is error and calls == [request]
        failed = [r.context_recovery for r in caplog.records
                  if getattr(r, 'context_recovery', {}).get('event') == 'context_overflow_recovery_failed']
        assert failed[-1]['stage'] == 'retry'
        assert 'PRIVATE' not in caplog.text
    asyncio.run(scenario())


class EchoContextModel(AsyncContextModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        marker = re.search(r'\b[0-9a-f]{32}\b', '\n'.join(str(m.content) for m in messages))
        assert marker is not None
        if str(messages[0].content).startswith('Summarize completed'):
            self.summary_inputs.append(messages)
        else:
            self.normal_inputs.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=marker.group(), response_metadata={'finish_reason': 'stop'},
        ))])


def test_context_probe_uses_real_assembly_and_state_stream_offline(monkeypatch, capsys):
    from probe_budget_helpers import guard_fake_catalog
    monkeypatch.setattr('scripts.provider_acceptance_budget.guard_catalog', guard_fake_catalog)
    from scripts.probe_async_model import _worker
    from naiyue_test_agent.model_access import ModelCatalog
    chosen = EchoContextModel(metadata={**model(window=128000, output=16384, reserve=16384).metadata,
        'naiyue_model_id': 'A'})
    monkeypatch.setattr('naiyue_test_agent.model_access.load_model_catalog', lambda _: ModelCatalog('A', {'A': chosen}))
    args = SimpleNamespace(config='unused.json', model_id=None, timeout_seconds=5, with_context=True)
    asyncio.run(_worker(args))
    assert len(chosen.summary_inputs) == len(chosen.normal_inputs) == 1
    assert 'async_context_probe=PASS logical_calls=2' in capsys.readouterr().out


def test_probe_parent_forwards_context_flag(monkeypatch, capsys):
    from scripts import probe_async_model
    seen = []
    monkeypatch.setattr('sys.argv', ['probe', '--with-context', '--timeout-seconds', '5'])

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='async_context_probe=PASS logical_calls=2\n')

    monkeypatch.setattr(probe_async_model.subprocess, 'run', run)
    assert probe_async_model.main() == 0
    assert '--with-context' in seen[0][0] and seen[0][1]['timeout'] == 10
    assert capsys.readouterr().out.strip() == 'async_context_probe=PASS logical_calls=2'
