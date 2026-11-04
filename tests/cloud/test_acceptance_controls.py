"""S09.1 regressions: local admission and heartbeat business revisions."""
import asyncio
from test_manual_task_commands import prepared, CommandRemote
from test_manual_task_bindings import uid
from test_runner_control import prepared as runner_prepared, client, connect, hello
from lifecycle_helpers import config
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime


def test_result_pending_does_not_block_followup(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            remote.task['reclaim_blockers']=['background_commands','result_pending']
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
            request=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],
                text='continue',model_id='A',draft_revision=None)
            op=await manager.commands.submit(domain,binding['binding_id'],'message',request);await manager.drain()
            assert domain.repo.operation(op['command_id'],sid)['phase']=='accepted'
            assert len(adapter.calls)==1
        finally:
            await manager.close();store.close()
    asyncio.run(scenario())


def test_unchanged_heartbeat_preserves_revision_and_quiet_start(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=runner_prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));fence={k:v for k,v in ws.receive_json().items() if k!='kind'}
            before=r.registry.get_task(b.task_id)
            for _ in range(2):
                ws.send_json(dict(kind='heartbeat',quiescent=True,phase='ready',run_id=None,**fence))
                assert ws.receive_json()['kind']=='welcome'
                after=r.registry.get_task(b.task_id)
                assert after.revision==before.revision
                assert after.last_quiescent_at==before.last_quiescent_at
            ws.send_json(dict(kind='heartbeat',quiescent=False,phase='ready',run_id=None,**fence));ws.receive_json()
            after=r.registry.get_task(b.task_id)
            assert after.revision==before.revision+1 and 'background_commands' in after.reclaim_blockers
