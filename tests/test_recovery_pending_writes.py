"""Actual tools store one task result before another task fails."""

import asyncio

from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict
import pytest

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission, PermissionMechanismError
from test_async_application import StreamingToolModel


def test_recovery_keeps_completed_pending_writes_and_original_task_records(monkeypatch, tmp_path):
    class Input(BaseModel):
        model_config = ConfigDict(strict=True, extra='forbid')
        value: int = 1
    async def scenario():
        saved, calls = asyncio.Event(), []
        @tool(args_schema=Input, response_format='content_and_artifact')
        async def fast(value: int = 1):
            """Return the actual synthetic saved result."""
            calls.append('a')
            return 'actual A', {'record': [1]}
        @tool(args_schema=Input)
        async def failed(value: int = 1):
            """Fail after the other result has reached SQLite."""
            await asyncio.wait_for(saved.wait(), 5)
            calls.append('b')
            raise PermissionMechanismError('fixture')
        specs = [ToolSpec(item, Input, permissions=ToolPermission(default='allow')) for item in (fast, failed)]
        model = StreamingToolModel(replies=[AIMessage(content='', id='ai', tool_calls=[
            {'name': 'fast', 'args': {}, 'id': 'a'}, {'name': 'failed', 'args': {}, 'id': 'b'}])])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        original = AsyncSqliteSaver.aput_writes
        async def writes(self, config, values, *args, **kwargs):
            result = await original(self, config, values, *args, **kwargs)
            if any(channel == 'messages' and isinstance(value, list) and any(
                    isinstance(message, ToolMessage) and message.tool_call_id == 'a' for message in value)
                   for channel, value in values):
                saved.set()
            return result
        monkeypatch.setattr(AsyncSqliteSaver, 'aput_writes', writes)
        def opened():
            return open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite',
                tools=specs, system_prompt='', response_format=None)
        async with opened() as app:
            sid = await app.acreate_session('A')
            with pytest.raises(PermissionMechanismError):
                await app.arun(sid, [('user', 'fixture')])
            row = app._sessions._persistence.storage.get(sid)
        model.seen.clear()
        async with opened() as app:
            config = {'configurable': {'thread_id': row['thread_id']}}
            snapshot = await app._agent.aget_state(config)
            before = await app._agent.checkpointer.aget_tuple(snapshot.config)
            assert any(isinstance(m, ToolMessage) and m.tool_call_id == 'a' for m in snapshot.values['messages'])
            events = []
            app.subscribe_events(events.append)
            await app.arecover_session(sid, checkpoint_id=snapshot.config['configurable']['checkpoint_id'], action='continue')
            final = await app._agent.aget_state(config)
            after = await app._agent.checkpointer.aget_tuple(snapshot.config)
            assert before.checkpoint == after.checkpoint and before.pending_writes == after.pending_writes
            results = [m for m in final.values['messages'] if isinstance(m, ToolMessage)]
            assert [m.tool_call_id for m in results] == ['a', 'b']
            assert results[0].content == 'actual A' and results[0].artifact == {'record': [1]}
            assert results[0].id == next(m.id for m in snapshot.values['messages'] if isinstance(m, ToolMessage))
            assert results[1].status == 'error' and calls == ['a', 'b']
            assert model.seen == [] and events == [] and final.next == () and final.tasks == ()
    asyncio.run(scenario())
