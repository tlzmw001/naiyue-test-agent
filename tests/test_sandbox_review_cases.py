"""Deterministic review regressions at lifecycle, scheduling and wire boundaries."""
import asyncio
from threading import Event,Thread,get_ident
from types import SimpleNamespace
import json
import pytest
from naiyue_test_agent.contracts.sandbox import SandboxError,SandboxRecord,PROTOCOL_VERSION
from naiyue_test_agent.sandbox_settings import SandboxSettings


@pytest.mark.parametrize('name',['find','grep'])
def test_async_search_validation_does_not_block_owner_loop(monkeypatch,name):
    from naiyue_test_agent.tools import find,grep
    from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities,ToolAuthorization
    module=find if name=='find' else grep
    spec=module.create_session_find_tool() if name=='find' else module.create_session_grep_tool()
    async def scenario():
        loop=asyncio.get_running_loop()
        entered,release,heartbeat=Event(),Event(),Event()
        responsive=[]
        def target(*args,**kwargs):
            entered.set();loop.call_soon_threadsafe(heartbeat.set)
            assert release.wait(5)
        def observe():
            assert entered.wait(5)
            responsive.append(heartbeat.wait(2));release.set()
        async def search(*args,**kwargs):return 'done'
        monkeypatch.setattr(module,'require_file_target',target)
        scope=RunScope(RunIdentity('S'),capabilities=RunCapabilities(files=SimpleNamespace(find=search,grep=search)))
        runtime=SimpleNamespace(context=RuntimeContext(scope,authorization=ToolAuthorization(permit={'subject':{}})),tool_call_id='C')
        observer=Thread(target=observe);observer.start()
        try:assert await spec.tool.coroutine(pattern='x',runtime=runtime)=='done'
        finally:release.set();observer.join(5)
        assert responsive==[True]
    asyncio.run(scenario())


@pytest.mark.parametrize('mutates',[False,True])
@pytest.mark.parametrize('body',[{'ok':True},{'ok':False,'error':[]},{'ok':'yes','result':'x'}])
def test_malformed_reply_retains_unknown_write_outcome(mutates,body):
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    row=SandboxRecord('B','P','S','running','D','container','volume','image',SandboxSettings().model_dump(mode='json'),container_id='c'*64)
    class CLI:
        def run(self,args,*,input_bytes=None,**kwargs):
            request=json.loads(input_bytes)
            return json.dumps(dict(protocol=PROTOCOL_VERSION,request_id=request['request_id'],environment=row.environment.public(),session_id='S',**body)).encode()
    engine=DockerEnvironment(CLI());engine.validate=lambda *_a,**_k:None
    with pytest.raises(SandboxError,match='env_operation_unknown' if mutates else 'env_protocol_mismatch'):
        engine.request(row,'write',{},mutates=mutates)


def test_destroy_cannot_finish_then_be_revived_by_pending_create(monkeypatch,tmp_path):
    from naiyue_test_agent.application.environments import service
    reserved,allow_create,destroy_attempted,destroy_done=Event(),Event(),Event(),Event()
    order=[]
    class Repo:
        row=None
        def reserve(self,row,*,limits):
            self.row=row;reserved.set();assert allow_create.wait(5)
        def touch(self,binding,now):return True
        def get(self,binding):
            if self.row is None:raise SandboxError('env_not_registered')
            return self.row
        def save(self,row):self.row=row;order.append(row.lifecycle)
    repo=Repo()
    class CLI:
        def __init__(self,*args):pass
        def daemon(self,*args):return 'D'
        def json(self,*args):return [{'Os':'linux','Id':'image'}]
        def container(self,*args,**kwargs):return None
        def volume(self,*args):return None
    class Engine:
        def __init__(self,cli):self.cli=cli
        def create(self,row,save):
            order.append('create_after_'+repo.row.lifecycle)
            row=row.changed(container_id='c'*64);save(row);return row
        def handshake(self,row):pass
        def action(self,row,action):return row.changed(lifecycle='destroyed')
    monkeypatch.setattr(service,'DockerCLI',CLI);monkeypatch.setattr(service,'DockerEnvironment',Engine)
    controller=service.EnvironmentService(SimpleNamespace(data_dir=tmp_path),SandboxSettings(image='app:test'))
    controller._project=lambda *_:None;controller.records=lambda:repo
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    controller.activity=EnvironmentActivity(controller.records)
    original=controller._lock
    def lifecycle_lock(binding):
        if get_ident()!=creator.ident:destroy_attempted.set()
        return original(binding)
    controller._lock=lifecycle_lock
    errors=[]
    def create():
        try:controller.create('P','S')
        except Exception as error:errors.append(error)
    creator=Thread(target=create);creator.start();assert reserved.wait(5)
    def destroy():
        try:controller.action(repo.row.binding_id,'destroy')
        except Exception as error:errors.append(error)
        finally:destroy_done.set()
    gate=original(repo.row.binding_id)
    unprotected=gate.acquire(blocking=False)
    if unprotected:gate.release()
    destroyer=Thread(target=destroy);destroyer.start();assert destroy_done.wait(5)
    # Active creation now rejects lifecycle mutation immediately, before IO.
    assert len(errors)==1 and isinstance(errors[0],SandboxError) and errors[0].code=='env_busy'
    errors.clear()
    allow_create.set();creator.join(5);destroyer.join(5)
    assert not errors and not creator.is_alive() and not destroyer.is_alive()
    assert 'create_after_destroyed' not in order
    controller.action(repo.row.binding_id,'destroy')
    assert repo.row.lifecycle=='destroyed'


def test_sandbox_resource_boundary_keeps_discovery_diagnostics(tmp_path):
    from naiyue_test_agent.envd.files import EnvdFiles
    from naiyue_test_agent.contracts.environment import EnvironmentRef
    from naiyue_test_agent.application.sessions.skills import discover_project_skills
    from naiyue_test_agent.application.sessions.project_instructions import load_project_instruction_part,ProjectInstructionsError
    outside=tmp_path.parent/'other-resources';outside.mkdir(exist_ok=True)
    (outside/'AGENTS.md').write_text('outside secret')
    (tmp_path/'.agents').mkdir();(tmp_path/'.agents/skills').symlink_to(outside,target_is_directory=True)
    files=EnvdFiles(EnvironmentRef('B','sandbox','P',str(tmp_path),'linux'),'S')
    catalog=discover_project_skills(files)
    assert catalog.diagnostics and catalog.diagnostics[0]['reason']=='outside_workspace'
    (tmp_path/'AGENTS.md').symlink_to(outside/'AGENTS.md')
    with pytest.raises(ProjectInstructionsError):load_project_instruction_part(files)


@pytest.mark.parametrize('operation',['write','edit'])
@pytest.mark.parametrize('result',[None,{'text':'done','artifact':{}},['done']])
def test_invalid_write_feedback_is_an_unknown_outcome(operation,result):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.execution.docker_files import DockerFileSystem
    from naiyue_test_agent.tools.settings import WriteSettings,EditSettings
    row=SandboxRecord('B','P','S','running','D','container','volume','image',{})
    controller=SimpleNamespace(request=lambda *_a,**_k:result)
    files=DockerFileSystem(controller,row)
    with pytest.raises(ToolException,match='env_operation_unknown'):
        if operation=='write':
            files.write({},'content',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
        else:
            files.edit({},[],settings=EditSettings(),control=None,progress=None)
