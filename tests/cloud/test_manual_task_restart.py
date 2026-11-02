"""Reopened local bindings discover original cloud commands without replay."""
import asyncio
from test_manual_task_commands import prepared,CommandRemote
from test_manual_task_bindings import uid
from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks,LocalTaskDomain
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.lifetime import ApplicationLifetime
from contextlib import AsyncExitStack
from test_manual_task_preparation import Connections


def test_lost_message_ack_reopens_by_binding_index_and_never_resends(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        adapter=CommandRemote(remote);adapter.settings=remote.settings;adapter.lose=True;manager.connections.client=adapter
        req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='only once',model_id='A',draft_revision=None)
        await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
        assert domain.repo.operation(req['command_id'],sid)['phase']=='unknown'
        await manager.close();path=store.path;project=domain.project_id;store.close()
        reopened=ProjectStore(path);other=ManualCloudTasks(manager.settings,book=manager.book,connections=Connections(adapter))
        recovered=LocalTaskDomain(ApplicationLifetime(None,None,AsyncExitStack()),reopened,project)
        try:
            values=await other.commands.list(recovered,binding['binding_id'],sid)
            sent=[v for v in values if v['kind']=='message']
            assert len(sent)==1 and sent[0]['phase']=='accepted' and sent[0]['run_id']==remote.task['active_run_id']
            await other.retry(recovered,sent[0]['command_id'],sid);await other.drain()
            assert len(adapter.calls)==1
            assert reopened.sessions.get(sid)['run_id'] is None
            await other.close()
        finally:reopened.close()
    asyncio.run(scenario())


def test_close_drains_other_dispatches_even_when_one_producer_fails(tmp_path):
    import pytest
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        entered=asyncio.Event();release=asyncio.Event();fail=asyncio.Event();primary=OSError('controlled journal failure')
        async def slow():entered.set();await release.wait()
        async def broken():await fail.wait();raise primary
        try:
            for coroutine in (slow(),broken()):
                key=(domain.project_id,uid());task=asyncio.create_task(coroutine)
                manager.commands.flights[key]=('message',task);domain.own(task)
                task.add_done_callback(lambda done,k=key:manager.commands.flights.pop(k,None))
            closer=asyncio.create_task(manager.close());await entered.wait();fail.set()
            # Wait for the failed producer to settle; the other dispatch is still owned.
            await asyncio.gather(*(v[1] for v in tuple(manager.commands.flights.values()) if v[1].get_coro().__name__=='broken'),return_exceptions=True)
            await asyncio.sleep(0)
            assert not remote.closed and not closer.done()
            release.set()
            with pytest.raises(OSError) as caught:await closer
            assert caught.value is primary and remote.closed
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_cancelled_http_waiter_after_command_admission_still_dispatches(tmp_path,monkeypatch):
    import threading
    import pytest
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path);entered=threading.Event();release=threading.Event()
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
            original=domain.repo.commands.reserve
            def reserve(*args):
                value=original(*args);entered.set();assert release.wait(5);return value
            monkeypatch.setattr(domain.repo.commands,'reserve',reserve)
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='once',model_id='A',draft_revision=None)
            sending=asyncio.create_task(manager.commands.submit(domain,binding['binding_id'],'message',req))
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5);sending.cancel();release.set()
            with pytest.raises(asyncio.CancelledError):await sending
            await manager.drain();assert domain.repo.operation(req['command_id'],sid)['phase']=='accepted'
            assert len(adapter.calls)==1
            await manager.close()
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_missing_original_receipt_remains_unknown_and_is_never_resent(tmp_path):
    import pytest
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;adapter.lose=True;manager.connections.client=adapter
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='once',model_id='A',draft_revision=None)
            await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
            remote.commands.pop(req['command_id'])
            remote.task.update(active_run_id=None,reclaim_blockers=[],revision=remote.task['revision']+1)
            op=await manager.commands.reconcile(domain,req['command_id'],sid)
            assert op['phase']=='unknown'
            await manager.retry(domain,req['command_id'],sid);await manager.drain()
            binding=await manager.read(domain,binding['binding_id'],sid)
            with pytest.raises(CloudError,match='cloud_command_pending'):
                await manager.commands.submit(domain,binding['binding_id'],'message',req|{'command_id':uid(),'expected_binding_revision':binding['revision']})
            assert len(adapter.calls)==1
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())
