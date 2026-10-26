"""Disposable Docker result to explicit local Git delivery, without vendor calls."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4
from scripts.verify_sandbox_inputs import git


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.tools import create_builtin_tools
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    model=OfflineModel(responses=[AIMessage(content='done')])
    settings=AppSettings.model_validate({'storage':{'data_dir':str(directory/'data')},
        'memory':{'auto_enabled':False},'permissions':{'mode':'BypassApprovals'},
        'sandbox':{'context':context,'image':image,'network':{'mode':'none'}}})
    def application():return open_service_application('unused',tools=create_builtin_tools(settings.tools),
        system_prompt='',response_format=None,settings=settings)
    root=directory/'source';root.mkdir();git(root,'init','-q')
    (root/'app.txt').write_text('base\n');git(root,'add','.');git(root,'commit','-qm','base')
    original=git(root,'rev-parse','HEAD');project=None;report={}
    with patch('naiyue_test_agent.application.load_model_catalog',side_effect=lambda _:ModelCatalog('fake',{'fake':model})):
        try:
            async with application() as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                model.responses=[AIMessage(content='',tool_calls=[dict(name='bash',args={'command':"printf 'agent update\\n' > app.txt; printf 'new output\\n' > added.txt"},id='C')]),AIMessage(content='done')]
                result=await host.runs.start(sid,[('user','exercise result delivery')])
                message=next(m for m in reversed(result['messages']) if isinstance(m,ToolMessage) and m.name=='bash')
                assert message.status=='success',message.content
                ident=uuid4().hex;captured=await host.sessions.capture_result(sid,result_id=ident,include_untracked=['added.txt'])
                assert captured['state']=='ready' and git(root,'rev-parse','HEAD')==original
                await host.sessions.environment_action(sid,'destroy')
                branch=await host.sessions.deliver(sid,ident,dict(operation_id=uuid4().hex,action='branch',branch='review/result'))
                assert branch['state']=='completed' and git(root,'rev-parse','HEAD')==original
                assert not (root/'added.txt').exists()
                merged=await host.sessions.deliver(sid,ident,dict(operation_id=uuid4().hex,action='merge'))
                assert merged['state']=='completed' and (root/'app.txt').read_text()=='agent update\n'
                assert (root/'added.txt').read_text()=='new output\n' and not git(root,'status','--porcelain')
                discarded=await host.sessions.deliver(sid,ident,dict(operation_id=uuid4().hex,action='discard'))
                assert discarded['state']=='completed' and not git(root,'for-each-ref','refs/naiyue/tasks/')
                assert git(root,'rev-parse','refs/heads/review/result').decode().strip()==captured['receipt']['output_commit']
            async with application() as app:
                host=await app.projects.history_host(project.project_id)
                assert (await host.sessions.result(sid,ident))['discarded']
                assert '+new output' in (await host.sessions.result_diff(sid,ident,'added.txt'))['patch']
                assert len(await host.sessions.delivery_operations(sid,ident))==3
            report=dict(docker_capture='passed',delivery_after_destroy='passed',branch_without_checkout='passed',
                clean_linear_merge='passed',discard_keeps_user_branch='passed',
                review_and_operations_after_restart='passed',provider_requests=0)
        finally:
            primary=sys.exc_info()[0];failures=[]
            async with application() as app:
                for row in await app.environments.list(project.project_id) if project else ():
                    if row.lifecycle=='destroyed':continue
                    try:await app.environments.destroy(row.binding_id)
                    except Exception as error:
                        failures.append(error);logging.getLogger(__name__).error('sandbox_delivery_cleanup_failed error_type=%s',type(error).__name__)
            if failures and primary is None:raise failures[0]
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-delivery-gate-') as directory:
        report=asyncio.run(scenario(Path(directory).resolve(),args.context,args.image))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
