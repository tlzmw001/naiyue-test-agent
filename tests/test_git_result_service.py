"""Session-owned result retrieval, retry and durable publication boundaries."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import pytest

from git_input_helpers import git,repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_sessions import application
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_session_result_can_be_read_after_sandbox_disappears(tmp_path,monkeypatch):
    root=repository(tmp_path);(root/'app.txt').write_text('user input\n')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                (tmp_path/binding/'answer.txt').write_text('new output\n')
                options=await host.sessions.result_options(sid)
                assert options['untracked']==['answer.txt']
                result=await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=['answer.txt'])
                assert result['state']=='ready' and [row['path'] for row in result['files']]==['answer.txt']
                transport.expired.add(binding)
                assert (await host.sessions.result_diff(sid,'c'*32,'answer.txt'))['patch'].endswith('+new output\n')
                assert (await host.sessions.result(sid,'c'*32))['receipt']==result['receipt']
                assert [row['result_id'] for row in await host.sessions.results(sid)]==['c'*32]
                assert (root/'app.txt').read_text()=='user input\n' and not (root/'answer.txt').exists()
                transport.expired.clear()
    asyncio.run(scenario())


def test_delivery_retry_reads_original_capture_without_resubmitting(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                (tmp_path/binding/'app.txt').write_text('first result\n')
                engine=app.environments._service._engine(app.environments._service.get(binding))
                engine_type=type(engine);normal=engine_type.request;calls=[];fail=True
                def request(self,row,operation,params,**kwargs):
                    nonlocal fail
                    calls.append(operation)
                    if operation=='result_chunk' and fail:
                        fail=False;raise SandboxError('env_unreachable')
                    return normal(self,row,operation,params,**kwargs)
                monkeypatch.setattr(engine_type,'request',request)
                with pytest.raises(SandboxError,match='env_unreachable'):
                    await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
                (tmp_path/binding/'app.txt').write_text('later result\n')
                result=await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
                assert result['state']=='ready' and calls.count('operation_submit')==1
                patch=await host.sessions.result_diff(sid,'c'*32,'app.txt')
                assert '+first result' in patch['patch'] and '+later result' not in patch['patch']
                with pytest.raises(SandboxError,match='env_operation_conflict'):
                    await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=['another'])
    asyncio.run(scenario())


def test_result_publication_save_failure_reconciles_same_ref(tmp_path,monkeypatch):
    from naiyue_test_agent.storage.sandbox_operations import SandboxOperations
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id'];(tmp_path/binding/'app.txt').write_text('changed\n')
                normal=SandboxOperations.save
                def save(repo,bid,ident,changes):
                    if changes.get('metadata',{}).get('delivery',{}).get('state')=='ready':raise OSError('fixture disk error')
                    return normal(repo,bid,ident,changes)
                monkeypatch.setattr(SandboxOperations,'save',save)
                with pytest.raises(GitInputError,match='git_result_io_failed'):
                    await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
                imported=git(root,'rev-parse','refs/naiyue/tasks/'+'c'*32)
                monkeypatch.setattr(SandboxOperations,'save',normal)
                result=await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
                assert result['state']=='ready' and result['receipt']['output_commit']==imported.decode().strip()
    asyncio.run(scenario())


def test_unknown_capture_is_not_replayed_and_does_not_become_process_custody(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id'];calls=[]
                def unknown(*args):calls.append(args);raise OSError('fixture interrupted capture')
                monkeypatch.setattr(transport.remotes[binding].results,'capture',unknown)
                for _ in range(2):
                    with pytest.raises(GitInputError,match='git_result_unconfirmed'):
                        await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
                assert len(calls)==1
                assert (await host.sessions.result(sid,'c'*32))['operation_state']=='unknown'
    asyncio.run(scenario())


def test_git_source_result_stays_in_application_mirror(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project=await app.projects.add_git(root.as_uri(),ref='HEAD',name='remote',operation_id='G')
                await app.projects.trust_git(project.project_id,expected_revision=project.revision,operation_id='T')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id'];(tmp_path/binding/'app.txt').write_text('remote result\n')
                result=await host.sessions.capture_result(sid,result_id='c'*32,include_untracked=[])
                assert result['state']=='ready'
                assert '+remote result' in (await host.sessions.result_diff(sid,'c'*32,'app.txt'))['patch']
                assert git(root,'for-each-ref','refs/naiyue/tasks/')==b''
                assert (root/'app.txt').read_text()=='base\n'
    asyncio.run(scenario())
