"""A queued next turn follows confirmed normal completion, never WS/ACK state."""
import asyncio
from uuid import uuid4
import pytest
from test_manual_task_commands import prepared,CommandRemote
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('outcome',['completed','failed','stopped','unconfirmed'])
def test_queue_waits_for_authoritative_result_and_only_completed_sends(tmp_path,outcome):
    from naiyue_test_agent.storage.cloud_message_queue import QueueRepository
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            bid=binding['binding_id'];run='b'*32;remote.task.update(active_run_id=run,last_run_id=run)
            binding=await manager.read(domain,bid,sid)
            body=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],text='next',model_id='A')
            repo=QueueRepository(domain.repo.database);queued=repo.reserve(bid,sid,body,run)
            assert repo.reserve(bid,sid,body,run)==queued
            with pytest.raises(CloudError,match='cloud_queue_full'):
                repo.reserve(bid,sid,body|{'command_id':str(uuid4())},run)
            adapter=CommandRemote(remote);original=adapter.request
            def request(method,path,**kwargs):
                if path.endswith('/results'):return [dict(source_run_id=run,phase='available',outcome=outcome)]
                return original(method,path,**kwargs)
            manager.connections.client=adapter;adapter.settings=remote.settings;adapter.closed=False
            adapter.request=request
            waiting=await manager.queue.advance(domain,queued)
            assert waiting['phase']=='waiting' and adapter.calls==[]
            remote.task.update(active_run_id=None)
            final=await manager.queue.advance(domain,waiting)
            await manager.drain()
            if outcome=='completed':
                assert final['phase']=='sent' and len(adapter.calls)==1
                assert adapter.calls[0][1]['command_id']==queued['message_command_id']
                assert adapter.calls[0][1]['message']=='next'
                assert await manager.queue.advance(domain,final)==final
            else:
                assert final['phase']=='paused' and adapter.calls==[]
            assert repo.read(bid,sid)['phase']==final['phase']
        finally:await manager.close();store.close()
    asyncio.run(scenario())


def test_cancelled_queue_cannot_be_revived_by_old_http_retry(tmp_path):
    from naiyue_test_agent.storage.cloud_message_queue import QueueRepository
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            bid=binding['binding_id'];repo=QueueRepository(domain.repo.database)
            request=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],text='later',model_id='A')
            original=repo.reserve(bid,sid,request,'a'*32)
            cancelled=await manager.queue.cancel(domain,bid,sid,request['command_id'])
            assert cancelled['phase']=='cancelled'
            next_item=repo.reserve(bid,sid,request|{'command_id':str(uuid4()),'text':'new'},'a'*32)
            assert repo.reserve(bid,sid,request,'a'*32)==cancelled
            assert repo.read(bid,sid)==next_item
            assert original['message_command_id']!=next_item['message_command_id']
        finally:await manager.close();store.close()
    asyncio.run(scenario())


def test_queue_rejects_stale_draft_and_old_worker_cannot_advance_new_queue(tmp_path):
    from naiyue_test_agent.storage.cloud_message_queue import QueueRepository
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            bid=binding['binding_id'];repo=QueueRepository(domain.repo.database)
            draft=dict(text='old',model_id='A',expected_draft_revision=0)
            domain.repo.commands.save_draft(bid,sid,draft)
            domain.repo.commands.save_draft(bid,sid,draft|dict(text='new',expected_draft_revision=1))
            request=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],text='old',model_id='A',draft_revision=1)
            with pytest.raises(CloudError,match='cloud_draft_conflict'):repo.reserve(bid,sid,request,'a'*32)
            request['draft_revision']=None
            original=repo.reserve(bid,sid,request,'a'*32)
            cancelled=await manager.queue.cancel(domain,bid,sid,original['command_id'])
            new=repo.reserve(bid,sid,request|dict(command_id=str(uuid4())),'a'*32)
            assert await manager.queue.advance(domain,original)==cancelled
            assert repo.read(bid,sid)==new
        finally:await manager.close();store.close()
    asyncio.run(scenario())


def test_queue_recovers_reserved_unsent_command_and_forbids_uncertain_cancel(tmp_path):
    from naiyue_test_agent.storage.cloud_message_queue import QueueRepository
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            bid=binding['binding_id'];repo=QueueRepository(domain.repo.database)
            request=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],text='later',model_id='A')
            queued=repo.reserve(bid,sid,request,'a'*32)
            child=request|dict(command_id=queued['message_command_id'])
            domain.repo.commands.reserve(bid,sid,'message',child)
            queued=repo.update(queued['command_id'],sid,'dispatching',dispatch_request=child)
            adapter=CommandRemote(remote);manager.connections.client=adapter;adapter.settings=remote.settings;adapter.closed=False
            final=await manager.queue.advance(domain,queued)
            assert final['phase']=='sent' and len(adapter.calls)==1
            repo.update(queued['command_id'],sid,'paused',expected=('sent',))
            with pytest.raises(CloudError,match='cloud_queue_send_unconfirmed'):
                await manager.queue.cancel(domain,bid,sid,queued['command_id'])
        finally:await manager.close();store.close()
    asyncio.run(scenario())
