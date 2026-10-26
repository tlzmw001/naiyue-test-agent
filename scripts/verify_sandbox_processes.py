"""Explicit real Docker process/recovery gate, with offline model responses."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.application import prepare_tool_registry
    from naiyue_test_agent.agent_runtime import build_runtime_agent
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities
    from naiyue_test_agent.contracts.sandbox import SandboxError
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    settings=AppSettings.model_validate({'storage':{'data_dir':str(directory/'data')},'memory':{'auto_enabled':False},
        'tools':{'aitest_kit':{'executable':'python','prefix_args':['-I','-c','import sys;print(sys.argv[1])']}},
        'sandbox':{'context':context,'image':image,'process_output_bytes':65536,'network':{'mode':'none'}}})
    names={'read','write','edit','bash','aitest_kit','task_list','task_output','task_stop'}
    specs={spec.tool.name:spec for spec in create_builtin_tools(settings.tools) if spec.tool.name in names}
    def application():
        return open_service_application('unused',tools=tuple(specs.values()),system_prompt='',response_format=None,settings=settings)
    async def invoke(files,processes,tasks,name,args):
        model=OfflineModel(responses=[AIMessage(content='',tool_calls=[dict(name=name,args=args,id='C')]),AIMessage(content='done')])
        registry=prepare_tool_registry([specs[name]])
        agent=build_runtime_agent(model,tool_registry=registry,resolve_active_tool_names=lambda *_a,**_k:registry.names(),
            system_prompt='',response_format=None,resolve_model=lambda *_a,**_k:('fake',model),
            resolve_permission_context=lambda *_a,**_k:{'mode':'BypassApprovals','session_allow':[]})
        scope=RunScope(RunIdentity(files.owner_session_id,uuid4().hex,project_id=files.environment.project_id),
            RunCapabilities(files=files,processes=processes,task_queries=tasks),environment=files.environment)
        result=await agent.ainvoke({'messages':[('user','sandbox operation')]},context=RuntimeContext(scope))
        return next(item for item in result['messages'] if isinstance(item,ToolMessage))
    report={};project=None;binding=None
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':OfflineModel(responses=[AIMessage(content='unused')])})):
        try:
            async with application() as app:
                root=directory/'source';root.mkdir()
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id);sid=await host.sessions.create('fake')
                record=await app.environments.create(project.project_id,sid);binding=record.binding_id
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                processes=await app.environments.processes(binding,project_id=project.project_id,session_id=sid)
                tasks=await app.environments.tasks(binding,project_id=project.project_id,session_id=sid)
                async def call(name,args):return await invoke(files,processes,tasks,name,args)
                result=await call('bash',{'command':'printf hello > hello.txt; printf hello'})
                assert result.status=='success' and 'hello' in result.content,result.content
                assert not (root/'hello.txt').exists()
                result=await call('read',{'path':'hello.txt'})
                assert result.status=='success' and 'hello' in result.content
                result=await call('aitest_kit',{'args':['literal; $(do not execute)']})
                assert result.status=='success' and 'literal; $(do not execute)' in result.content,result.content
                for command,timeout,fragment in [('exit 7',None,'exit_code=7'),('sleep 5',1,'timed out')]:
                    args={'command':command}
                    if timeout:args['timeout']=timeout
                    result=await call('bash',args)
                    assert result.status=='error' and fragment in result.content,result.content
                result=await call('bash',{'command':'printf started; while :; do sleep 1; done','background':True})
                assert result.status=='success',result.content
                task_id=json.loads(result.content)['task_id']
                deadline=time.monotonic()+30
                while True:
                    output=await tasks.output(task_id,session_id=sid)
                    if 'started' in output['output']:break
                    assert time.monotonic()<deadline
                    await asyncio.sleep(.1)
                result=await call('task_list',{})
                assert result.status=='success' and task_id in result.content
                result=await call('task_stop',{'task_id':task_id})
                stopped=json.loads(result.content)
                assert stopped['status']=='stopped' and stopped['cleanup_confirmed'],stopped
                report.update(formal_shell='passed',literal_argv='passed',nonzero_timeout='passed',background_tools_stop='passed')
                original=DockerEnvironment.request;submissions=[]
                def lose_ack(engine,row,operation,params,**kwargs):
                    value=original(engine,row,operation,params,**kwargs)
                    if operation=='operation_submit':
                        submissions.append(params['operation_id'])
                        raise SandboxError('env_operation_unknown')
                    return value
                with patch.object(DockerEnvironment,'request',lose_ack):
                    result=await call('bash',{'command':'printf once >> count.txt; printf acknowledged'})
                    assert result.status=='success' and 'acknowledged' in result.content,result.content
                    result=await call('write',{'path':'ack-write.txt','content':'written once'})
                    assert result.status=='success',result.content
                assert len(submissions)==2 and len(set(submissions))==2
                result=await call('read',{'path':'count.txt'})
                assert 'once' in result.content and 'onceonce' not in result.content
                report['lost_process_and_file_ack']='passed'
                # Deliberately pause collection until output has exceeded envd retention.
                from naiyue_test_agent.tools.settings import BashSettings
                process_settings=BashSettings();ident=uuid4().hex
                payload=dict(name='bash',command="python -I -c 'import sys;sys.stdout.write(\"x\"*262144+\"END\")'",
                    timeout=30,settings=process_settings.model_dump(mode='json'),process_settings=None,output_limit=None)
                metadata=dict(offset=0,saved_bytes=0,max_archive_bytes=1048576,tail_limit=65536,
                    settings=process_settings.model_dump(mode='json'),background=False,agent_path='root')
                coordinator=app.environments._operations
                await run_blocking(coordinator.submit,binding,ident,'shell',payload,metadata)
                deadline=time.monotonic()+30
                while True:
                    remote=await run_blocking(coordinator._rpc,binding,'operation_status',dict(operation_id=ident))
                    if remote['state']=='completed':break
                    assert time.monotonic()<deadline
                    await asyncio.sleep(.1)
                output=await tasks.output(ident,session_id=sid)
                assert output['observed_raw_bytes']==262147 and output['output'].endswith('END'),output
                assert not output['archive_complete'] and output['archive_reason']=='capture_gap',output
                assert not output['output_complete'] and output['archive_saved_bytes']<=65536,output
                report['retention_gap_and_final_tail']='passed'
                # A normal application close must stop its remote task, not the container.
                result=await call('bash',{'command':'printf closing; while :; do sleep 1; done','background':True})
                closing_task=json.loads(result.content)['task_id']
            async with application() as app:
                tasks=await app.environments.tasks(binding,project_id=project.project_id,session_id=sid)
                output=await tasks.output(closing_task,session_id=sid)
                assert output['status']=='stopped' and output['cleanup_confirmed'],output
                assert (await app.environments.get(binding)).lifecycle=='running'
                report['normal_close_confirmed']='passed'
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                processes=await app.environments.processes(binding,project_id=project.project_id,session_id=sid)
                result=await invoke(files,processes,tasks,'bash',{'command':'printf restart; while :; do sleep 1; done','background':True})
                interrupted=json.loads(result.content)['task_id']
                await app.environments.stop(binding);await app.environments.start(binding)
                output=await tasks.output(interrupted,session_id=sid)
                assert output['status']=='unknown',output
                try:await tasks.stop(interrupted,session_id=sid)
                except SandboxError as error:assert error.code=='env_stop_unconfirmed'
                else:raise AssertionError('Unknown process was falsely reported stopped')
                report['envd_restart_unknown_no_replay']='passed'
                await app.environments.destroy(binding)
                result=await invoke(files,processes,tasks,'bash',{'command':'printf must-not-run'})
                assert result.status=='error'
                assert not (root/'hello.txt').exists()
                report.update(destroy_no_host_fallback='passed',provider_requests=0)
        finally:
            if project is not None:
                async with application() as cleanup:
                    for row in await cleanup.environments.list(project.project_id):await cleanup.environments.destroy(row.binding_id)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-sandbox-process-') as directory:
        report=asyncio.run(scenario(Path(directory),args.context,args.image))
        from scripts.sandbox_crash_probe import verify_crash
        report['controller_crash_recovery']=verify_crash(Path(directory)/'crash',args.context,args.image)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
