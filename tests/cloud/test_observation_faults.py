"""Deterministic races and failure injection at optional observation boundaries."""
import asyncio
import json

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_observation import ObservationLimits
from lifecycle_helpers import config
from observation_helpers import running,item,frame


def test_concurrent_subscriptions_recheck_capacity_after_snapshot(tmp_path,monkeypatch):
    async def run(r):
        b,c,cmd=await running(r);entered=asyncio.Event();release=asyncio.Event();count=0
        original=r.observations.snapshot
        async def snapshot(*args,**kwargs):
            nonlocal count
            value=await original(*args,**kwargs);count+=1
            if count==2:entered.set()
            await release.wait();return value
        monkeypatch.setattr(r.observations,'snapshot',snapshot)
        jobs=[asyncio.create_task(r.observations.subscribe(b.task_id)) for _ in range(2)]
        await asyncio.wait_for(entered.wait(),5);release.set()
        results=await asyncio.gather(*jobs,return_exceptions=True)
        assert sum(not isinstance(v,BaseException) for v in results)==1
        assert len(r.observations.leases)==1
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path,observation_limits=ObservationLimits(subscribers=1,task_subscribers=1))) as r:
        asyncio.run(run(r))


def test_stale_connection_cannot_publish_after_usage_io_yields(tmp_path,monkeypatch):
    from naiyue_test_agent.application.cloud import observations as module
    async def run(r):
        b,c,cmd=await running(r);entered=asyncio.Event();release=asyncio.Event();original=module.cloud_work
        async def worker(runtime,action,**kwargs):
            if getattr(action,'__name__','')=='<lambda>' and not entered.is_set():
                # Gate only the usage call, after its admission/acceptance.
                if action.__code__.co_names and '_save_usage' in action.__code__.co_names:
                    entered.set();await release.wait()
            return await original(runtime,action,**kwargs)
        monkeypatch.setattr(module,'cloud_work',worker)
        packet=frame(b,c,[item(cmd,'message_delta',dict(message_id='old',text='stale',index=1,end_index=1,reply_id=None))])
        job=asyncio.create_task(r.control.receive(c,packet))
        await asyncio.wait_for(entered.wait(),5)
        r.observations.connected(b.task_id,'replacement')
        release.set()
        with pytest.raises(CloudError,match='runner_connection_stale'):await job
        assert not (await r.observations.snapshot(b.task_id))['live']['messages']
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


def test_tool_collection_and_final_sse_encoding_respect_budgets(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        records=[item(cmd,'tool_progress',dict(tool_call_id=str(i),tool_name='bash',activity='running',phase='work',text='x'*2048)) for i in range(5)]
        await r.control.receive(c,frame(b,c,records))
        state=r.observations.states[b.task_id]
        assert len(json.dumps(state.view(all_threads=True),ensure_ascii=False).encode())<=4096
        assert state.incomplete
        lease=await r.observations.subscribe(b.task_id)
        from naiyue_test_agent.application.cloud.observation_stream import encode_sse
        assert len(encode_sse(await lease.next()))<=r.settings.max_frame_bytes
        await lease.aclose();await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path,observation_limits=ObservationLimits(state_bytes=4096))) as r:
        asyncio.run(run(r))


def test_snapshot_budget_counts_cursor_line_and_reconnect_encoding(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text='x'*2450,index=1,end_index=1,reply_id=None))]))
        lease=await r.observations.subscribe(b.task_id)
        from naiyue_test_agent.application.cloud.observation_stream import encode_sse
        event=await lease.next();assert len(encode_sse(event))<=4096
        await lease.aclose();await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path,max_frame_bytes=4096,observation_limits=ObservationLimits(text_chars=3000))) as r:
        asyncio.run(run(r))


def test_optional_notice_and_usage_errors_do_not_change_owner_run(tmp_path,monkeypatch,caplog):
    async def run(r):
        b,c,cmd=await running(r)
        def fail(*args,**kwargs):raise OSError('synthetic optional IO fault')
        from naiyue_test_agent.storage.cloud.usage import CloudUsage
        monkeypatch.setattr(CloudUsage,'save',fail)
        await r.control.receive(c,frame(b,c,[item(cmd,'message_delta',dict(message_id='a',text='ok',index=1,end_index=1,reply_id=None))]))
        assert (await r.observations.snapshot(b.task_id))['live']['usage_incomplete']
        monkeypatch.setattr(r.observations,'changed',fail)
        from naiyue_test_agent.contracts.cloud_runner import RunnerFrame
        await r.control.receive(c,RunnerFrame.validate_python(dict(kind='heartbeat',**c.fence)))
        assert r.registry.get_command(cmd['command_id']).status=='running'
        assert r.history_page(b.task_id)['last_seq']==0
        assert 'cloud_usage_capture_incomplete' in caplog.text and 'cloud_live_notification_failed' in caplog.text
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


def test_generation_revocation_during_optional_io_is_not_downgraded(tmp_path,monkeypatch):
    from naiyue_test_agent.application.cloud import observations as module
    async def run(r):
        b,c,cmd=await running(r);entered=asyncio.Event();release=asyncio.Event();original=module.cloud_work
        async def worker(runtime,action,**kwargs):
            if '_save_usage' in action.__code__.co_names:
                entered.set();await release.wait()
            return await original(runtime,action,**kwargs)
        monkeypatch.setattr(module,'cloud_work',worker)
        packet=frame(b,c,[item(cmd,'message_delta',dict(message_id='old',text='old generation',index=1,end_index=1,reply_id=None))])
        job=asyncio.create_task(r.control.receive(c,packet));await asyncio.wait_for(entered.wait(),5)
        old=r.registry.get_task(b.task_id)
        r.replace_task(old.model_copy(update={'revision':old.revision+1,'generation':2}),expected_revision=old.revision,expected_generation=old.generation)
        release.set()
        with pytest.raises(CloudError):await job
        assert not (await r.observations.snapshot(b.task_id))['live']['messages']
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


def test_usage_failure_does_not_publish_confirmed_message_as_provisional(tmp_path,monkeypatch):
    from langchain_core.messages import AIMessage
    from test_history_api import publish
    from test_history_projection import record
    from naiyue_test_agent.agent_runtime.message_presentation import message_content_id
    from naiyue_test_agent.storage.cloud.usage import CloudUsage
    async def run(r):
        b,c,cmd=await running(r)
        publish(r,r.registry.get_task(b.task_id),b.token.get_secret_value(),[record(AIMessage(id='done',content='confirmed'))])
        lease=await r.observations.subscribe(b.task_id);await lease.next()
        def fail(*args,**kwargs):raise OSError('synthetic optional usage failure')
        monkeypatch.setattr(CloudUsage,'save',fail)
        row=dict(id='done',type='ai',content='confirmed',content_id=message_content_id(dict(id='done',type='ai',content='confirmed')))
        await r.control.receive(c,frame(b,c,[item(cmd,'message_completed',row)]))
        value=await lease.next()
        assert value['event']=='update' and value['data']['messages']==[]
        await lease.aclose();await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))


def test_optional_usage_reopen_failure_does_not_block_required_history(tmp_path,monkeypatch,caplog):
    from naiyue_test_agent.storage.cloud.usage import CloudUsage
    from test_history_ingress import setup_task,batch
    def fail(*args,**kwargs):raise OSError('synthetic optional reopen fault')
    monkeypatch.setattr(CloudUsage,'reopen',fail)
    with open_cloud_runtime(config(tmp_path)) as r:
        task,token=setup_task(r)
        assert r.append_events(token,batch(task))['last_seq']==1
        assert r.history_page(task.task_id)['last_seq']==1
        assert 'cloud_usage_reopen_incomplete' in caplog.text


def test_new_live_revision_is_visible_but_old_confirmed_context_does_not_resurrect(tmp_path):
    from langchain_core.messages import AIMessage
    from test_history_api import publish
    from test_history_projection import record
    from naiyue_test_agent.agent_runtime.message_presentation import message_content_id
    async def run(r):
        b,c,cmd=await running(r)
        old=record(AIMessage(id='same',content='old confirmed'))
        # A prior run owns the previously confirmed version.
        publish(r,r.registry.get_task(b.task_id),b.token.get_secret_value(),[old])
        changed=item(cmd,'message_revised',dict(id='same',type='ai',content='new unconfirmed',
            content_id=message_content_id(dict(id='same',type='ai',content='new unconfirmed'))))
        await r.control.receive(c,frame(b,c,[changed]))
        snapshot=await r.observations.snapshot(b.task_id)
        assert snapshot['archive']['messages'][0]['content']=='old confirmed'
        assert snapshot['live']['messages'][0]['content']=='new unconfirmed'
        same=item(cmd,'message_completed',dict(id='same',type='ai',content='old confirmed',
            content_id=message_content_id(dict(id='same',type='ai',content='old confirmed'))))
        await r.control.receive(c,frame(b,c,[same],seq=2))
        assert (await r.observations.snapshot(b.task_id))['live']['messages']==[]
        await r.observations.aclose();await r.control.aclose()
    with open_cloud_runtime(config(tmp_path)) as r:asyncio.run(run(r))
