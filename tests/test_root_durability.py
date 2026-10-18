"""Public durability reaches every root execution path and gates subsequent steps."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.pregel import Pregel

from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.tools import create_session_read_tool
from native_application_helpers import async_test
from test_async_application import StreamingToolModel, call
from test_async_persistent import factory


@pytest.mark.parametrize('workspace', [False, True])
@pytest.mark.parametrize('path', ['stream', 'unsupported', 'baseline_failure'])
@async_test
async def test_persistent_root_passes_public_sync_to_graph(monkeypatch, tmp_path, workspace, path):
    modes = []
    original_stream = Pregel.astream
    async def stream(self, *args, **kwargs):
        modes.append(kwargs.get('durability'))
        async for part in original_stream(self, *args, **kwargs):
            yield part
    monkeypatch.setattr(Pregel, 'astream', stream)
    async with factory(monkeypatch, tmp_path / 'state.sqlite',
                       StreamingToolModel(replies=[AIMessage(content='saved')])) as app:
        if workspace:
            sid = await app.acreate_workspace_session('A', tmp_path)
        else:
            sid = await app.acreate_session('A')
        if path == 'unsupported':
            original_invoke = app._agent.ainvoke
            async def invoke(*args, **kwargs):
                assert kwargs.get('durability') == 'sync'
                return await original_invoke(*args, **kwargs)
            monkeypatch.setattr(app._agent, 'ainvoke', invoke)
        elif path == 'baseline_failure':
            original_get = app._agent.aget_state
            reads = 0
            async def get(*args, **kwargs):
                nonlocal reads
                reads += 1
                if reads == 1:
                    raise OSError('synthetic baseline failure')
                return await original_get(*args, **kwargs)
            monkeypatch.setattr(app._agent, 'aget_state', get)
        result = await app.arun(sid, [('user', 'run')])
        assert result['messages'][-1].content == 'saved'
        assert modes and all(mode == 'sync' for mode in modes)
        assert (await app.aget_session_status(sid))['state'] == 'ready'


@async_test
async def test_persistent_approval_resume_passes_public_sync(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('approved value')
    model = StreamingToolModel(replies=[call('read', {'path': str(outside)}), AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    modes = []
    original = Pregel.astream
    async def stream(self, *args, **kwargs):
        modes.append(kwargs.get('durability'))
        async for part in original(self, *args, **kwargs):
            yield part
    monkeypatch.setattr(Pregel, 'astream', stream)
    async with open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite',
            tools=[create_session_read_tool()], system_prompt='', response_format=None) as app:
        sid = await app.acreate_workspace_session('A', root)
        pending = await app.arun(sid, [('user', 'read')])
        assert len(pending['approval_requests']) == 1
        result = await app.aresume_tool_approval(sid, ['approve'])
        assert next(m.content for m in result['messages'] if isinstance(m, ToolMessage)) == 'approved value'
        assert result['messages'][-1].content == 'done'
        assert len(modes) == 2 and modes == ['sync', 'sync']


@async_test
async def test_sync_checkpoint_settles_before_next_node_without_publisher():
    entered, release, saved, second_started = (asyncio.Event() for _ in range(4))
    class Saver(InMemorySaver):
        async def aput(self, config, checkpoint, metadata, versions):
            if metadata['step'] == 1:
                entered.set()
                await release.wait()
                result = await super().aput(config, checkpoint, metadata, versions)
                saved.set()
                return result
            return await super().aput(config, checkpoint, metadata, versions)
    async def first(state):
        return {'value': 1}
    async def second(state):
        second_started.set()
        assert saved.is_set(), 'Next node started before the previous checkpoint settled'
        return {'value': 2}
    builder = StateGraph(dict)
    builder.add_node('first', first)
    builder.add_node('second', second)
    builder.add_edge(START, 'first')
    builder.add_edge('first', 'second')
    builder.add_edge('second', END)
    graph = builder.compile(checkpointer=Saver())
    task = asyncio.create_task(ainvoke_agent_run(graph, [], session_id='s', thread_id='t', run_id='r',
                                               durability='sync'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not second_started.is_set()
        release.set()
        assert await task == {'value': 2}
        assert second_started.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
