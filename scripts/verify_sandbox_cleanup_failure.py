"""Exercise fixed cleanup failure semantics through an actual Docker RPC channel."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4

from scripts.sandbox_crash_probe import application


# Test interpreter patches are confined to this disposable container/process.
# The installed package and production envd keep their original source and entrypoint.
SERVER = '''import json,os
from naiyue_test_agent.envd import operations
from naiyue_test_agent.envd import __main__ as server
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.tools.processes import ProcessCleanupError
def fail(*args,**kwargs):raise ProcessCleanupError("PRIVATE_CLEANUP_BODY",facts={"PRIVATE_FACT":"secret"})
operations.execute_process=fail
original_submit=operations.EnvironmentOperations.submit
def submit(self,*args,**kwargs):
 row=original_submit(self,*args,**kwargs)
 assert self.wait(row["operation_id"],10)
 return self.journal.get(row["operation_id"])
operations.EnvironmentOperations.submit=submit
server.SOCKET_PATH="/tmp/nyt-cleanup-probe.sock"
settings=SandboxSettings.model_validate(json.loads(os.environ["NYT_SANDBOX_POLICY"]))
server.serve(EnvironmentRef(**json.loads(os.environ["NYT_ENVIRONMENT"])),os.environ["NYT_SESSION_ID"],
    settings.max_message_bytes,settings=settings,state_root="/tmp/nyt-cleanup-probe")
'''
CLIENT = '''import sys
from naiyue_test_agent.envd import __main__ as client
client.SOCKET_PATH="/tmp/nyt-cleanup-probe.sock"
sys.argv=["envd","request",*sys.argv[1:]]
client.main()
'''


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.agent_runtime import build_runtime_agent
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.application import prepare_tool_registry
    from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    from naiyue_test_agent.tools.processes import ProcessCleanupError

    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self

    async with application(directory,context,image) as app:
        code=directory/'source';code.mkdir()
        project=await app.projects.add_local(code,trusted=True,operation_id='P')
        host=await app.projects.open(project.project_id);sid=await host.sessions.create('fake')
        record=await app.environments.create(project.project_id,sid)
        report={}
        try:
            files=await app.environments.files(record.binding_id,project_id=project.project_id,session_id=sid)
            processes=await app.environments.processes(record.binding_id,project_id=project.project_id,session_id=sid)
            engine=app.environments._service._engine(record)
            await run_blocking(engine.cli.run,['container','exec','-d',record.container_id,'python','-I','-c',SERVER],mutates=True)

            class ProbeCLI:
                def __init__(self,original):self.original=original
                def __getattr__(self,name):return getattr(self.original,name)
                def run(self,args,**kwargs):
                    if args[:4]==['container','exec','-i',record.container_id]:
                        assert args[4:9]==['python','-I','-m','naiyue_test_agent.envd','request']
                        args=[*args[:6],'-c',CLIENT,*args[9:]]
                    return self.original.run(args,**kwargs)

            original=DockerEnvironment.request
            def routed(engine,row,*args,**kwargs):
                if row.binding_id==record.binding_id:
                    engine=DockerEnvironment(ProbeCLI(engine.cli))
                return original(engine,row,*args,**kwargs)

            specs={spec.tool.name:spec for spec in create_builtin_tools(AppSettings().tools)}
            with patch.object(DockerEnvironment,'request',routed):
                for label,name,args in [('foreground','bash',{'command':'never started'}),
                        ('background_before_handoff','bash',{'command':'never started','background':True}),
                        ('literal_argv','aitest_kit',{'args':['--help']})]:
                    selected=[]
                    model=OfflineModel(responses=[AIMessage(content='',tool_calls=[dict(name=name,args=args,id='C')]),
                        AIMessage(content='must not continue')])
                    registry=prepare_tool_registry([specs[name]])
                    def select(*_a,**_k):selected.append('model');return 'fake',model
                    agent=build_runtime_agent(model,tool_registry=registry,resolve_active_tool_names=lambda *_a,**_k:registry.names(),
                        system_prompt='',response_format=None,resolve_model=select,
                        resolve_permission_context=lambda *_a,**_k:{'mode':'BypassApprovals','session_allow':[]})
                    scope=RunScope(RunIdentity(sid,uuid4().hex,project_id=project.project_id),
                        RunCapabilities(files=files,processes=processes),environment=record.environment)
                    try:await agent.ainvoke({'messages':[('user','verify cleanup fault')]},context=RuntimeContext(scope))
                    except ProcessCleanupError as error:
                        assert 'operation_id=' in str(error) and 'PRIVATE_' not in str(error)
                    else:raise AssertionError('Cleanup mechanism failure was converted to ordinary tool feedback')
                    assert selected==['model']
                    report[label]='passed'
            rows=await run_blocking(lambda:app.environments._operations.records().list(record.binding_id))
            assert len(rows)==3 and all(row['state']=='unknown' and
                row['result']['error']['code']=='env_process_cleanup_unconfirmed' for row in rows)
            assert 'PRIVATE_' not in json.dumps(rows)
            report.update(persisted_fixed_error_code='passed',no_private_exception_body='passed',provider_requests=0)
        finally:await app.environments.destroy(record.binding_id,confirm_destroy=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-cleanup-failure-') as directory:
        report=asyncio.run(scenario(Path(directory).resolve(),args.context,args.image))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
