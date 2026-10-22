"""Actual interactive graph runs the context chain with native async model IO."""

from naiyue_test_agent.application import prepare_tool_registry

import asyncio
import logging
from typing import Any
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from naiyue_test_agent.agent_runtime import _assemble_interactive_runtime
from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError, SUMMARY_MARKER
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from naiyue_test_agent.agent_runtime.tool_execution.permissions import fixed_permission_context


class AsyncContextModel(BaseChatModel):
    summary_results: list = Field(default_factory=list)
    normal_results: list = Field(default_factory=list)
    summary_inputs: list = Field(default_factory=list)
    normal_inputs: list = Field(default_factory=list)
    summary_options: list = Field(default_factory=list)
    summary_entered: Any = None
    summary_release: Any = None
    retry_entered: Any = None
    retry_release: Any = None

    @property
    def _llm_type(self):
        return 'native-async-context-test'

    def _generate(self, *args, **kwargs):
        raise AssertionError('Synchronous model IO is forbidden in this probe')

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        summary = bool(messages and str(messages[0].content).startswith('Summarize completed'))
        if summary:
            self.summary_inputs.append(messages)
            self.summary_options.append(kwargs)
            if self.summary_entered is not None:
                self.summary_entered.set()
            if self.summary_release is not None:
                await self.summary_release.wait()
            item = self.summary_results.pop(0)
        else:
            self.normal_inputs.append(messages)
            if len(self.normal_inputs) == 2 and self.retry_entered is not None:
                self.retry_entered.set()
                await self.retry_release.wait()
            item = self.normal_results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item.model_copy(deep=True))])


def model(*, window=500, output=20, reserve=450, keep=10, summary_output=8,
          summaries=None, answers=None, **kwargs):
    return AsyncContextModel(
        summary_results=[AIMessage(content='brief summary')] if summaries is None else summaries,
        normal_results=[AIMessage(content='answer', response_metadata={'finish_reason': 'stop'})]
                       if answers is None else answers,
        metadata={
            'naiyue_model_limits': {'model_id': 'A', 'context_window_tokens': window,
                'max_output_tokens': output, 'context_window_source': 'config', 'max_output_source': 'config'},
            'naiyue_compaction_reserve': {'tokens': reserve, 'source': 'config'},
            'naiyue_compaction_keep_recent': {'tokens': keep, 'source': 'config'},
            'naiyue_compaction_summary_output': {'tokens': summary_output, 'source': 'config'},
        }, **kwargs,
    )


def history():
    return [HumanMessage(content='PRIVATE_OLD_' + 'x' * 200, id='old-user'),
            AIMessage(content='old answer', id='old-ai'),
            HumanMessage(content='PRIVATE_RECENT_' + 'y' * 100, id='recent-user'),
            AIMessage(content='recent answer', id='recent-ai'),
            HumanMessage(content='new question', id='new-user')]


def overflow_error():
    response = httpx.Response(400, request=httpx.Request('POST', 'https://example.invalid'))
    return BadRequestError('PRIVATE_ERROR', response=response,
                           body={'code': 'context_length_exceeded', 'message': 'PRIVATE_ERROR'})


def graph_for(models, selected=None, project='PROJECT'):
    selected = selected or {'s': 'A'}
    _runtime_registry_1 = prepare_tool_registry([])
    _runtime_names_1 = _runtime_registry_1.names()
    port = SimpleNamespace(
        resolve_model=lambda sid, agent_path='root': (selected[sid], models[selected[sid]]),
        resolve_request_configuration=lambda sid, agent_path='root': (selected[sid], models[selected[sid]], _runtime_names_1),
        resolve_active_tool_names=lambda _sid, agent_path='root': _runtime_names_1,
        resolve_permission_context=fixed_permission_context,
        resolve_prompt_parts=lambda _sid, agent_path='root': [{'id': 'project', 'text': project}] if project else [],
        get_registered_subagents=lambda: (),
        drain_steering=lambda _: [], is_stop_requested=lambda _: False,
        finish_run_boundary=lambda _: (False, []),
        prepare_control_messages=lambda _sid, messages: [HumanMessage(content=text) for text in messages])
    return _assemble_interactive_runtime(
        models['A'], tool_registry=_runtime_registry_1, session_port=port,
        system_prompt='BASE', response_format=None, checkpointer=InMemorySaver())



CONFIG = {'configurable': {'thread_id': 's'}}
CONTEXT = {'session_id': 's', 'run_id': 'r'}


async def execute(graph, messages, mode):
    if mode == 'invoke':
        return await graph.ainvoke({'messages': messages}, context=dict(CONTEXT), config=CONFIG), []
    parts, latest = [], None
    async for part in graph.astream({'messages': messages}, context=dict(CONTEXT), config=CONFIG,
                                   stream_mode=['updates', 'values'], version='v2'):
        parts.append(part)
        if part['type'] == 'values':
            latest = part['data']
    return latest, parts


@pytest.mark.parametrize('mode', ['invoke', 'stream'])
def test_proactive_compaction_prompt_and_checkpoint(mode):
    async def scenario():
        selected = {'s': 'B'}
        a, b = model(window=10000, reserve=20), model()
        graph = graph_for({'A': a, 'B': b}, selected)
        original = history()
        result, parts = await execute(graph, original, mode)
        assert a.normal_inputs == a.summary_inputs == []
        assert len(b.summary_inputs) == len(b.normal_inputs) == 1
        assert b.summary_options[0]['max_tokens'] == 8
        assert 'PRIVATE_OLD_' in b.summary_inputs[0][-1].content
        assert b.normal_inputs[0][0].content == 'BASEPROJECT'
        assert result['messages'][0].additional_kwargs[SUMMARY_MARKER] is True
        assert result['messages'][1:-1] == original[2:]
        assert result['messages'][-1].content == 'answer'
        snapshot = await graph.aget_state(CONFIG)
        assert snapshot.values['messages'] == result['messages']
        snapshots = [s async for s in graph.aget_state_history(CONFIG)]
        assert any(s.values.get('messages') and s.values['messages'][0].id == 'old-user' for s in snapshots)
        if mode == 'stream':
            updates = [part['data'] for part in parts if part['type'] == 'updates']
            assert any('select_model.before_model' in row for row in updates)
            assert any(part['type'] == 'values' and
                       part['data']['messages'][0].additional_kwargs.get(SUMMARY_MARKER) for part in parts)
    asyncio.run(scenario())


@pytest.mark.parametrize('project', [None, 'PROJECT'])
def test_small_request_skips_summary_and_preserves_prompt(project):
    async def scenario():
        chosen = model(window=10000, reserve=20)
        result, _ = await execute(graph_for({'A': chosen}, project=project), [('user', 'small')], 'invoke')
        assert chosen.summary_inputs == []
        assert chosen.normal_inputs[0][0].content == 'BASE' + (project or '')
        assert [m.content for m in result['messages']] == ['small', 'answer']
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['invoke', 'stream'])
def test_explicit_overflow_recovers_once_and_streams_committed_summary(mode, caplog):
    async def scenario():
        chosen = model(window=2000, reserve=20, answers=[overflow_error(), AIMessage(content='recovered')])
        graph = graph_for({'A': chosen})
        with caplog.at_level(logging.INFO, logger='naiyue_test_agent.agent_runtime'):
            result, parts = await execute(graph, history(), mode)
        assert len(chosen.normal_inputs) == 2 and len(chosen.summary_inputs) == 1
        assert chosen.normal_inputs[0][0].content == chosen.normal_inputs[1][0].content == 'BASEPROJECT'
        assert chosen.normal_inputs[1][1].additional_kwargs[SUMMARY_MARKER] is True
        assert result['messages'] == [*chosen.normal_inputs[1][1:], result['messages'][-1]]
        assert result['messages'][-1].content == 'recovered'
        assert (await graph.aget_state(CONFIG)).values['messages'] == result['messages']
        if mode == 'stream':
            values = [part['data'] for part in parts if part['type'] == 'values']
            assert values[-1]['messages'] == result['messages']
        assert 'PRIVATE_' not in caplog.text
    asyncio.run(scenario())


def test_second_overflow_is_not_retried_or_committed():
    async def scenario():
        second = overflow_error()
        chosen = model(window=2000, reserve=20, answers=[overflow_error(), second])
        graph = graph_for({'A': chosen})
        with pytest.raises(BadRequestError) as caught:
            await execute(graph, history(), 'invoke')
        assert caught.value is second
        assert len(chosen.normal_inputs) == 2 and len(chosen.summary_inputs) == 1
        assert [m.id for m in (await graph.aget_state(CONFIG)).values['messages']] == [m.id for m in history()]
    asyncio.run(scenario())


def test_non_overflow_preserves_original_exception_without_summary():
    async def scenario():
        error = RuntimeError('PRIVATE input exceeds the context window')
        chosen = model(window=2000, reserve=20, answers=[error])
        with pytest.raises(RuntimeError) as caught:
            await execute(graph_for({'A': chosen}), history(), 'invoke')
        assert caught.value is error
        assert len(chosen.normal_inputs) == 1 and chosen.summary_inputs == []
    asyncio.run(scenario())


def test_invalid_retry_response_does_not_commit_summary():
    async def scenario():
        chosen = model(window=2000, reserve=20, answers=[overflow_error(),
            AIMessage(content='PRIVATE', response_metadata={'finish_reason': 'content_filter'})])
        graph = graph_for({'A': chosen})
        with pytest.raises(ModelResponseRejected) as caught:
            await execute(graph, history(), 'invoke')
        assert caught.value.reason == 'stop_reason_error'
        assert len(chosen.normal_inputs) == 2 and len(chosen.summary_inputs) == 1
        assert [m.id for m in (await graph.aget_state(CONFIG)).values['messages']] == [m.id for m in history()]
    asyncio.run(scenario())


def test_overflow_without_safe_cut_does_not_send_summary_or_retry():
    async def scenario():
        chosen = model(window=2000, reserve=20, answers=[overflow_error()])
        graph = graph_for({'A': chosen})
        with pytest.raises(ContextCompactionError, match='no_safe_cut'):
            await execute(graph, [('user', 'one indivisible request')], 'invoke')
        assert len(chosen.normal_inputs) == 1 and chosen.summary_inputs == []
    asyncio.run(scenario())


def test_soft_threshold_without_safe_cut_skips_summary_inside_send_budget():
    async def scenario():
        chosen = model()
        original = HumanMessage(content='x' * 240, id='only-user')
        result, _ = await execute(graph_for({'A': chosen}), [original], 'invoke')
        assert result['messages'][:-1] == [original]
        assert chosen.summary_inputs == [] and len(chosen.normal_inputs) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('recovery', [False, True])
def test_summary_wait_can_be_cancelled_without_fallback_or_commit(recovery, caplog):
    async def scenario():
        entered, release, control = asyncio.Event(), asyncio.Event(), asyncio.Event()
        chosen = model(window=2000 if recovery else 500, reserve=20 if recovery else 450,
                       answers=[overflow_error(), AIMessage(content='must not run')] if recovery else None,
                       summary_entered=entered, summary_release=release)
        graph = graph_for({'A': chosen})
        task = asyncio.create_task(execute(graph, history(), 'invoke'))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            control.set()
            assert control.is_set() and not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(chosen.normal_inputs) == int(recovery)
            assert len(chosen.summary_inputs) == 1
            assert [m.id for m in (await graph.aget_state(CONFIG)).values['messages']] == [m.id for m in history()]
            assert not any(getattr(r, 'context_compaction', {}).get('event') == 'context_compaction_fallback'
                           for r in caplog.records)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_cancel_during_retry_propagates_without_third_request():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        chosen = model(window=2000, reserve=20,
                       answers=[overflow_error(), AIMessage(content='must not return')],
                       retry_entered=entered, retry_release=release)
        graph = graph_for({'A': chosen})
        task = asyncio.create_task(execute(graph, history(), 'invoke'))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(chosen.normal_inputs) == 2 and len(chosen.summary_inputs) == 1
            assert [m.id for m in (await graph.aget_state(CONFIG)).values['messages']] == [m.id for m in history()]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
