"""Streaming text, cancellation, failure and request-local SDK borrowing."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_openai.chat_models.base import OpenAIConnectionError
from openai import APIConnectionError
from openai.types.responses import ResponseFunctionToolCall

from naiyue_test_agent.model_access.responses_stream import _ToolArguments
from test_responses_final_arguments import configured, sequence, event, tool_item


@pytest.mark.parametrize('value', ['{"path":', '[]', 'null', '"PRIVATE"'])
def test_final_parameters_are_strict_objects_and_never_echoed(value):
    arguments = _ToolArguments()
    item = ResponseFunctionToolCall(**tool_item(0, value))
    with pytest.raises(ValueError, match='responses_tool_arguments') as caught:
        arguments.finish(0, item)
    assert value not in str(caught.value)


def test_identity_conflict_final_conflict_and_unfinished_calls_fail_closed():
    arguments = _ToolArguments()
    item = ResponseFunctionToolCall(**tool_item(0, '{"path":"left"}'))
    arguments.finish(0, item)
    with pytest.raises(ValueError, match='identity_mismatch'):
        arguments.finish(0, item.model_copy(update={'id': 'wrong'}))
    with pytest.raises(ValueError, match='final_arguments_mismatch'):
        arguments.finish(0, item.model_copy(update={'arguments': '{"path":"right"}'}))
    arguments.accept(SimpleNamespace(type='response.output_item.added', output_index=1,
                                     item=ResponseFunctionToolCall(**tool_item(1, ''))))
    with pytest.raises(ValueError, match='unfinished'):
        arguments.end()


def test_two_final_events_cannot_disagree_about_the_execution_target():
    arguments = _ToolArguments()
    arguments.accept(SimpleNamespace(type='response.function_call_arguments.done',
        output_index=0, item_id='fc_0', arguments='{"path":"left"}'))
    with pytest.raises(ValueError, match='final_arguments_mismatch'):
        arguments.finish(0, ResponseFunctionToolCall(**tool_item(0, '{"path":"right"}')))


@pytest.mark.parametrize('action', ['complete', 'cancel', 'network_error'])
def test_text_is_delivered_before_tool_completion_and_http_always_closes(monkeypatch, tmp_path, action):
    async def run():
        release, waiting, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                text = {'type': 'response.output_text.delta', 'output_index': 0, 'content_index': 0,
                        'item_id': 'text_0', 'delta': 'LIVE_TEXT', 'sequence_number': 1}
                yield ('data: ' + json.dumps(text) + '\n\n').encode()
                waiting.set()
                await release.wait()
                if action == 'network_error':
                    raise httpx.ReadError('synthetic-network-failure')
                for row in sequence('final_only'):
                    yield ('data: ' + json.dumps(row) + '\n\n').encode()
            async def aclose(self):
                closed.set()
        async def transport(_, request):
            return httpx.Response(200, stream=Body(), headers={'content-type': 'text/event-stream'})
        monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
        catalog = configured(tmp_path)
        catalog._prepare_async_clients()
        model = catalog.get('test')
        model.max_retries = model.root_async_client.max_retries = 0
        stream = model.astream([HumanMessage(content='synthetic')])
        try:
            first = await anext(stream)
            assert first.text == 'LIVE_TEXT' and not release.is_set()
            async def rest():
                return [part async for part in stream]
            task = asyncio.create_task(rest())
            await waiting.wait()
            if action == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                if action == 'network_error':
                    with pytest.raises(OpenAIConnectionError) as caught:
                        await task
                    assert isinstance(caught.value.__cause__, APIConnectionError)
                    assert isinstance(caught.value.__cause__.__cause__, httpx.ReadError)
                else:
                    assert any(part.tool_calls for part in await task)
            assert closed.is_set()
        finally:
            await stream.aclose()
            await catalog.aclose()
    asyncio.run(run())


def test_concurrent_calls_do_not_share_parameter_buffers_or_replace_sdk(monkeypatch, tmp_path):
    async def run():
        entered, release = 0, asyncio.Event()
        async def transport(_, request):
            nonlocal entered
            entered += 1
            if entered == 2:
                release.set()
            await release.wait()
            prompt = json.loads(request.content)['input'][0]['content']
            assert prompt in ('A', 'B')
            rows = sequence('final_only')
            for row in rows:
                if row['type'] == 'response.output_item.done':
                    row['item']['arguments'] = json.dumps({'path': prompt})
                if row['type'] == 'response.completed':
                    for item in row['response']['output']:
                        item['arguments'] = json.dumps({'path': prompt})
            return httpx.Response(200, content=''.join('data: ' + json.dumps(r) + '\n\n' for r in rows),
                                  headers={'content-type': 'text/event-stream'})
        monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
        catalog = configured(tmp_path)
        catalog._prepare_async_clients()
        model = catalog.get('test')
        sdk = model.root_async_client
        async def invoke(label):
            chunks = [part async for part in model.astream([HumanMessage(content=label)])]
            return [call['args'] for part in chunks for call in part.tool_calls]
        try:
            a, b = await asyncio.wait_for(asyncio.gather(invoke('A'), invoke('B')), 5)
            assert a == [{'path': 'A'}] * 2 and b == [{'path': 'B'}] * 2
            assert model.root_async_client is sdk
        finally:
            await catalog.aclose()
    asyncio.run(run())
