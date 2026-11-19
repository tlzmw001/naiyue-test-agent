"""Wiki navigation has an independent finite context budget."""
import json
from test_memory_readonly import memory
from test_memory_wiki_mutations import setup


def test_navigation_contains_only_topic_entries(memory):
    from naiyue_test_agent.application.memory.memory_wiki_context import prepare_wiki_parts
    _,access,workspace=setup(memory)
    parts=prepare_wiki_parts(access,'caller',workspace,allowed=lambda _:True)
    assert len(parts)==1 and parts[0]['id']=='memory'
    assert 'list_pages' in parts[0]['text'] and 'personal_conventions' not in parts[0]['text']


def test_navigation_zero_and_denied(memory):
    from naiyue_test_agent.application.memory.memory_wiki_context import prepare_wiki_parts
    _,access,workspace=setup(memory)
    assert prepare_wiki_parts(access,'caller',workspace,allowed=lambda _:False)==[]
    access.settings=access.settings.model_copy(update={'wiki_navigation_tokens':0})
    assert prepare_wiki_parts(access,'caller',workspace,allowed=lambda _:True)==[]


def test_history_directory_when_memory_disabled_and_preview_is_readonly(monkeypatch,tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage
    from test_history_tool import build
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.tools.history import create_history_tool
    from naiyue_test_agent.settings import AppSettings
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='done'),AIMessage(content='done')])
        settings=AppSettings.model_validate({'memory':{'enabled':False}})
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool()],settings=settings) as app:
            old=await app.host.sessions.create_workspace('A',tmp_path)
            await app.host.runs.start(old,[('user','old')])
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            persistence=app.host.sessions._persistence
            before=persistence.storage.database.connection.total_changes
            parts=await persistence.prepare_memory(sid,tmp_path,[],model,preview=True)
            assert [p['id'] for p in parts]==['session_directory']
            assert old in parts[0]['text']
            assert persistence.storage.database.connection.total_changes==before
            assert sid not in persistence._directory_eligible
            await persistence.prepare_memory(sid,tmp_path,[],model)
            assert sid in persistence._directory_eligible
            persistence.directory_attempt(sid)
            assert await persistence.prepare_memory(sid,tmp_path,[],model,preview=True)==[]
    asyncio.run(scenario())


def test_history_deny_does_not_leak_directory_with_memory_disabled(monkeypatch,tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage
    from test_history_tool import build
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.tools.history import create_history_tool
    from naiyue_test_agent.settings import AppSettings
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='done')])
        settings=AppSettings.model_validate({'memory':{'enabled':False},'permissions':{'deny':[{'tool':'history','args':{'action':'read','agent_id':None}}]}})
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool()],settings=settings) as app:
            old=await app.host.sessions.create_workspace('A',tmp_path)
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            persistence=app.host.sessions._persistence
            assert await persistence.prepare_memory(sid,tmp_path,[],model,preview=True)==[]
            assert sid not in persistence._directory_eligible
    asyncio.run(scenario())


def test_disabled_memory_still_honors_directory_zero_budget(monkeypatch,tmp_path):
    import asyncio
    from test_history_tool import build
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.tools.history import create_history_tool
    from naiyue_test_agent.settings import AppSettings
    async def scenario():
        settings=AppSettings.model_validate({'memory':{'enabled':False,'session_directory_tokens':0}})
        model=StreamingToolModel(replies=[])
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool()],settings=settings) as app:
            await app.host.sessions.create_workspace('A',tmp_path)
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            assert await app.host.sessions._persistence.prepare_memory(sid,tmp_path,[],model,preview=True)==[]
    asyncio.run(scenario())


def test_real_request_contains_wiki_and_history_parts_and_marks_after_save(monkeypatch,tmp_path):
    import asyncio,time
    from langchain_core.messages import AIMessage,SystemMessage
    from test_history_tool import build
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.tools.history import create_history_tool
    from naiyue_test_agent.tools.memory import create_memory_tool
    from naiyue_test_agent.settings import AppSettings
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='done'),AIMessage(content='done')])
        settings=AppSettings.model_validate({'memory':{'auto_enabled':False}})
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool(),create_memory_tool()],settings=settings) as app:
            persistence=app.host.sessions._persistence
            persistence.storage.memory.prepare_wiki(now=time.time())
            old=await app.host.sessions.create_workspace('A',tmp_path)
            await app.host.runs.start(old,[('user','old')])
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            await app.host.runs.start(sid,[('user','new')])
            system='\n'.join(m.content for m in model.seen[-1] if isinstance(m,SystemMessage))
            assert 'Saved memory Wiki navigation' in system and 'Other session directory' in system
            assert 'list_pages' in system and old in system
            assert persistence.storage.memory.progress(sid)[0]['directory_seen']
            assert not any(p['id']=='session_directory' for p in await persistence.prepare_memory(sid,tmp_path,[],model,preview=True))
    asyncio.run(scenario())
