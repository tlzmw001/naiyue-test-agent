"""Real Docker input gate using public management APIs and offline model tool calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
from uuid import uuid4


def git(root,*args):
    return subprocess.check_output(['git','-c','core.hooksPath='+os.devnull,
        '-c','commit.gpgsign=false','-c','user.name=Input Gate','-c','user.email=input@invalid',
        *args],cwd=root,stderr=subprocess.PIPE)


def seed(directory):
    root=directory/'source';root.mkdir()
    git(root,'init','-q')
    (root/'app.txt').write_text('base\n')
    (root/'.gitignore').write_text('ignored/\n.env\n')
    (root/'AGENTS.md').write_text('Use the selected input.\n')
    git(root,'add','.');git(root,'commit','-qm','base')
    (root/'app.txt').write_text('staged\n');git(root,'add','app.txt')
    (root/'app.txt').write_text('working input\n')
    skill=root/'.agents'/'skills'/'input-check'/'SKILL.md';skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: input-check\ndescription: Check input\n---\nCheck selected input.\n')
    (root/'ignored').mkdir();(root/'ignored'/'private.txt').write_text('excluded\n')
    return root


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.application import prepare_tool_registry
    from naiyue_test_agent.agent_runtime import build_runtime_agent
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.contracts.run_context import RuntimeContext,RunScope,RunIdentity,RunCapabilities
    from naiyue_test_agent.contracts.git_inputs import GitInputError
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    settings=AppSettings.model_validate({'storage':{'data_dir':str(directory/'data')},
        'memory':{'auto_enabled':False},'sandbox':{'context':context,'image':image,'network':{'mode':'none'}}})
    specs={spec.tool.name:spec for spec in create_builtin_tools(settings.tools) if spec.tool.name in ('read','bash')}
    def application():
        return open_service_application('unused',tools=tuple(specs.values()),system_prompt='',response_format=None,settings=settings)
    async def invoke(files,processes,name,args):
        model=OfflineModel(responses=[AIMessage(content='',tool_calls=[dict(name=name,args=args,id='C')]),AIMessage(content='done')])
        registry=prepare_tool_registry([specs[name]])
        agent=build_runtime_agent(model,tool_registry=registry,resolve_active_tool_names=lambda *_a,**_k:registry.names(),
            system_prompt='',response_format=None,resolve_model=lambda *_a,**_k:('fake',model),
            resolve_permission_context=lambda *_a,**_k:{'mode':'BypassApprovals','session_allow':[]})
        scope=RunScope(RunIdentity(files.owner_session_id,uuid4().hex,project_id=files.environment.project_id),
            RunCapabilities(files=files,processes=processes),environment=files.environment)
        result=await agent.ainvoke({'messages':[('user','verify selected input')]},context=RuntimeContext(scope))
        message=next(item for item in result['messages'] if isinstance(item,ToolMessage))
        assert message.status=='success',message.content
        return message.content
    root=await run_blocking(seed,directory)
    report={};project=None;binding=None;operation=None;snapshot=None
    with patch('naiyue_test_agent.application.load_model_catalog',lambda _:ModelCatalog('fake',{'fake':OfflineModel(responses=[AIMessage(content='unused')])})):
        try:
            async with application() as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                index=(root/'.git'/'index').read_bytes();head=await run_blocking(git,root,'rev-parse','HEAD')
                ident=uuid4().hex
                prepared=await app.environments.prepare_input(project.project_id,input_id=ident,
                    include_untracked=('.agents/skills/input-check/SKILL.md',))
                snapshot=prepared.snapshot
                assert snapshot.original_head!=snapshot.input_snapshot
                assert (root/'.git'/'index').read_bytes()==index
                assert await run_blocking(git,root,'rev-parse','HEAD')==head
                assert (root/'app.txt').read_text()=='working input\n'
                assert 'ignored/private.txt' in {item.path for item in snapshot.excluded}
                host=await app.projects.open(project.project_id);sid=await host.sessions.create('fake')
                row=await app.environments.create(project.project_id,sid);binding=row.binding_id
                installed=await app.environments.install_input(binding,ident);operation=installed.input_operation_id
                assert installed.input_snapshot==snapshot.input_snapshot
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                processes=await app.environments.processes(binding,project_id=project.project_id,session_id=sid)
                assert 'working input' in await invoke(files,processes,'read',{'path':'app.txt'})
                result=await invoke(files,processes,'bash',{'command':'git rev-parse HEAD; git branch --show-current; printf agent-change > app.txt'})
                assert snapshot.input_snapshot in result and 'naiyue-task/'+ident in result
                from naiyue_test_agent.application.sessions.skills import prepare_run_resources
                resources=await run_blocking(prepare_run_resources,files)
                assert 'Use the selected input' in resources['project_instructions']
                assert any(part.get('count')==1 for part in resources['prompt_parts'])
                original=DockerEnvironment.request;submissions=[]
                def observe(engine,row,op,params,**kwargs):
                    if op=='operation_submit':submissions.append(params['operation_id'])
                    return original(engine,row,op,params,**kwargs)
                with patch.object(DockerEnvironment,'request',observe):
                    assert (await app.environments.install_input(binding,ident)).input_snapshot==snapshot.input_snapshot
                    assert (await app.environments.operation_status(binding,operation))['state']=='completed'
                assert submissions==[]
                assert 'agent-change' in await invoke(files,processes,'read',{'path':'app.txt'})
                assert (root/'app.txt').read_text()=='working input\n'
                (root/'.env.local').write_text('example-private-fixture=1\n')
                try:
                    await app.environments.prepare_input(project.project_id,input_id=uuid4().hex,include_untracked=('.env.local',))
                except GitInputError as error:assert error.code=='git_input_sensitive' and error.paths==('.env.local',)
                else:raise AssertionError('Sensitive input accepted')
                report.update(real_bundle_install='passed',index_and_head_unchanged='passed',
                    formal_read_shell='passed',selected_hidden_skill='passed',ignored_excluded='passed',
                    credentials_refused='passed',repeat_preserves_changes='passed')
            async with application() as app:
                await app.environments.start(binding)
                assert (await app.environments.operation_status(binding,operation))['state']=='completed'
                assert (await app.environments.get(binding)).input_snapshot==snapshot.input_snapshot
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                processes=await app.environments.processes(binding,project_id=project.project_id,session_id=sid)
                assert 'agent-change' in await invoke(files,processes,'read',{'path':'app.txt'})
                report.update(reopen_confirms_original='passed',provider_requests=0)
        finally:
            import logging,sys
            primary=sys.exc_info()[0];failures=[]
            async with application() as app:
                rows=await app.environments.list(project.project_id) if project is not None else ()
                for row in rows:
                    try:await app.environments.destroy(row.binding_id)
                    except Exception as error:
                        failures.append(error)
                        logging.getLogger(__name__).error('sandbox_input_gate_cleanup_failed error_type=%s',type(error).__name__)
            if failures and primary is None:raise failures[0]
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-input-gate-') as directory:
        report=asyncio.run(scenario(Path(directory),args.context,args.image))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
