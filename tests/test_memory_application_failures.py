"""Memory observations do not replace required saves or resource cleanup."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.application.errors import SessionPersistenceError
from test_async_application import StreamingToolModel,call,make_app
from test_memory_tool import opened
from test_memory_application import config
from test_memory_jobs import response


def test_required_root_save_failure_never_releases_memory_worker(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',dict(action='refresh_overview')),AIMessage(content='已登记'),response()])
        async with opened(monkeypatch,tmp_path,model,settings=config(tmp_path)) as app:
            sid=await app.sessions.create_workspace('A',tmp_path)
            def fail(*args,**kwargs):raise OSError('PRIVATE_SAVE_DETAIL')
            monkeypatch.setattr(app.sessions._persistence.storage.runs, 'finish_run',fail)
            with pytest.raises(SessionPersistenceError,match='session_save_unconfirmed'):
                await app.runs.start(sid,[('user','登记整理')])
            await app.sessions._sessions.memory_jobs.wait_idle()
            task=(await app.tasks.list(session_id=sid))['items'][0]
            assert task['result_status']=='stopped' and len(model.seen)==2
    asyncio.run(scenario())


def test_memory_close_failure_still_closes_process_owner_and_catalog(monkeypatch):
    async def scenario():
        app=await make_app(monkeypatch,StreamingToolModel(replies=[]))
        calls=[]
        async def memory_close():calls.append('memory');raise OSError('memory cleanup failed')
        async def process_close():calls.append('process')
        async def catalog_close():calls.append('catalog')
        app.sessions._sessions.memory_jobs=SimpleNamespace(aclose=memory_close)
        monkeypatch.setattr(app.sessions._sessions.background_tasks,'aclose',process_close)
        monkeypatch.setattr(app.models._catalog,'aclose',catalog_close)
        with pytest.raises(OSError,match='memory cleanup failed'):await app.aclose()
        assert calls==['memory','process','catalog']
    asyncio.run(scenario())
