"""Actual async graph, file IO and checkpoint state at a cooperative stop."""

import asyncio
from pathlib import Path
from threading import Event

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import (
    create_session_read_tool, create_session_write_tool, create_session_edit_tool,
)
from naiyue_test_agent.tools import write as write_module
from event_sequence import EventSequenceChecker
from test_async_application import StreamingToolModel
from test_file_tool_stop import ObservedStream


@pytest.mark.parametrize('name', ['read', 'write', 'edit'])
def test_real_file_stop_pairs_error_and_saves_ready_checkpoint(monkeypatch, tmp_path, caplog, name):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        target = root / 'file.txt'
        target.write_text('PRIVATE_original')
        entered, release = Event(), Event()
        if name == 'read':
            original = Path.open
            def opened(path, mode='r', *args, **kwargs):
                source = original(path, mode, *args, **kwargs)
                if path == target and mode == 'rb':
                    def hold(method, size):
                        if size != 32:
                            entered.set()
                            assert release.wait(5)
                    return ObservedStream(source, hold)
                return source
            monkeypatch.setattr(Path, 'open', opened)
        else:
            original_sync = write_module.os.fsync
            def sync(fd):
                original_sync(fd)
                entered.set()
                assert release.wait(5)
            monkeypatch.setattr(write_module.os, 'fsync', sync)
        args = {'path': target.name}
        if name == 'write':
            args['content'] = 'replacement'
        if name == 'edit':
            args['edits'] = [{'oldText': 'PRIVATE_original', 'newText': 'replacement'}]
        chosen = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': name, 'args': args, 'id': 'file'},
            {'name': 'write', 'args': {'path': 'later.txt', 'content': 'later'}, 'id': 'later'},
        ])])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': chosen}))
        tools = [create_session_read_tool(), create_session_write_tool(), create_session_edit_tool()]
        database = tmp_path / 'sessions.sqlite'
        async with open_async_persistent_application('unused', database_path=database,
                tools=tools, system_prompt='', response_format=None) as app:
            sid = await app.acreate_workspace_session('A', root)
            events = []
            app.subscribe_events(events.append)
            task = asyncio.create_task(app.arun(sid, [('user', 'operate on the file')]))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                app.request_stop(sid)
                assert app._sessions.require(sid).describe_session()['busy'] and not task.done()
                release.set()
                result = await asyncio.wait_for(task, 5)
                outputs = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
                assert outputs['file'].status == 'error' and 'stopped' in outputs['file'].content
                assert 'PRIVATE_original' not in outputs['file'].content
                assert outputs['file'].tool_call_id == 'file' and outputs['file'].name == name
                assert outputs['later'].additional_kwargs['naiyue_not_executed'] == 'user_stop'
                assert result['run_stopped'] and len(chosen.seen) == 1
                assert target.read_bytes() == b'PRIVATE_original' and not (root / 'later.txt').exists()
                assert not list(root.glob('.naiyue-write-*'))
                assert not app._sessions.require(sid).describe_session()['busy']
                row = (await app.alist_sessions())[0]
                assert row['state'] == 'ready' and row['last_run_status'] == 'stopped'
                snapshot = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
                assert not snapshot.next and not any(t.error for t in snapshot.tasks)
                assert any(isinstance(m, ToolMessage) and m.tool_call_id == 'file'
                           and m.status == 'error' for m in snapshot.values['messages'])
                assert events[-1].data.status == 'stopped'
                EventSequenceChecker.assert_healthy(events, caplog.records)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        fresh = StreamingToolModel(replies=[AIMessage(content='continued')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': fresh}))
        async with open_async_persistent_application('unused', database_path=database,
                tools=tools, system_prompt='', response_format=None) as app:
            await app.arestore_session(sid)
            result = await app.arun(sid, [('user', 'continue')])
            assert result['messages'][-1].content == 'continued'
            assert len(fresh.seen) == 1 and any(isinstance(m, ToolMessage)
                and m.tool_call_id == 'file' and m.status == 'error' for m in fresh.seen[0])
    asyncio.run(scenario())
