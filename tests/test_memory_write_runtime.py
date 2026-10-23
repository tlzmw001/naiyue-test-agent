"""Formal model/tool/checkpoint flow for immediate explicit memory commits."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel, call
from test_memory_tool import opened, feedback
from test_memory_lifecycle import proposal
from naiyue_test_agent.application.errors import SessionPersistenceError


def record_args(**changes):
    return dict(action='record',scope='workspace',proposal=proposal(),
        sources=[dict(current_user=True,quote='以后测试使用.venv')]) | changes


def test_current_round_user_is_archived_before_commit_and_reopens(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',record_args(),'save'),AIMessage(content='已记录')])
        async with opened(monkeypatch,tmp_path,model) as app:
            identity=app._storage.get_workspace_binding()['workspace_id']
            sid=await app.acreate_workspace_session('A',tmp_path)
            result=await app.arun(sid,[('user','以后测试使用.venv')])
            message,=feedback(result)
            assert message.status=='success'
            item=json.loads(message.content)['item']
            assert item['status']=='active' and item['evidence_at']>0 and len(model.seen)==2
            source=app._storage.connection.execute('SELECT * FROM naiyue_memory_sources').fetchone()
            assert source['session_id']==sid and source['role']=='human' and source['occurred_at']==item['evidence_at']
        model2=StreamingToolModel(replies=[call('memory',dict(action='search',scope='workspace',query='.venv')),AIMessage(content='found')])
        async with opened(monkeypatch,tmp_path,model2,identity=identity) as app:
            sid2=await app.acreate_workspace_session('A',tmp_path)
            result=await app.arun(sid2,[('user','查看记忆')])
            assert item['memory_id'] in feedback(result)[0].content
    asyncio.run(scenario())


def test_necessary_archive_failure_is_error_feedback_no_write_and_agent_continues(monkeypatch,tmp_path,caplog):
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',record_args(),'save'),AIMessage(content='记录失败请重试')])
        async with opened(monkeypatch,tmp_path,model) as app:
            sid=await app.acreate_workspace_session('A',tmp_path)
            original=app._storage.save_chat_updates
            calls=[]
            def fail_once(*args):
                calls.append(True)
                if len(calls)==1:raise SessionPersistenceError('private archive failure')
                return original(*args)
            monkeypatch.setattr(app._storage,'save_chat_updates',fail_once)
            result=await app.arun(sid,[('user','以后测试使用.venv')])
            message,=feedback(result)
            assert message.status=='error' and 'memory_source_save_failed' in message.content
            assert result['messages'][-1].content=='记录失败请重试' and len(model.seen)==2
            assert app._storage.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==0
            assert 'private archive failure' not in message.content+caplog.text
    asyncio.run(scenario())
