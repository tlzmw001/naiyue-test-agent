"""Disposable real-Docker result capture, import and offline review acceptance."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

from scripts.verify_sandbox_inputs import git,seed


async def scenario(directory,context,image):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage,ToolMessage
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.contracts.git_inputs import GitInputError
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
    async def shell(host,sid,command,*,background=False):
        model.i=0;model.responses=[AIMessage(content='',tool_calls=[dict(name='bash',
            args=dict(command=command,background=background),id=uuid4().hex)]),AIMessage(content='done')]
        run=await host.runs.start(sid,[('user','exercise sandbox result boundary')])
        message=next(m for m in reversed(run['messages']) if isinstance(m,ToolMessage) and m.name=='bash')
        assert message.status=='success',message.content
        return message.content
    root=seed(directory);project=None;report={}
    original=(git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes(),(root/'app.txt').read_bytes())
    with patch('naiyue_test_agent.application.load_model_catalog',side_effect=lambda _:ModelCatalog('fake',{'fake':model})):
        try:
            async with application() as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                task=json.loads(await shell(host,sid,'printf still-running; while :; do sleep 1; done',background=True))['task_id']
                try:await host.sessions.capture_result(sid,result_id=uuid4().hex,include_untracked=[])
                except GitInputError as error:assert error.code=='git_result_busy',error.code
                else:raise AssertionError('Result captured with an active background process')
                assert (await host.tasks.stop(task,session_id=sid))['cleanup_confirmed']
                await shell(host,sid,"printf 'agent update\\n' > app.txt; printf 'agent new\\n' > added.txt; printf '\\000\\001binary' > binary.bin; chmod +x app.txt; mv AGENTS.md RENAMED.md")
                options=await host.sessions.result_options(sid)
                assert set(options['untracked'])=={'added.txt','binary.bin','RENAMED.md'}
                ident=uuid4().hex
                result=await host.sessions.capture_result(sid,result_id=ident,
                    include_untracked=['added.txt','binary.bin','RENAMED.md'])
                assert result['state']=='ready',result
                receipt=result['receipt'];files={item['path']:item for item in result['files']}
                assert set(files)=={'added.txt','binary.bin','RENAMED.md','app.txt'},files
                assert files['RENAMED.md']['status']=='R' and files['app.txt']['new_mode']=='100755'
                assert (await host.sessions.result_diff(sid,ident,'binary.bin'))['binary']
                diff=await host.sessions.result_diff(sid,ident,'app.txt')
                assert '-working input' in diff['patch'] and '+agent update' in diff['patch']
                assert receipt['input_snapshot']!=receipt['original_head']
                assert git(root,'rev-parse','refs/naiyue/tasks/'+ident).decode().strip()==receipt['output_commit']
                assert (git(root,'rev-parse','HEAD'),(root/'.git'/'index').read_bytes(),(root/'app.txt').read_bytes())==original
                assert not (root/'added.txt').exists() and (root/'AGENTS.md').exists()
                assert (await host.sessions.capture_result(sid,result_id=ident,
                    include_untracked=['added.txt','binary.bin','RENAMED.md']))['receipt']==receipt
                await host.sessions.environment_action(sid,'destroy',confirm_destroy=True)
                assert '+agent new' in (await host.sessions.result_diff(sid,ident,'added.txt'))['patch']
                report.update(active_process_refused='passed',snapshot_basis='passed',rename_binary_mode='passed',
                    source_head_index_worktree_preserved='passed',fixed_ref_import='passed',same_capture_retry='passed',
                    review_after_destroy='passed',original_head=receipt['original_head'],input_snapshot=receipt['input_snapshot'],output_commit=receipt['output_commit'])
            async with application() as app:
                host=await app.projects.history_host(project.project_id)
                assert (await host.sessions.result(sid,ident))['receipt']==receipt
                assert '+agent new' in (await host.sessions.result_diff(sid,ident,'added.txt'))['patch']
                report.update(review_after_restart='passed',provider_requests=0)
        finally:
            primary=sys.exc_info()[0];failures=[]
            async with application() as app:
                for row in await app.environments.list(project.project_id) if project else ():
                    if row.lifecycle=='destroyed':continue
                    try:await app.environments.destroy(row.binding_id,confirm_destroy=True)
                    except Exception as error:
                        failures.append(error);logging.getLogger(__name__).error('sandbox_result_gate_cleanup_failed error_type=%s',type(error).__name__)
            if failures and primary is None:raise failures[0]
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True);parser.add_argument('--image',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nyt-result-gate-') as directory:
        report=asyncio.run(scenario(Path(directory).resolve(),args.context,args.image))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
