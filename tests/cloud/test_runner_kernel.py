"""Real create_agent/tool/persistence behavior behind a fixed task Runner."""
import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings
from test_async_application import StreamingToolModel, call


def bootstrap():
    from naiyue_test_agent.contracts.cloud_runner import RunnerBootstrap
    return RunnerBootstrap(task_id=uuid4().hex, project_id=str(uuid4()), input_id='a'*64,
        instance_id=str(uuid4()), generation=1, boot_id=str(uuid4()), state_epoch=str(uuid4()),
        release_id='test-release', endpoint='http://172.18.0.1:28102', token='task-test-only',
        expires_at=9999999999.0)


def request(**changes):
    from naiyue_test_agent.contracts.cloud_runner import RunCommand
    return RunCommand(command_id=str(uuid4()), run_id=uuid4().hex, message='create the file',
        model_id='A', **changes)


def test_runner_tool_loop_durable_history_retry_and_reopen(tmp_path):
    from naiyue_test_agent.cloud_runner.kernel import open_runner
    async def scenario():
        b=bootstrap(); root=tmp_path/'project'; root.mkdir(); state=tmp_path/'state'; state.mkdir(mode=0o700)
        command=request()
        model=StreamingToolModel(replies=[call('write',{'path':'answer.txt','content':'42'}), AIMessage(content='saved')])
        permissions=PermissionSettings(mode='NoAsk',allow=(PermissionRule(tool='write',path='${workspace}/**'),))
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model}),permissions=permissions) as runner:
            assert runner.phase=='ready'
            result=await runner.execute(command)
            assert result['status']=='completed' and (root/'answer.txt').read_text()=='42'
            first=runner.journal.pending()
            assert first and first[-1].payload['record']=='run_finished'
            assert first[-1].payload['command_id']==command.command_id
            messages=[e.payload['message'] for e in first if e.payload['record']=='message']
            assert [m['message_type'] for m in messages]==['human','ai','tool','ai']
            assert await runner.execute(command)==result
            assert len(model.seen)==2 and runner.journal.pending()==first
            with pytest.raises(CloudError,match='command_id_conflict'):
                await runner.execute(command.model_copy(update={'message':'changed'}))
            runner.journal.ack(first[-1].seq)
            assert runner.journal.pending()==[]
        b=b.model_copy(update={'generation':2,'boot_id':str(uuid4())})
        next_model=StreamingToolModel(replies=[AIMessage(content='remembered')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':next_model}),permissions=permissions) as runner:
            assert await runner.execute(command)==result
            assert not next_model.seen
            await runner.execute(request())
            assert any(m.content=='saved' for m in next_model.seen[0])
            assert runner.journal.pending()[-1].seq>first[-1].seq
            assert len(runner.journal.pending())==3  # Two new messages plus terminal; no full-history retransmission.
    asyncio.run(scenario())


@pytest.mark.parametrize('boundary',['journal','archive'])
def test_required_history_failure_rolls_back_session_and_blocks_replay(tmp_path,monkeypatch,boundary):
    from naiyue_test_agent.cloud_runner.kernel import open_runner
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700);b=bootstrap();cmd=request()
        model=StreamingToolModel(replies=[AIMessage(content='result')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            original=OSError('injected private required-save failure')
            def fail(*args,**kwargs):raise original
            if boundary=='journal':monkeypatch.setattr(runner.journal,'finish',fail)
            else:
                import naiyue_test_agent.storage.unit_of_work as module
                monkeypatch.setattr(module,'write_chat_updates',fail)
            with pytest.raises(SessionPersistenceError,match='session_save_unconfirmed') as raised:
                await runner.execute(cmd)
            causes=[];current=raised.value
            while current is not None:causes.append(current);current=current.__cause__
            assert original in causes
            assert runner.store.sessions.get(b.task_id)['state']=='running'
            assert runner.journal.pending()==[]
            assert runner.journal.lookup(cmd.command_id)['status']=='unknown'
            assert (await runner.execute(cmd))['status']=='unknown'
            assert len(model.seen)==1
    asyncio.run(scenario())


def test_missing_model_and_lost_or_wrong_state_fail_closed(tmp_path):
    from naiyue_test_agent.cloud_runner.kernel import open_runner
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700);b=bootstrap()
        async with open_runner(b,root,state) as runner:
            assert runner.phase=='waiting_model'
            with pytest.raises(CloudError,match='model_unavailable'):await runner.execute(request())
        with pytest.raises(CloudError,match='runner_state_identity_mismatch'):
            async with open_runner(b.model_copy(update={'state_epoch':str(uuid4())}),root,state):pass
        (state/'session.sqlite').unlink()
        with pytest.raises(CloudError,match='runner_state_missing'):
            async with open_runner(b,root,state):pass
    asyncio.run(scenario())


def test_attached_files_are_installed_before_model_and_paths_enter_context(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_runner.kernel import open_runner
    from naiyue_test_agent.contracts.cloud_tasks import TaskAttachment
    import naiyue_test_agent.cloud_runner.attachment_download as module
    aid=str(uuid4());installed=[]
    async def receive(kernel,command):
        assert not model.seen
        installed.append(command.command_id)
        return [dict(attachment_id=aid,display_name='report',path='.nyt-input/'+aid)]
    monkeypatch.setattr(module,'receive',receive)
    model=StreamingToolModel(replies=[AIMessage(content='read selected files')])
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        cmd=request(attachments=[TaskAttachment(attachment_id=aid,input_id='c'*64,display_name='report',size=100)])
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            result=await runner.execute(cmd)
            assert result['status']=='completed' and installed==[cmd.command_id]
            assert any('.nyt-input/'+aid in str(message.content) for message in model.seen[0])
    asyncio.run(scenario())
