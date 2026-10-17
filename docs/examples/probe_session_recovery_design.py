"""Spec092 framework probes only; no production recovery API is implemented.

Run from the repository root:
PYTHONPATH=tests:. .venv/bin/python docs/examples/probe_session_recovery_design.py
All storage, file writes and models are temporary/local fixtures.
"""

import asyncio
import importlib
import json
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pytest import MonkeyPatch

from naiyue_test_agent.application.session_approval import pending_tool_approval
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool
from test_application_run_events import ToolModel
from test_persistent_sessions import build


def probe_continue():
    with TemporaryDirectory() as directory, MonkeyPatch.context() as patch:
        root = Path(directory) / 'work'
        root.mkdir()
        outside = Path(directory) / 'outside.txt'
        outside.write_text('fixture')
        database = Path(directory) / 'state.sqlite'
        read_module = importlib.import_module('naiyue_test_agent.tools.read')
        original_read = read_module._read
        calls = []

        def record_read(*args, **kwargs):
            calls.append(True)
            return original_read(*args, **kwargs)

        patch.setattr(read_module, '_read', record_read)
        with SessionStorage(database) as storage:
            model = ToolModel(responses=[AIMessage(content='', tool_calls=[
                {'name': 'read', 'args': {'path': str(outside)}, 'id': 'old-call'},
            ])])
            app = build(patch, storage, model, [create_session_read_tool()])
            session = app.create_workspace_session('A', root)
            app.run(session, [HumanMessage(content='fixture request')])
            thread = storage.get(session)['thread_id']
        with SessionStorage(database) as storage:
            app = build(patch, storage, tools=[create_session_read_tool()])
            agent = app._agent
            config = {'configurable': {'thread_id': thread}}
            old = agent.get_state(config)
            assert old.next and any(task.interrupts for task in old.tasks)
            record = {'version': 1, 'recovery_id': 'fixture-recovery',
                      'source_checkpoint_id': old.config['configurable']['checkpoint_id']}

            def marked(config, phase):
                return {**config, 'metadata': {'naiyue_recovery': json.dumps({**record, 'phase': phase})}}

            cleared = agent.update_state(marked(config, 'cleared'), None, as_node=END)
            assert not agent.get_state(cleared).next
            paired = agent.update_state(marked(cleared, 'paired'), {
                'messages': [ToolMessage(content='Previous result unconfirmed; inspect effects before retrying.',
                    tool_call_id='old-call', name='read', status='error', id='recovery-old-call')],
                'tool_execution': {}, 'run_stopped': False,
            }, as_node='tools')
            final_config = agent.update_state(marked(paired, 'finished'), None, as_node=END)
            final = agent.get_state(final_config)
            assert not final.next and len(final.values['messages']) == 3
            assert json.loads(final.metadata['naiyue_recovery'])['phase'] == 'finished'
            assert agent.get_state(old.config).values['messages'] == old.values['messages']
            storage.finish_run(session, 'ready', final.config['configurable']['checkpoint_id'], '', 'recovered')
        with SessionStorage(database) as storage:
            app = build(patch, storage, tools=[create_session_read_tool()])
            app.restore_session(session)
            result = app.run(session, [HumanMessage(content='inspect only')])
            assert result['messages'][-1].content == 'answer'
            assert sum(isinstance(message, ToolMessage) for message in result['messages']) == 1
            assert calls == []
    print('continue_probe=PASS reopened=True marker_persisted=True continuation=True old_tool_calls=0')


class ParallelState(TypedDict):
    messages: Annotated[list, add_messages]


async def probe_pending_writes():
    with TemporaryDirectory() as directory:
        database = str(Path(directory) / 'parallel.sqlite')
        done = asyncio.Event()
        counts = {'fast': 0, 'failed': 0}

        async def fast(_state):
            counts['fast'] += 1
            done.set()
            return {'messages': [ToolMessage(content='saved-success', tool_call_id='one', id='result-one')]}

        async def failed(_state):
            await done.wait()
            counts['failed'] += 1
            raise RuntimeError('fixture')

        builder = StateGraph(ParallelState)
        builder.add_node('fast', fast)
        builder.add_node('failed', failed)
        for name in ('fast', 'failed'):
            builder.add_edge(START, name)
            builder.add_edge(name, END)
        config = {'configurable': {'thread_id': 'probe'}}
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            agent = builder.compile(checkpointer=saver)
            try:
                await agent.ainvoke({'messages': [AIMessage(content='', id='assistant', tool_calls=[
                    {'name': 'x', 'args': {}, 'id': 'one'}, {'name': 'y', 'args': {}, 'id': 'two'},
                ])]}, config)
            except RuntimeError as error:
                assert str(error) == 'fixture'
            else:
                raise AssertionError('Expected fixture failure')
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            agent = builder.compile(checkpointer=saver)
            old = await agent.aget_state(config)
            raw_before = await saver.aget_tuple(old.config)
            assert old.next
            marker = {'metadata': {'naiyue_recovery': '{"phase":"cleared","version":1}'}}
            cleared = await agent.aupdate_state({**config, **marker}, None, as_node=END)
            snapshot = await agent.aget_state(cleared)
            assert not snapshot.next
            assert any(message.content == 'saved-success' for message in snapshot.values['messages'])
            assert json.loads(snapshot.metadata['naiyue_recovery'])['phase'] == 'cleared'
            paired = await agent.aupdate_state(cleared, {'messages': [
                ToolMessage(content='outcome unknown', tool_call_id='two', status='error', id='recovery-two'),
            ]}, as_node='fast')
            final_config = await agent.aupdate_state(paired, None, as_node=END)
            final = await agent.aget_state(final_config)
            raw_after = await saver.aget_tuple(old.config)
            assert not final.next and len(final.values['messages']) == 3
            assert raw_before.checkpoint == raw_after.checkpoint
            assert counts == {'fast': 1, 'failed': 1}
    print('pending_writes_probe=PASS async=True saved_success_retained=True original_checkpoint_unchanged=True no_replay=True')


def probe_approval_transport():
    """Manually rebuild memory only to test existing resume transport, not new policy validation."""
    with TemporaryDirectory() as directory, MonkeyPatch.context() as patch:
        root = Path(directory) / 'work'
        root.mkdir()
        (root / 'inside.txt').write_text('inside')
        target = Path(directory) / 'outside.txt'
        database = Path(directory) / 'approval.sqlite'
        calls = []
        for name, function in (('read', '_read'), ('write', '_write')):
            module = importlib.import_module('naiyue_test_agent.tools.' + name)
            original = getattr(module, function)

            def recorded(*args, _original=original, _name=name, **kwargs):
                calls.append(_name)
                return _original(*args, **kwargs)

            patch.setattr(module, function, recorded)
        tools = [create_session_read_tool(), create_session_write_tool()]
        with SessionStorage(database) as storage:
            model = ToolModel(responses=[AIMessage(content='', tool_calls=[
                {'name': 'read', 'args': {'path': 'inside.txt'}, 'id': 'read-call'},
                {'name': 'write', 'args': {'path': str(target), 'content': 'written'}, 'id': 'write-call'},
            ])])
            app = build(patch, storage, model, tools)
            session = app.create_workspace_session('A', root)
            result = app.run(session, [('user', 'fixture request')])
            assert len(result['approval_requests']) == 1 and not target.exists()
            assert calls == []
            assert not any(isinstance(message, ToolMessage) for message in result['messages'])
            memory = dict(app._sessions._sessions[session])
        with SessionStorage(database) as storage:
            app = build(patch, storage, tools=tools)
            row = storage.get(session)
            snapshot = app._agent.get_state({'configurable': {'thread_id': row['thread_id']}})
            interrupts = tuple(item for task in snapshot.tasks for item in task.interrupts)
            pending = pending_tool_approval({**snapshot.values, '__interrupt__': interrupts})
            pending['requested_run_id'] = row['run_id']
            memory.update(pending_tool_approval=pending, pending_turn=None, session_allow=[],
                          steering_queue=deque(), follow_up_queue=deque(), busy=False, run_id=None)
            app._sessions._sessions[session] = memory
            result = app.resume_tool_approval(session, ['approve'])
            assert target.read_text() == 'written' and result['messages'][-1].content == 'answer'
            results = [message for message in result['messages'] if isinstance(message, ToolMessage)]
            assert [message.tool_call_id for message in results] == ['read-call', 'write-call']
            assert calls == ['read', 'write']
    print('approval_transport_probe=PASS reopened=True mixed_sequential_batch=True no_early_tools=True new_grants=True')


if __name__ == '__main__':
    probe_continue()
    asyncio.run(probe_pending_writes())
    probe_approval_transport()
