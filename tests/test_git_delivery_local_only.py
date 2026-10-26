import asyncio
from contextlib import ExitStack
import pytest
from pydantic import ValidationError
from git_input_helpers import repository
from test_sandbox_sessions import application
from test_git_delivery_service import prepare
from sandbox_session_helpers import SandboxTransport
from naiyue_test_agent.contracts.git_delivery import DeliveryCommand
from naiyue_test_agent.execution.git_repository import GitRepository


@pytest.mark.parametrize('command',[
    dict(action='push',branch='task',remote_name='origin',remote_url='https://example.invalid/repo'),
    dict(action='pr'),dict(action='branch',branch='task',remote_name='origin'),
    dict(action='merge',remote_url='https://example.invalid/repo'),
])
def test_remote_delivery_commands_are_not_part_of_contract(command):
    with pytest.raises(ValidationError):DeliveryCommand(operation_id='1'*32,**command)


def test_delivery_options_and_all_local_actions_never_consult_remote_or_credentials(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                _,host,sid,_=await prepare(app,root,tmp_path)
                calls=[];normal=GitRepository.run
                def observed(self,args,**kwargs):
                    calls.append((args,kwargs))
                    return normal(self,args,**kwargs)
                monkeypatch.setattr(GitRepository,'run',observed)
                options=await host.sessions.delivery_options(sid,'c'*32)
                assert set(options)=={'actions','discarded','operations'}
                assert set(options['actions'])=={'branch','merge','discard'}
                with pytest.raises(ValidationError):
                    await host.sessions.deliver(sid,'c'*32,dict(operation_id='1'*32,action='push',branch='task'))
                assert await host.sessions.delivery_operations(sid,'c'*32)==[]
                for index,action in enumerate(('branch','merge','discard'),1):
                    result=await host.sessions.deliver(sid,'c'*32,dict(operation_id=str(index)*32,action=action,
                        **({'branch':'review/local'} if action=='branch' else {})))
                    assert result['state']=='completed'
                assert not any(kwargs.get('credentials') or kwargs.get('source_config') or
                    any(arg in ('push','ls-remote','remote','config') for arg in args) for args,kwargs in calls)
    asyncio.run(scenario())
