"""The one registered memory tool reads the real Wiki API and repository."""
import asyncio
import json
import time
from langchain_core.messages import AIMessage
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.memory import create_memory_tool
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from test_async_application import StreamingToolModel,call
from test_memory_tool import opened,feedback
from memory_wiki_helpers import record
from test_memory_wiki_pagination import decode_content


def test_registered_memory_reads_real_page_without_model_search(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[])
        settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')},'memory':{'auto_enabled':False}})
        async with opened(monkeypatch,tmp_path,model,tools=[create_memory_tool()],settings=settings) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            store=app.host.sessions._persistence.storage
            workspace=store.sessions.get_workspace_binding()['workspace_id']
            now=time.time();store.memory.prepare_wiki(now=now)
            page=WikiWriter.new_page('working_agreements','后台规则','失败处理约定')
            m=record(page['page_id'],workspace_id=workspace,body='失败后跳过，不重试',
                evidence_at=now,expires_at=now+86400,created_at=now,updated_at=now)
            with store.memory.wiki_edit('workspace',workspace,now=now,capacity=30) as writer:
                writer.apply(pages=[page],memories=[m])
            model.replies=[call('memory',{'request':dict(action='read_page',scope='workspace',page_id=page['page_id'])}),AIMessage(content='已读取规则')]
            result=await app.host.runs.start(sid,[('user','读取后台规则')])
            message,=feedback(result)
            assert message.status=='success'
            payload=json.loads(message.content)
            assert payload['complete'] and payload['budget']['estimated_tokens']<=2000
            assert decode_content(payload)['entries'][0]['body']=='失败后跳过，不重试'
            assert store.database.connection.execute('SELECT version,expires_at FROM naiyue_memories').fetchone()[:]==(1,m['row']['expires_at'])
    asyncio.run(scenario())


def test_registered_memory_saves_real_user_source(monkeypatch,tmp_path):
    from test_memory_wiki_mutations import args
    async def scenario():
        settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')},'memory':{'auto_enabled':False}})
        request=args(sources=[dict(current_user=True,quote='以后测试使用.venv')])
        model=StreamingToolModel(replies=[call('memory',{'request':request}),AIMessage(content='完成')])
        async with opened(monkeypatch,tmp_path,model,tools=[create_memory_tool()],settings=settings) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            store=app.host.sessions._persistence.storage;store.memory.prepare_wiki(now=time.time())
            result=await app.host.runs.start(sid,[('user','以后测试使用.venv')])
            message,=feedback(result);assert message.status=='success'
            saved=json.loads(message.content);assert saved['status']=='saved'
            assert saved['item']['ttl_days']==30
            row=store.database.connection.execute('SELECT body,version FROM naiyue_memories').fetchone()
            assert tuple(row)==('使用.venv',1)
            proof=store.database.connection.execute('SELECT user_origin,evidence_format FROM naiyue_memory_sources').fetchone()
            assert tuple(proof)==(1,1)
    asyncio.run(scenario())


import pytest


@pytest.mark.parametrize('mode',['Default','NoAsk','BypassApprovals'])
def test_wiki_writes_obey_deny_before_api(monkeypatch,tmp_path,mode):
    from test_memory_wiki_mutations import args
    from naiyue_test_agent.application.memory.api import MemoryAPI
    seen=[]
    async def forbidden(*a,**kw):seen.append(True);raise AssertionError('denied API was reached')
    monkeypatch.setattr(MemoryAPI,'query',forbidden)
    async def scenario():
        settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(tmp_path/'data')),
            memory=dict(auto_enabled=False),permissions=dict(mode=mode,deny=[dict(tool='memory',args=dict(action='record'))])))
        model=StreamingToolModel(replies=[call('memory',{'request':args(sources=[dict(current_user=True,quote='以后测试使用.venv')])}),AIMessage(content='已拒绝')])
        async with opened(monkeypatch,tmp_path,model,tools=[create_memory_tool()],settings=settings) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            result=await app.host.runs.start(sid,[('user','以后测试使用.venv')])
            assert feedback(result)[0].status=='error'
    asyncio.run(scenario());assert seen==[]


def test_required_source_save_failure_cannot_write_wiki(monkeypatch,tmp_path):
    from test_memory_wiki_mutations import args
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    async def scenario():
        settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(tmp_path/'data')),memory=dict(auto_enabled=False)))
        model=StreamingToolModel(replies=[call('memory',{'request':args(sources=[dict(current_user=True,quote='以后测试使用.venv')])}),AIMessage(content='记录失败')])
        async with opened(monkeypatch,tmp_path,model,tools=[create_memory_tool()],settings=settings) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            storage=app.host.sessions._persistence.storage;storage.memory.prepare_wiki(now=time.time())
            original=storage.conversation.save_chat_updates;seen=[]
            def fail_first(*a,**kw):
                seen.append(True)
                if len(seen)==1:raise SessionPersistenceError('source_save_failed')
                return original(*a,**kw)
            monkeypatch.setattr(storage.conversation,'save_chat_updates',fail_first)
            result=await app.host.runs.start(sid,[('user','以后测试使用.venv')])
            message,=feedback(result)
            assert message.status=='error' and 'memory_source_save_failed' in message.content
            assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0
            assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_pages').fetchone()[0]==0
    asyncio.run(scenario())


@pytest.mark.parametrize('action',['organize','refresh_overview'])
def test_wiki_background_action_registers_real_task_without_claiming_saved(monkeypatch,tmp_path,action):
    async def scenario():
        model=StreamingToolModel(replies=[])
        settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(tmp_path/'data')),memory=dict(auto_enabled=False)))
        async with opened(monkeypatch,tmp_path,model,tools=[create_memory_tool()],settings=settings) as app:
            sid=await app.host.sessions.create_workspace('A',tmp_path)
            store=app.host.sessions._persistence.storage;store.memory.prepare_wiki(now=time.time())
            workspace=store.sessions.get_workspace_binding()['workspace_id']
            p=WikiWriter.new_page('working_agreements','规则','后台行为')
            with store.memory.wiki_edit('workspace',workspace,now=time.time(),capacity=30) as writer:
                writer.apply(pages=[p],memories=[record(p['page_id'],workspace_id=workspace,expires_at=time.time()+86400)])
            request=dict(action=action)
            if action=='organize':request.update(scope='workspace',page_ids=[p['page_id']])
            from test_memory_wiki_worker import finish
            from test_memory_jobs import response
            background=finish() if action=='organize' else response()
            model.replies=[call('memory',{'request':request}),AIMessage(content='已受理'),background]
            result=await app.host.runs.start(sid,[('user','整理记忆')])
            message,=feedback(result);assert message.status=='success'
            accepted=json.loads(message.content);assert accepted['status']=='accepted' and accepted['task_id']
            jobs=app.host.sessions._sessions.memory_jobs;await jobs.wait_idle()
            job=jobs._entries[accepted['task_id']]
            assert job.wiki_request['action']==action
            assert job.result_status=='completed'
            assert job.saved==0 and job.overview_saved==(0 if action=='organize' else 1)
            assert len(model.seen)==3
    asyncio.run(scenario())


@pytest.mark.parametrize('deny_args', [None, {'scope': 'personal'}, {'scope': 'personal', 'action': 'read_page'}])
def test_link_metadata_respects_target_read_permission(monkeypatch, tmp_path, deny_args):
    async def scenario():
        rules = [] if deny_args is None else [dict(tool='memory', args=deny_args)]
        settings = AppSettings.model_validate(dict(
            storage=dict(data_dir=str(tmp_path / 'data')), memory=dict(auto_enabled=False),
            permissions=dict(deny=rules)))
        model = StreamingToolModel(replies=[])
        async with opened(monkeypatch, tmp_path, model, tools=[create_memory_tool()], settings=settings) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            store = app.host.sessions._persistence.storage
            access = app.host.memory._access
            workspace = store.sessions.get_workspace_binding()['workspace_id']
            now = time.time()
            store.memory.prepare_wiki(now=now)
            personal = access._personal_store.repository()
            personal.prepare_wiki(now=now)
            private = WikiWriter.new_page('collaboration', 'Private title', 'Private description')
            project = WikiWriter.new_page('collaboration', 'Project title', 'Project description')
            with personal.wiki_edit('personal', None, now=now, capacity=30) as writer:
                writer.apply(pages=[private], memories=[record(
                    private['page_id'], scope='personal', workspace_id=None, expires_at=now + 86400)])
            targets = [dict(scope='personal', page_id=private['page_id'], description='Link', workspace_id=None)]
            with store.memory.wiki_edit('workspace', workspace, now=now, capacity=30) as writer:
                writer.apply(pages=[project], memories=[record(
                    project['page_id'], workspace_id=workspace, expires_at=now + 86400)],
                    links=[dict(page_id=project['page_id'], targets=targets)], external_targets=targets)
            model.replies = [
                call('memory', {'request': dict(action='read_page', scope='personal', page_id=private['page_id'])}, identity='personal'),
                call('memory', {'request': dict(action='read_page', scope='workspace', page_id=project['page_id'])}, identity='workspace'),
                AIMessage(content='完成'),
            ]
            result = await app.host.runs.start(sid, [('user', '读取页面')])
            direct, linked = feedback(result)
            assert direct.status == ('success' if deny_args is None else 'error')
            assert linked.status == 'success'
            links = decode_content(json.loads(linked.content))['links']
            if deny_args is None:
                assert [(link['title'], link['description']) for link in links] == [
                    ('Private title', 'Private description')]
            else:
                assert links == []
                assert private['page_id'] not in linked.content
    asyncio.run(scenario())


@pytest.mark.parametrize('action, selectors', [
    ('list_topics', {}), ('list_pages', {'topic': 'working_agreements'}),
    ('list', {}), ('search', {'query': 'Pytest'}),
])
def test_combined_reads_never_query_a_denied_scope(monkeypatch, tmp_path, action, selectors):
    async def scenario():
        settings = AppSettings.model_validate(dict(
            storage=dict(data_dir=str(tmp_path / 'data')),
            memory=dict(auto_enabled=False),
            permissions=dict(deny=[dict(tool='memory', args=dict(scope='personal', action=name))
                                   for name in sorted({action, 'list_topics'})]),
        ))
        model = StreamingToolModel(replies=[
            call('memory', {'request': dict(action=action, scope='both', **selectors)}),
            AIMessage(content='完成'),
        ])
        async with opened(monkeypatch, tmp_path, model, tools=[create_memory_tool()], settings=settings) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            store = app.host.sessions._persistence.storage
            workspace = store.sessions.get_workspace_binding()['workspace_id']
            now = time.time()
            store.memory.prepare_wiki(now=now)
            page = WikiWriter.new_page('working_agreements', 'Pytest project', '项目约定')
            with store.memory.wiki_edit('workspace', workspace, now=now, capacity=30) as writer:
                writer.apply(pages=[page], memories=[record(page['page_id'], workspace_id=workspace,
                    title='Pytest project', body='Pytest project', expires_at=now + 86400)])
            access = app.host.memory._access
            opened_personal = []
            original = access._personal_store.repository

            def tracked():
                opened_personal.append(True)
                return original()

            # The direct call is the only operation in this run that could access the personal store.
            monkeypatch.setattr(access._personal_store, 'repository', tracked)
            result = await app.host.runs.start(sid, [('user', '查看当前范围')])
            message, = feedback(result)
            assert message.status == 'success'
            payload = json.loads(message.content)
            assert set(payload['generation_id']) == {'workspace'}
            key = {'list_topics': 'topics', 'list_pages': 'pages'}.get(action, 'entries')
            entries = decode_content(payload)[key]
            assert entries and {entry['scope'] for entry in entries} == {'workspace'}
            assert not opened_personal

    asyncio.run(scenario())
