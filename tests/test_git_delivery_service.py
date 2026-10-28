import asyncio
from contextlib import ExitStack
from uuid import uuid4
import pytest
from git_input_helpers import git,repository
from test_sandbox_sessions import application
from sandbox_session_helpers import SandboxTransport


async def prepare(app,root,tmp_path):
    project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
    sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
    binding=(await host.sessions.status(sid))['execution']['binding_id'];(tmp_path/binding/'app.txt').write_text('agent result\n')
    result=await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
    return project,host,sid,result


def test_formal_delivery_branch_merge_and_discard_keep_review(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project,host,sid,result=await prepare(app,root,tmp_path)
                options=await host.sessions.delivery_options(sid,'c'*32)
                assert options['actions']['merge']['available']
                command=dict(operation_id='d'*32,action='branch',branch='review')
                branch=await host.sessions.deliver(sid,'c'*32,command)
                assert branch['state']=='completed' and not (root/'answer.txt').exists()
                merged=await host.sessions.deliver(sid,'c'*32,dict(operation_id='e'*32,action='merge'))
                assert merged['state']=='completed' and (root/'app.txt').read_text()=='agent result\n'
                assert git(root,'rev-parse','HEAD').decode().strip()==result['receipt']['output_commit']
                discarded=await host.sessions.deliver(sid,'c'*32,dict(operation_id='f'*32,action='discard'))
                assert discarded['state']=='completed'
                assert (await host.sessions.result(sid,'c'*32))['discarded']
                assert '+agent result' in (await host.sessions.result_diff(sid,'c'*32,'app.txt'))['patch']
                assert (await host.sessions.deliver(sid,'c'*32,command))['state']=='completed'
    asyncio.run(scenario())


def test_required_save_failure_reconciles_merge_without_replaying(tmp_path,monkeypatch,caplog):
    from naiyue_test_agent.storage.git_deliveries import GitDeliveries
    from naiyue_test_agent.contracts.git_delivery import GitDeliveryError
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                _,host,sid,result=await prepare(app,root,tmp_path)
                normal=GitDeliveries.complete
                def fail(*args):raise OSError('fixture required save failed')
                monkeypatch.setattr(GitDeliveries,'complete',fail)
                command=dict(operation_id='d'*32,action='merge')
                with pytest.raises(GitDeliveryError,match='git_delivery_unconfirmed'):
                    await host.sessions.deliver(sid,'c'*32,command)
                assert 'complete_save' in caplog.text and 'd'*32 in caplog.text and 'c'*32 in caplog.text
                assert 'fixture required save failed' not in caplog.text
                head=git(root,'rev-parse','HEAD')
                monkeypatch.setattr(GitDeliveries,'complete',normal)
                recovered=await host.sessions.deliver(sid,'c'*32,command)
                assert recovered['state']=='completed' and git(root,'rev-parse','HEAD')==head
                assert len(await host.sessions.delivery_operations(sid,'c'*32))==1
    asyncio.run(scenario())


def test_closed_host_reference_does_not_block_root_delivery(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project,host,sid,_=await prepare(app,root,tmp_path)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                await app.projects.close(project.project_id)
                assert host._lifetime._closed
                result=await app.environments.deliver(binding,'c'*32,dict(operation_id='d'*32,action='branch',branch='after-close'))
                assert result['state']=='completed'
    asyncio.run(scenario())


def test_local_branch_save_failure_reconciles_after_application_restart(tmp_path,monkeypatch):
    from naiyue_test_agent.storage.git_deliveries import GitDeliveries
    from naiyue_test_agent.contracts.git_delivery import GitDeliveryError
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project,host,sid,result=await prepare(app,root,tmp_path)
                command=dict(operation_id='d'*32,action='branch',branch='review/restarted')
                normal=GitDeliveries.complete
                def fail(*args):raise OSError('fixture save failed')
                monkeypatch.setattr(GitDeliveries,'complete',fail)
                with pytest.raises(GitDeliveryError,match='git_delivery_unconfirmed'):
                    await host.sessions.deliver(sid,'c'*32,command)
                monkeypatch.setattr(GitDeliveries,'complete',normal)
                assert git(root,'rev-parse','refs/heads/review/restarted').decode().strip()==result['receipt']['output_commit']
            async with application(tmp_path) as app:
                host=await app.projects.history_host(project.project_id)
                saved=await host.sessions.delivery_operations(sid,'c'*32)
                assert saved[0]['state']=='unknown' and saved[0]['command']=={**command}
                reconciled=await host.sessions.deliver(sid,'c'*32,command)
                assert reconciled['state']=='completed'
    asyncio.run(scenario())
