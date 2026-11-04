"""The full probe drives two controlled tasks and real capture/result/file bytes."""
import json,shutil,threading,time
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask
from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary,ResultLimits
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.client import CloudConnection
from scripts.cloud_workflow.settings import WorkflowSettings
from scripts.verify_cloud_workflow import run


def test_two_tasks_two_rounds_and_resume_never_submit_another_run(tmp_path,monkeypatch):
    import scripts.cloud_workflow.scenario as module
    source=tmp_path/'source';source.mkdir();(source/'main.txt').write_text('input')
    selection=tmp_path/'selection.json';selection.write_text(json.dumps({'paths':['main.txt']}));selection.chmod(0o600)
    connection=tmp_path/'connection.json';instance=str(uuid4())
    connection.write_text(json.dumps(dict(endpoint='http://127.0.0.1:12345',owner_authority='127.0.0.1:12345',
        owner_token='test-only',instance_id=instance)));connection.chmod(0o600)
    output=tmp_path/'probe';receipts={};tasks={};results={};blobs={};uploaded=set();posts=[];lock=threading.RLock()
    class Client:
        def __init__(self,settings):self.settings=settings
        def close(self):pass
        def upload(self,directory):
            captured=json.loads((directory/'receipt.json').read_text());ident=captured['input_id'];hit=ident in uploaded;uploaded.add(ident)
            return dict(input_id=ident,uploaded_bytes=0 if hit else captured['size'],cache_hit=hit)
        def download(self,task_id,artifact_id,destination):destination.write_bytes(blobs[artifact_id])
        def request(self,method,path,**kwargs):
            with lock:return self.call(method,path,**kwargs)
        def call(self,method,path,**kwargs):
            parts=path.strip('/').split('/')
            if method=='GET':
                if path=='/instance':return dict(instance_id=self.settings.instance_id,protocol=1)
                if parts[0]=='commands':
                    if parts[1] not in receipts:raise CloudError('command_not_found',404)
                    return receipts[parts[1]]
                if len(parts)==2:return tasks[parts[1]].model_dump(mode='json')
                record=results[parts[3]]
                if record['plan']['task_id']!=parts[1]:raise CloudError('result_not_found',404)
                return record
            body=kwargs['json']
            if parts[-1] in ('lease','release'):return {}
            cid=body['command_id'];posts.append((path,cid));result={}
            if path=='/projects':result={'project':{'project_id':str(uuid4())}}
            elif path=='/tasks':
                task=CloudTask(task_id=uuid4().hex,project_id=body['project_id'],title=body['title'],input_id=body['input_id'],
                    template_id=body['template_id'],runner_release_id='candidate',state_schema=1,generation=1,revision=1,
                    lifecycle='stopped',created_at=time.time(),updated_at=time.time())
                tasks[task.task_id]=task;result={'task':task.model_dump(mode='json')}
            elif parts[-1]=='commands':
                task=tasks[parts[1]].model_copy(update=dict(lifecycle='ready',runner_phase='ready',sandbox_id='fixture'))
                tasks[task.task_id]=task
            else:
                task=tasks[parts[1]];root=tmp_path/task.task_id
                if not root.exists():shutil.copytree(source,root)
                plan=ResultPlan(result_id=str(uuid4()),source_command_id=cid,source_run_id=uuid4().hex,task_id=task.task_id,
                    project_id=task.project_id,input_id=task.input_id,source_generation=1,output_directory='.nyt-output')
                reports=root/plan.output_directory/plan.source_run_id;reports.mkdir(parents=True)
                (reports/'acceptance.txt').write_text(body['message'])
                spool=tmp_path/('spools-'+task.task_id)/plan.result_id
                freeze_result(root,output/'capture-0',spool,plan,ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed'),
                    1,ResultLimits(),settings.input_limits)
                record=json.loads((spool/'result.json').read_text());record['phase']='available';results[plan.result_id]=record
                for f in record['bundle']['files']:blobs[f['artifact']['artifact_id']]=(spool/'files'/f['artifact']['artifact_id']).read_bytes()
                result={'command':{'result_plan':plan.model_dump(mode='json')}}
            receipt=dict(command_id=cid,status='succeeded',result=result);receipts[cid]=receipt
            return {'receipt':receipt} if path=='/projects' else receipt
    monkeypatch.setattr(module,'CloudClient',Client)
    from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL
    # The fixture and production probe must use the same declared protocol.
    assert OWNER_PROTOCOL==1
    settings=WorkflowSettings(connection_file=connection,source=source,selection_file=selection,
        template_id='current',model_id='chat',message='{task_marker}',rounds=2)
    report=run(settings,output,confirm_runs=4)
    assert report['status']=='passed',report
    assert report['cross_task_result_read']=='passed' and len(report['tasks'])==2
    assert report['metrics']['round_total_seconds']['samples']==4
    before=list(posts);assert run(settings,output,confirm_runs=4,resume=True)['status']=='passed'
    assert posts==before and len([p for p,_ in posts if p.endswith('/runs')])==4
    assert (source/'main.txt').read_text()=='input' and len(list(source.iterdir()))==1
    public=(output/'report.json').read_text()
    assert 'test-only' not in public and '127.0.0.1' not in public and settings.message not in public
    replacement=json.loads(connection.read_text());replacement['instance_id']=str(uuid4())
    connection.write_text(json.dumps(replacement))
    rejected=run(settings,output,confirm_runs=4,resume=True)
    assert rejected['status']=='failed' and rejected['error_code']=='probe_instance_changed'
    assert posts==before
