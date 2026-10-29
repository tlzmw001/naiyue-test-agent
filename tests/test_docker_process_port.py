"""Sandbox task tools use a binding, without inventing a controller Path."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities,ToolAuthorization


def test_sandbox_task_query_accepts_bound_environment_without_local_execution_path():
    from naiyue_test_agent.tools.background_tasks import task_context
    env=EnvironmentRef('B','sandbox','P','/workspace','linux');port=object()
    scope=RunScope(RunIdentity('S','R',project_id='P'),RunCapabilities(task_queries=port),environment=env)
    subject=dict(execution_environment=env.public(),workspace=env.root)
    runtime=SimpleNamespace(context=RuntimeContext(scope,authorization=ToolAuthorization(permit=dict(
        call_id='C',session_id='S',agent_path='root',subject=subject))),tool_call_id='C')
    result,arguments=task_context(runtime,query=True)
    assert result is port and arguments==dict(session_id='S',workspace_root=None)


def test_sandbox_process_port_rejects_cross_session_before_io():
    from naiyue_test_agent.execution.docker_processes import DockerProcesses
    from naiyue_test_agent.contracts.sandbox import SandboxRecord
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    row=SandboxRecord('B','P','S','running','D','c','v','i',{})
    port=DockerProcesses(SimpleNamespace(),row)
    scope=RunScope(RunIdentity('OTHER','R',project_id='P'),RunCapabilities(processes=port),environment=row.environment)
    with pytest.raises(PermissionMechanismError):port.check_access(SimpleNamespace(context=RuntimeContext(scope)))


@pytest.mark.parametrize('background',[False,True])
def test_cancellation_during_submit_stops_original_operation_before_handoff(background):
    import asyncio
    from threading import Event
    from naiyue_test_agent.execution.docker_processes import DockerProcesses
    from naiyue_test_agent.contracts.sandbox import SandboxRecord
    from naiyue_test_agent.tools.settings import BashSettings
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event();events=[]
        async def submit(*_a):
            events.append('submitted');entered.set()
            await release.wait()
            return {'state':'running'}
        async def stop(*_a):events.append('stopped');return {'state':'stopped'}
        coordinator=SimpleNamespace(asubmit=submit,astop=stop)
        row=SandboxRecord('B','P','S','running','D','c','v','i',{'poll_milliseconds':100})
        port=DockerProcesses(coordinator,row)
        scope=RunScope(RunIdentity('S','R',project_id='P'),environment=row.environment)
        access=SimpleNamespace(context=RuntimeContext(scope),tool_call_id='C')
        task=asyncio.create_task(port._execute('shell',dict(command='test',timeout=None),access,BashSettings(),background,None,100))
        await entered.wait();task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert events==['submitted','stopped']
    asyncio.run(scenario())


def test_pre_requested_stop_rejects_before_remote_submission():
    import asyncio
    from langchain_core.tools import ToolException
    from naiyue_test_agent.execution.docker_processes import DockerProcesses
    from naiyue_test_agent.contracts.sandbox import SandboxRecord
    from naiyue_test_agent.tools.settings import BashSettings
    def forbidden(*_a):raise AssertionError('Stopped invocation must not submit')
    row=SandboxRecord('B','P','S','running','D','c','v','i',{})
    port=DockerProcesses(SimpleNamespace(asubmit=forbidden),row)
    scope=RunScope(RunIdentity('S','R',project_id='P'),control=SimpleNamespace(requested=True),environment=row.environment)
    access=SimpleNamespace(context=RuntimeContext(scope),tool_call_id='C')
    with pytest.raises(ToolException,match='stopped'):
        asyncio.run(port._execute('shell',dict(command='test',timeout=None),access,BashSettings(),False,None,100))


def test_task_stop_rejects_file_operation_before_any_remote_stop():
    import asyncio
    from contextlib import nullcontext
    from naiyue_test_agent.application.environments.tasks import SandboxTasks
    from naiyue_test_agent.application.sessions.background_tasks import TaskQueryError
    calls=[]
    def stop(*_a):calls.append('file stopped');return {'kind':'write'}
    coordinator=SimpleNamespace(records=lambda:SimpleNamespace(get=lambda *_a:{'kind':'write'}),astop=stop)
    tasks=SandboxTasks(coordinator,SimpleNamespace(binding_id='B',session_id='S'),SimpleNamespace(operation=nullcontext))
    with pytest.raises(TaskQueryError,match='Shell task'):
        asyncio.run(tasks.stop('a'*32,session_id='S'))
    assert calls==[]


def test_sandbox_feedback_does_not_direct_read_to_a_controller_archive():
    from naiyue_test_agent.execution.docker_processes import DockerProcesses
    from naiyue_test_agent.contracts.sandbox import SandboxRecord
    from naiyue_test_agent.tools.settings import BashSettings
    value=dict(returncode=0,termination='exited',output_complete=True,
        process_reaped=True,pipes_closed=True,consumer_settled=True,backpressure_count=0)
    coordinator=SimpleNamespace(value=lambda _:value,_archive=lambda _:SimpleNamespace(read=lambda _:b'hello'))
    port=DockerProcesses(coordinator,SandboxRecord('B','P','S','running','D','c','v','i',{}))
    row=dict(operation_id='a'*32,observed_bytes=5,metadata=dict(settings=BashSettings().model_dump(),
        archive_identity=[1,2,None],archive_path='/controller/data/archive.log',saved_bytes=5))
    text=port.format(row)
    assert 'read(path=' not in text and 'task_output' in text
