"""Disposable real-Docker acceptance through the product SessionAPI and RunAPI."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

from scripts.verify_sandbox_inputs import seed


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.contracts.sandbox import SandboxError
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools,SubAgentSpec
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    first=OfflineModel(responses=[AIMessage(content='done')]);second=OfflineModel(responses=[AIMessage(content='done')])
    child=OfflineModel(responses=[AIMessage(content='child done')])
    settings=AppSettings.model_validate({'storage':{'data_dir':str(directory/'data')},
        'memory':{'auto_enabled':False},'permissions':{'mode':'BypassApprovals'},
        'subagents':(SubAgentSpec('worker','Inspect files','Read the file',('read',),model_id='child'),),
        'sandbox':{'context':context,'image':image,'network':{'mode':'none'}}})
    def application():
        return open_service_application('unused',tools=create_builtin_tools(settings.tools),system_prompt='',response_format=None,settings=settings)
    def plan(model,name,args,ident):
        model.i=0;model.responses=[AIMessage(content='',tool_calls=[dict(name=name,args=args,id=ident)]),AIMessage(content='done')]
    def feedback(result,name):
        value=next(item for item in reversed(result['messages']) if isinstance(item,ToolMessage) and item.name==name)
        assert value.status=='success',value.content
        return value.content
    root=seed(directory);project=None;report={}
    with patch('naiyue_test_agent.application.load_model_catalog',side_effect=lambda _:ModelCatalog('first',{'first':first,'second':second,'child':child})):
        try:
            async with application() as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='project')
                host=await app.projects.open(project.project_id)
                key=str(uuid4())
                sid=await host.sessions.create_sandbox('first',request_id=key,include_untracked=['.agents/skills/input-check/SKILL.md'])
                assert await host.sessions.create_sandbox('first',request_id=key,include_untracked=['.agents/skills/input-check/SKILL.md'])==sid
                other=await host.sessions.create_sandbox('second',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                other_binding=(await host.sessions.status(other))['execution']['binding_id']
                assert binding!=other_binding and len(await app.environments.list(project.project_id))==2
                plan(first,'write',dict(path='same.txt',content='one'),'W1');plan(second,'write',dict(path='same.txt',content='two'),'W2')
                values=await asyncio.gather(host.runs.start(sid,[('user','write first')]),host.runs.start(other,[('user','write second')]))
                for value in values:feedback(value,'write')
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                other_files=await app.environments.files(other_binding,project_id=project.project_id,session_id=other)
                assert await files.read_resource(str(files.environment.path(files.environment.root)/'same.txt'),1024)==b'one'
                assert await other_files.read_resource(str(other_files.environment.path(other_files.environment.root)/'same.txt'),1024)==b'two'
                assert not (root/'same.txt').exists()
                assert len((await host.sessions.skills(None,session_id=sid)).entries)==1
                assert (await host.history.context(sid))['current']['basis']=='next_request'
                plan(first,'subagent',dict(agent='worker',task='Read same.txt'),'D1')
                child.i=0;child.responses=[AIMessage(content='',tool_calls=[dict(name='read',args=dict(path='same.txt'),id='R1')]),AIMessage(content='one')]
                envelope=json.loads(feedback(await host.runs.start(sid,[('user','delegate')]),'subagent'))
                assert envelope['status']=='completed',envelope
                agent_id=envelope['agent_id']
                plan(first,'bash',dict(command='printf sandbox-background; while :; do sleep 1; done',background=True),'B1')
                task_id=json.loads(feedback(await host.runs.start(sid,[('user','background')]),'bash'))['task_id']
                assert any(row['task_id']==task_id for row in (await host.tasks.list())['items'])
                output=await host.tasks.output(task_id,session_id=sid)
                assert output['output_available'] and output['environment_binding']==binding
                assert (await host.tasks.stop(task_id,session_id=sid))['cleanup_confirmed']
                await host.sessions.select_permission_mode(sid,'Default')
                plan(first,'write',dict(path='.reviewed.txt',content='approved'),'A1')
                await host.runs.start(sid,[('user','review')])
                assert host.runs.pending_tools(sid)
                report.update(parallel_session_isolation='passed',same_creation_request='passed',formal_subagent='passed',
                    actual_skills_and_context='passed',management_tasks='passed')
            async with application() as app:
                await app.environments.start(binding)
                host=await app.projects.open(project.project_id)
                history=await host.history.session(sid)
                assert history['recovery']['kind']=='pending_approval'
                await host.sessions.recover(sid,checkpoint_id=history['checkpoint_id'],action='restore_approval')
                await host.runs.resume_approval(sid,['approve'])
                files=await app.environments.files(binding,project_id=project.project_id,session_id=sid)
                assert await files.read_resource(str(files.environment.path(files.environment.root)/'.reviewed.txt'),1024)==b'approved'
                await host.sessions.select_permission_mode(sid,'BypassApprovals')
                plan(first,'subagent',dict(agent='worker',task='Read again',agent_id=agent_id),'D2')
                child.i=0;child.responses=[AIMessage(content='continued')]
                envelope=json.loads(feedback(await host.runs.start(sid,[('user','continue child')]),'subagent'))
                assert envelope['agent_id']==agent_id and envelope['report']=='continued',envelope
                assert host.sessions._persistence.storage.subagents.get_subagent(sid,agent_id)['environment_binding_id']==binding
                await host.sessions.prepare(other)
                await host.sessions.environment_action(other,'destroy',confirm_destroy=True)
                try:await host.runs.start(other,[('user','must refuse')])
                except SandboxError as error:assert error.code=='env_expired',error.code
                else:raise AssertionError('Destroyed sandbox executed a run')
                assert not (root/'.reviewed.txt').exists() and not (root/'same.txt').exists()
                assert (await host.tasks.output(task_id,session_id=sid))['cleanup_confirmed']
                report.update(reopen_approval_same_binding='passed',reopen_child_same_binding='passed',expired_no_host_fallback='passed',provider_requests=0)
        finally:
            primary=sys.exc_info()[0];failures=[]
            async with application() as app:
                for row in await app.environments.list(project.project_id) if project else ():
                    if row.lifecycle=='destroyed':continue
                    try:await app.environments.destroy(row.binding_id,confirm_destroy=True)
                    except Exception as error:
                        failures.append(error);logging.getLogger(__name__).error('sandbox_session_gate_cleanup_failed error_type=%s',type(error).__name__)
            if failures and primary is None:raise failures[0]
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-session-gate-') as directory:
        report=asyncio.run(scenario(Path(directory).resolve(),args.context,args.image))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
