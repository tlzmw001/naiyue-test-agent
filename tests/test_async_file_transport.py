"""High-frequency file tools await transport instead of occupying worker threads."""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from test_docker_environment import record


@pytest.mark.parametrize('name',['read','write','edit','ls'])
def test_file_tools_register_native_async_body(name):
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    tool=next(spec.tool for spec in create_builtin_tools(AppSettings().tools) if spec.tool.name==name)
    assert inspect.iscoroutinefunction(tool.coroutine)


def test_remote_read_uses_async_request_and_keeps_subject_verification_in_envd():
    from naiyue_test_agent.execution.docker_files import DockerFileSystem
    from naiyue_test_agent.tools.settings import ReadSettings
    calls=[]
    async def request(binding,operation,params,**kwargs):calls.append((binding,operation,params));return 'content'
    files=DockerFileSystem(SimpleNamespace(arequest=request),record())
    subject={'requested':'/workspace/file','resolved':'/workspace/file'}
    async def run():
        return await files.read(subject,None,None,settings=ReadSettings(),control=None,progress=None)
    assert asyncio.run(run())=='content'
    assert len(calls)==1 and calls[0][1]=='read' and calls[0][2]['subject']==subject


def test_permission_preparation_awaits_remote_path_before_deciding(tmp_path,monkeypatch):
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_permission_call
    from naiyue_test_agent.tools import create_session_read_tool
    from naiyue_test_agent.tools.permission_rules import PermissionSettings
    from runtime_context_helpers import ExecutionRoot,RunIdentity,typed_context
    context=typed_context(identity=RunIdentity(session_id='session',execution_root=ExecutionRoot(tmp_path)))
    files=context.scope.capabilities.files
    original=files.prepare_target
    observed=[]
    async def prepare(self,path,operation):
        await asyncio.sleep(0)
        observed.append((path,operation))
        return original(path,operation)
    monkeypatch.setattr(type(files),'prepare_target',prepare)
    async def run():
        return await prepare_permission_call({'id':'read','name':'read','args':{'path':'file'}},
            create_session_read_tool(),context,PermissionSettings(),[])
    result=asyncio.run(run())
    assert result['validation']=='valid' and result['decision']=='allow'
    assert result['subject']['resolved']==str(tmp_path/'file') and observed==[('file','read')]
