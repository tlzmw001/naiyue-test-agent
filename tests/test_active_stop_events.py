"""Cancellation preserves safe event labels and still rejects malformed call IDs."""

from native_application_helpers import async_test

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from test_application_stop import ToolCapableFake, build_app
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected


@pytest.mark.parametrize('duplicate', [False, True])
@async_test
async def test_stop_boundary_validates_ids_and_redacts_tool_label(monkeypatch, duplicate):
    holder = {}

    class StopsOnReply(ToolCapableFake):
        def _generate(self, *args, **kwargs):
            holder['app'].host.runs.stop(holder['session'])
            return super()._generate(*args, **kwargs)

    calls = [{'name': '未知工具', 'args': {}, 'id': 'call'}]
    if duplicate:
        calls.append({'name': 'unknown', 'args': {}, 'id': 'call'})
    app = await build_app(monkeypatch, StopsOnReply(responses=[AIMessage(content='', tool_calls=calls)]))
    holder.update(app=app, session=await app.host.sessions.create('A'))
    events = []
    app.host.observability.subscribe(events.append)
    if duplicate:
        with pytest.raises(ModelResponseRejected, match='duplicate_tool_call_ids'):
            await app.host.runs.start(holder['session'], [('user', 'start')])
        assert events[-1].data.status == 'failed'
    else:
        result = await app.host.runs.start(holder['session'], [('user', 'start')])
        assert result['run_stopped']
        message = result['messages'][-1]
        assert isinstance(message, ToolMessage) and message.name == '未知工具'
        call_events = [e for e in events if e.event_type.startswith('tool_call_')]
        assert len(call_events) == 2 and all(e.data.tool_name is None for e in call_events)
