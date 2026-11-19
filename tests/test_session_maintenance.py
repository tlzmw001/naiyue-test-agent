"""Optional maintenance never replaces necessary-save outcomes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


def test_checkpoint_failure_does_not_block_memory(monkeypatch,caplog):
    from naiyue_test_agent.application.sessions.session_maintenance import after_finish
    from naiyue_test_agent.storage import checkpoint_retention
    monkeypatch.setattr(checkpoint_retention,'prune_after_finish',AsyncMock(side_effect=OSError('failed')))
    calls=[]
    memory=SimpleNamespace(maintain_expired=lambda:calls.append('memory'))
    asyncio.run(after_finish(agent=None,storage=None,session_id='s',run_id='r',snapshot=None,state='ready',checkpoint_settings=object(),memory_access=memory))
    assert calls==['memory'] and 'checkpoint_retention_failed' in caplog.text


def test_memory_failure_does_not_replace_success(monkeypatch,caplog):
    from naiyue_test_agent.application.sessions.session_maintenance import after_finish
    def fail():raise OSError('failed')
    asyncio.run(after_finish(agent=None,storage=None,session_id='s',run_id='r',snapshot=None,state='other',checkpoint_settings=None,memory_access=SimpleNamespace(maintain_expired=fail)))
    assert 'memory_retention_failed' in caplog.text


def test_actual_finish_runs_maintenance_only_after_necessary_save(monkeypatch,tmp_path):
    from langchain_core.messages import AIMessage
    from test_history_tool import build
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    from naiyue_test_agent.application.sessions import session_maintenance
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='done'),AIMessage(content='done')])
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[]) as app:
            calls=[]
            async def observe(**kwargs):
                assert app.host.sessions._persistence.storage.sessions.get(kwargs['session_id'])['state']=='ready'
                calls.append(kwargs['session_id'])
            monkeypatch.setattr(session_maintenance,'after_finish',observe)
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            await app.host.runs.start(sid,[('user','first')])
            assert calls==[sid]
            def fail(*a,**kw):raise OSError('injected')
            monkeypatch.setattr(app.host.sessions._persistence.storage.runs,'finish_run',fail)
            with pytest.raises(SessionPersistenceError,match='session_save_unconfirmed'):
                await app.host.runs.start(sid,[('user','second')])
            assert calls==[sid]
    asyncio.run(scenario())


def test_cancellation_waits_for_started_memory_io():
    from threading import Event
    from naiyue_test_agent.application.sessions.session_maintenance import after_finish
    async def scenario():
        entered=Event();release=Event();finished=Event()
        def maintain():
            entered.set();release.wait();finished.set()
        task=asyncio.create_task(after_finish(agent=None,storage=None,session_id='s',run_id='r',snapshot=None,
            state='other',checkpoint_settings=None,memory_access=SimpleNamespace(maintain_expired=maintain)))
        await asyncio.to_thread(entered.wait)
        task.cancel()
        assert not finished.is_set()
        release.set()
        try:await task
        except asyncio.CancelledError:pass
        assert finished.is_set()
    asyncio.run(scenario())


def test_personal_chat_without_jobs_uses_same_maintenance(monkeypatch,tmp_path):
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel
    from test_multi_project_memory import multi_app
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.tools.memory import create_memory_tool
    async def scenario():
        calls=[]
        monkeypatch.setattr(MemoryAccess,'maintain_expired',lambda self:calls.append(self.personal_only))
        model=StreamingToolModel(replies=[AIMessage(content='done')])
        async with multi_app(monkeypatch,tmp_path,model,tools=[create_memory_tool()]) as (app,_):
            chat=await app.chats.open();sid=await chat.sessions.create('A')
            await chat.runs.start(sid,[('user','hello')])
            assert calls==[True]
    asyncio.run(scenario())
