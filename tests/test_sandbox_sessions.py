"""Official session creation and run paths retain their original sandbox binding."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport,OfflineModel


def application(tmp_path,mode='BypassApprovals',*,roles=()):
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')},
        'memory':{'auto_enabled':False},'sandbox':{'image':'fixture:image'},'permissions':{'mode':mode},'subagents':roles})
    return open_service_application('unused',tools=create_builtin_tools(settings.tools),system_prompt='',response_format=None,settings=settings)


def test_session_creation_is_idempotent_and_formal_run_writes_only_sandbox(tmp_path,monkeypatch):
    root=repository(tmp_path)
    model=OfflineModel(responses=[AIMessage(content='',tool_calls=[dict(name='write',
        args=dict(path='same.txt',content='sandbox'),id='C')]),AIMessage(content='done')])
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack,model)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                request=str(uuid4())
                sid=await host.sessions.create_sandbox('fake',request_id=request)
                assert await host.sessions.create_sandbox('fake',request_id=request)==sid
                assert len(transport.created)==1
                status=await host.sessions.status(sid)
                assert status['execution']['kind']=='sandbox' and status['execution']['state']=='ready'
                assert status['workspace_root'] is None
                await host.runs.start(sid,[('user','write selected environment')])
                assert not (root/'same.txt').exists()
                binding=status['execution']['binding_id']
                assert (tmp_path/binding/'same.txt').read_text()=='sandbox'
                transport.expired.add(binding)
                with pytest.raises(SandboxError,match='env_expired'):
                    await host.runs.start(sid,[('user','must fail')])
                assert not (root/'same.txt').exists()
                transport.expired.clear()
    asyncio.run(scenario())


@pytest.mark.parametrize('offline',[False,True])
def test_root_approval_reopen_uses_original_environment_and_can_cancel_offline(tmp_path,monkeypatch,offline):
    root=repository(tmp_path)
    model=OfflineModel(responses=[AIMessage(content='',tool_calls=[dict(name='write',
        args=dict(path='.reviewed.txt',content='approved'),id='C')]),AIMessage(content='done')])
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack,model)
            async with application(tmp_path,'Default') as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                await host.runs.start(sid,[('user','write')])
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                assert host.runs.pending_tools(sid)
                assert not (tmp_path/binding/'.reviewed.txt').exists()
            async with application(tmp_path,'Default') as app:
                host=await app.projects.history_host(project.project_id)
                if offline:transport.expired.add(binding)
                else:await app.environments.start(binding)
                history=await host.history.session(sid)
                assert history['recovery']['kind']=='pending_approval'
                await host.sessions.recover(sid,checkpoint_id=history['checkpoint_id'],action='restore_approval')
                if offline:
                    batch=host.runs.pending_approvals(sid)[0]
                    await host.runs.stop_approval(sid,approval_batch_id=batch['approval_batch_id'],origin_run_id=batch['origin_run_id'])
                    assert not (tmp_path/binding/'.reviewed.txt').exists()
                    transport.expired.clear()
                else:
                    await host.runs.resume_approval(sid,['approve'])
                    assert (tmp_path/binding/'.reviewed.txt').read_text()=='approved'
                assert not (root/'.reviewed.txt').exists()
    asyncio.run(scenario())


def test_session_with_retained_sandbox_cannot_be_deleted_or_rebound(tmp_path,monkeypatch):
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id);request=str(uuid4())
                sid=await host.sessions.create_sandbox('fake',request_id=request)
                with pytest.raises(SandboxError,match='env_operation_conflict'):
                    await host.sessions.create_sandbox('fake',request_id=request,include_untracked=('new.txt',))
                with pytest.raises(SessionPersistenceError,match='session_environment_in_use'):
                    await host.sessions.delete(sid)
                assert len(transport.created)==1 and (await host.sessions.status(sid))['can_run']
    asyncio.run(scenario())


def test_git_project_memory_and_context_preview_use_sandbox_identity(tmp_path,monkeypatch):
    root=repository(tmp_path)
    skill=root/'.agents'/'skills'/'sample'/'SKILL.md';skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: sample\ndescription: inspect\n---\nBody')
    model=OfflineModel(responses=[AIMessage(content='',tool_calls=[dict(name='memory',
        args=dict(action='list',scope='both'),id='C')]),AIMessage(content='done')])
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack,model)
            async with application(tmp_path) as app:
                project=await app.projects.add_git(root.as_uri(),ref='HEAD',name='git',operation_id='G')
                await app.projects.trust_git(project.project_id,expected_revision=project.revision,operation_id='T')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                result=await host.runs.start(sid,[('user','read memory')])
                from langchain_core.messages import ToolMessage
                messages=result['messages']
                tool=next(item for item in messages if isinstance(item,ToolMessage))
                assert tool.status=='success',tool.content
                view=await host.history.context(sid)
                assert view['current']['basis']=='next_request'
    asyncio.run(scenario())


def test_git_history_has_project_identity_without_host_execution_or_implicit_trust(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_git(root.as_uri(),ref='HEAD',name='remote',operation_id='G')
                host=await app.projects.history_host(project.project_id)
                assert host.project_id==project.project_id
                with pytest.raises(ProjectError,match='project_execution_unavailable'):
                    await host.sessions.create('fake')
                with pytest.raises(SandboxError,match='env_project_unavailable'):
                    await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                assert not (await app.projects.get(project.project_id)).trusted
                await app.projects.trust_git(project.project_id,expected_revision=project.revision,operation_id='trust')
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                assert (await host.sessions.status(sid))['execution']['state']=='ready'
                assert len(transport.created)==1
    asyncio.run(scenario())


def test_rejected_input_preserves_failed_session_without_hiding_other_tasks(tmp_path,monkeypatch):
    root=repository(tmp_path);(root/'.env.local').write_text('fixture')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),include_untracked=['.env.local'])
                status=await host.sessions.status(sid)
                assert status['execution']['state']=='failed' and not status['can_run']
                assert (await host.tasks.list())['items']==[]
                assert transport.created==[]
                input_id=status['execution']['input_id']
                assert app.environments._inputs.get(project.project_id,input_id).state=='failed'
                await host.sessions.delete(sid)
                with pytest.raises(GitInputError,match='git_input_not_found'):
                    app.environments._inputs.get(project.project_id,input_id)
                (root/'.env.local').unlink()
                next_sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                assert (await host.sessions.status(next_sid))['execution']['state']=='ready'
    asyncio.run(scenario())


def test_child_continuation_after_restart_uses_original_sandbox(tmp_path,monkeypatch):
    import json
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.tools import SubAgentSpec
    from test_async_application import StreamingToolModel,call
    from test_subagent_execution import ChildModel
    from test_subagent_continuation import feedback
    root=repository(tmp_path)
    parent=StreamingToolModel(replies=[call('subagent',dict(agent='worker',task='first'),'D'),AIMessage(content='done')])
    child=ChildModel(replies=[call('write',dict(path='child.txt',content='sandbox'),'W'),AIMessage(content='one'),AIMessage(content='two')])
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            monkeypatch.setattr('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':parent,'child':child}))
            roles=(SubAgentSpec('worker','Inspect','Work',('read','write'),model_id='child'),)
            async with application(tmp_path,roles=roles) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                result=await host.runs.start(sid,[('user','delegate')])
                envelope=json.loads(feedback(result).content)
                assert envelope['status']=='completed',envelope
                identity=envelope['agent_id']
                original=host.sessions._persistence.storage.subagents.get_subagent(sid,identity)
                assert original['environment_binding_id']==binding
                assert (tmp_path/binding/'child.txt').read_text()=='sandbox' and not (root/'child.txt').exists()
            async with application(tmp_path,roles=roles) as app:
                host=await app.projects.open(project.project_id)
                await app.environments.start(binding)
                await host.sessions.prepare(sid)
                parent.replies.extend([call('subagent',dict(agent='worker',task='next',agent_id=identity),'D2'),AIMessage(content='done')])
                envelope=json.loads(feedback(await host.runs.start(sid,[('user','next')])).content)
                assert envelope['agent_id']==identity and envelope['report']=='two',envelope
                latest=host.sessions._persistence.storage.subagents.get_subagent(sid,identity)
                assert latest['child_thread_id']==original['child_thread_id'] and latest['environment_binding_id']==binding
                transport.expired.add(binding)
                with pytest.raises(SandboxError,match='env_expired'):await host.runs.start(sid,[('user','no fallback')])
                assert host.sessions._persistence.storage.subagents.get_subagent(sid,identity)['state']=='ready'
                transport.expired.clear()
    asyncio.run(scenario())


def test_environment_management_reserves_session_against_new_run(tmp_path,monkeypatch):
    from naiyue_test_agent.application.errors import SessionBusyError
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                entered,release=asyncio.Event(),asyncio.Event()
                original=app.environments.get
                async def get(binding):
                    entered.set();await release.wait();return await original(binding)
                monkeypatch.setattr(app.environments,'get',get)
                # Use the real action gate; the controlled engine confirms resource removal.
                task=asyncio.create_task(host.sessions.environment_action(sid,'destroy',confirm_destroy=True))
                try:
                    await entered.wait()
                    with pytest.raises(SessionBusyError):await host.runs.start(sid,[('user','must not start')])
                finally:
                    release.set();await task
                assert not host.sessions._sessions.require(sid).is_busy()
    asyncio.run(scenario())


def test_unconfirmed_container_creation_never_creates_a_second_environment(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            from naiyue_test_agent.application.environments import service
            original=service.DockerEnvironment.create
            def lost(engine,row,save):
                original(engine,row,save)
                raise SandboxError('env_operation_unknown')
            monkeypatch.setattr(service.DockerEnvironment,'create',lost)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                key=str(uuid4());sid=await host.sessions.create_sandbox('fake',request_id=key)
                assert not (await host.sessions.status(sid))['can_run']
                assert await host.sessions.create_sandbox('fake',request_id=key)==sid
                assert len(transport.created)==1
                with pytest.raises(SandboxError,match='env_operation_unknown'):await host.sessions.prepare_environment(sid)
                assert len(transport.created)==1
    asyncio.run(scenario())
