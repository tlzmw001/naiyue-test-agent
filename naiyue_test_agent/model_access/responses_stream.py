"""Finalize Responses function arguments before the pinned LangChain converter.

Only tool argument events are buffered. Text/reasoning/usage still stream through
the official adapter. SDK clients remain borrowed and never mutated or closed here.
"""

import json
from types import SimpleNamespace

from langchain_openai import ChatOpenAI


def _arguments(value):
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        raise ValueError('responses_tool_arguments_invalid') from None
    if not isinstance(parsed, dict):
        raise ValueError('responses_tool_arguments_not_object')
    return parsed


class _ToolArguments:
    def __init__(self):
        self.slots = {}

    def slot(self, index, item_id=None):
        if type(index) is not int or index < 0:
            raise ValueError('responses_tool_index_invalid')
        slot = self.slots.setdefault(index, {})
        if item_id is not None:
            if slot.get('item_id', item_id) != item_id:
                raise ValueError('responses_tool_identity_mismatch')
            slot['item_id'] = item_id
        return slot

    def finish(self, index, item):
        slot = self.slot(index, item.id)
        identity = (item.id, item.call_id, item.name)
        if slot.get('identity', identity) != identity:
            raise ValueError('responses_tool_identity_mismatch')
        slot['identity'] = identity
        value = item.arguments or slot.get('final', slot.get('partial', ''))
        parsed = _arguments(value)
        if slot.get('final') and _arguments(slot['final']) != parsed:
            raise ValueError('responses_tool_final_arguments_mismatch')
        if 'ready' in slot:
            if slot['ready'][1] != parsed:
                raise ValueError('responses_tool_final_arguments_mismatch')
            return []
        slot['ready'] = (item.model_copy(update={'arguments': value}), parsed)
        # One complete call per index avoids both repeated JSON concatenation and
        # index switching in the upstream append-only chunk accumulator.
        events = []
        for position, pending in sorted(self.slots.items()):
            if 'emitted' in pending:
                continue
            if 'ready' not in pending:
                break
            pending['emitted'] = True
            events.append(SimpleNamespace(type='response.output_item.added',
                output_index=position, item=pending['ready'][0]))
        return events

    def accept(self, event):
        kind = event.type
        if kind == 'response.output_item.added' and event.item.type == 'function_call':
            slot = self.slot(event.output_index, event.item.id)
            identity = (event.item.id, event.item.call_id, event.item.name)
            if slot.get('identity', identity) != identity:
                raise ValueError('responses_tool_identity_mismatch')
            slot.update(identity=identity, partial=event.item.arguments)
            return []
        if kind in ('response.function_call_arguments.delta', 'response.function_call_arguments.done'):
            slot = self.slot(event.output_index, getattr(event, 'item_id', None))
            if kind.endswith('.delta'):
                slot['partial'] = slot.get('partial', '') + event.delta
            else:
                parsed = _arguments(event.arguments)
                if ('ready' in slot and slot['ready'][1] != parsed) or (
                        'final' in slot and _arguments(slot['final']) != parsed):
                    raise ValueError('responses_tool_final_arguments_mismatch')
                slot['final'] = event.arguments
            return []
        if kind == 'response.output_item.done' and event.item.type == 'function_call':
            return self.finish(event.output_index, event.item) + [event]
        if kind in ('response.completed', 'response.incomplete'):
            calls = []
            for index, item in enumerate(event.response.output):
                if item.type == 'function_call':
                    calls.extend(self.finish(index, item))
            return calls + [event]
        return [event]

    def end(self):
        if any('emitted' not in slot for slot in self.slots.values()):
            raise ValueError('responses_tool_arguments_unfinished')


class _FinalArgumentsStream:
    def __init__(self, stream):
        self.stream = stream

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    async def __aiter__(self):
        arguments = _ToolArguments()
        async for event in self.stream:
            for normalized in arguments.accept(event):
                yield normalized
        arguments.end()


class _RawResponse:
    def __init__(self, response):
        self.response = response

    def __getattr__(self, name):
        return getattr(self.response, name)

    def parse(self):
        return _FinalArgumentsStream(self.response.parse())


class _ResponsesResource:
    def __init__(self, resource, raw=False):
        self.resource, self.raw = resource, raw

    async def create(self, **kwargs):
        response = await self.resource.create(**kwargs)
        return _RawResponse(response) if self.raw else _FinalArgumentsStream(response)


class _ClientView:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)

    @property
    def responses(self):
        return _ResponsesResource(self.client.responses)

    @property
    def with_raw_response(self):
        return SimpleNamespace(responses=_ResponsesResource(self.client.with_raw_response.responses, raw=True))


class ResponsesChatOpenAI(ChatOpenAI):
    """Request-local stream normalization; all other model operations stay native."""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        local = self
        if self._use_responses_api({**kwargs, **self.model_kwargs}):
            local = self.model_copy(update={'root_async_client': _ClientView(self.root_async_client)})
        async for chunk in super(ResponsesChatOpenAI, local)._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk
