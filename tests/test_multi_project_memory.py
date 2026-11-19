"""Personal memory remains shared, with sources validated in the calling archive."""
import asyncio
import json

from langchain_core.messages import AIMessage, SystemMessage
import pytest

from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.tools.memory import create_memory_tool
from test_async_application import StreamingToolModel, call
from test_memory_wiki_mutations import args
from test_memory_wiki_pagination import decode_content
from test_memory_tool import feedback
from test_multi_project_runtime import multi_app, projects


def test_personal_memory_shared_projects_chats_and_survives_project_close(monkeypatch, tmp_path):
    async def scenario():
        text = 'Use the virtual environment for testing'
        model = StreamingToolModel(replies=[call('memory', {'request': dict(args(sources=[dict(current_user=True,quote=text)]),scope='personal',scope_source_indexes=[0],proposal=dict(args(sources=[])['proposal'],body=text))}),
            AIMessage(content='saved'), AIMessage(content='understood')])
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_memory_tool()]) as (app, _):
            (pa, a), (_, b) = await projects(app, tmp_path)
            chat = await app.chats.open()
            sa, sb, sc = await a.sessions.create('A'), await b.sessions.create('A'), await chat.sessions.create('A')
            result = await a.runs.start(sa, [('user', text)])
            saved = json.loads(feedback(result)[0].content)
            assert saved['status'] == 'saved'
            a_access, b_access, chat_access = a.memory._access, b.memory._access, chat.memory._access
            assert a_access._personal_store is b_access._personal_store is chat_access._personal_store
            request = dict(action='read',scope='personal',memory_id=saved['item']['memory_id'])
            assert decode_content(b_access.query_wiki(sb,b.project_id,request))['entries'][0]['body'] == text
            item = decode_content(chat_access.query_wiki(sc,None,request))['entries'][0]
            assert item['body'] == text and item['sources'][0]['available'] is False
            await app.projects.close(pa.project_id)
            assert not chat_access._personal_store.closed
            await chat.runs.start(sc, [('user', 'Remember my testing preference')])
            system=''.join(m.content for m in model.seen[-1] if isinstance(m,SystemMessage))
            assert 'Saved memory Wiki navigation' in system and text not in system
        assert chat_access._personal_store.closed
    asyncio.run(scenario())


def test_chats_write_personal_and_reject_project_memory(monkeypatch, tmp_path):
    async def scenario():
        text = 'Always answer in Chinese'
        model = StreamingToolModel(replies=[call('memory', {'request': dict(args(sources=[dict(current_user=True,quote=text)]),scope='personal',scope_source_indexes=[0],proposal=dict(args(sources=[])['proposal'],body=text))}),
            AIMessage(content='saved')])
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_memory_tool()]) as (app, _):
            chat = await app.chats.open()
            sid = await chat.sessions.create('A')
            result = await chat.runs.start(sid, [('user', text)])
            saved=json.loads(feedback(result)[0].content)
            assert saved['status']=='saved'
            access = chat.memory._access
            item = decode_content(access.query_wiki(sid,None,dict(action='read',scope='personal',memory_id=saved['item']['memory_id'])))['entries'][0]
            assert item['scope'] == 'personal' and item['sources'][0]['source_workspace_id'] == 'chats'
            assert item['sources'][0]['available'] is True
            with pytest.raises(MemoryReadError, match='memory_scope_mismatch'):
                access.query_wiki(sid,None,dict(action='list_topics',scope='workspace'))
            with pytest.raises(ProjectError, match='project_execution_scope_mismatch'):
                await chat.sessions.create_workspace('A', tmp_path)
            assert not (tmp_path / '.naiyue').exists()
    asyncio.run(scenario())


def test_chat_memory_injection_obeys_fixed_deny(monkeypatch, tmp_path):
    from test_memory_wiki_storage import page
    from memory_wiki_helpers import record
    import time
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='hello')])
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_memory_tool()],
                permissions={'deny': [{'tool': 'memory'}]}) as (app, _):
            chat = await app.chats.open()
            access = chat.memory._access
            p=page();now=time.time()
            with access._personal_store.repository().wiki_edit('personal',None,now=now,capacity=30) as writer:
                writer.apply(pages=[p],memories=[record(p['page_id'],scope='personal',workspace_id=None,body='PRIVATE SAVED PREFERENCE',expires_at=now+86400)])
            sid = await chat.sessions.create('A')
            await chat.runs.start(sid, [('user', 'hello')])
            assert 'PRIVATE SAVED PREFERENCE' not in str(model.seen)
            assert 'Saved memory Wiki navigation' not in str(model.seen)
    asyncio.run(scenario())


def test_chat_memory_injection_requires_active_memory_tool(monkeypatch, tmp_path):
    from test_memory_wiki_storage import page
    from memory_wiki_helpers import record
    import time
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='hello')])
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_memory_tool()]) as (app, _):
            chat = await app.chats.open()
            access = chat.memory._access
            p=page();now=time.time()
            with access._personal_store.repository().wiki_edit('personal',None,now=now,capacity=30) as writer:
                writer.apply(pages=[p],memories=[record(p['page_id'],scope='personal',workspace_id=None,body='PRIVATE SAVED PREFERENCE',expires_at=now+86400)])
            sid = await chat.sessions.create('A')
            await chat.sessions.select_tools(sid, [])
            await chat.runs.start(sid, [('user', 'hello')])
            assert 'PRIVATE SAVED PREFERENCE' not in str(model.seen)
            assert 'Saved memory Wiki navigation' not in str(model.seen)
    asyncio.run(scenario())
