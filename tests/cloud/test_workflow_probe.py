"""Acceptance commands persist before IO and resume original side effects."""
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_unknown_ack_is_looked_up_before_any_original_id_retry(tmp_path):
    from scripts.cloud_workflow.commands import command
    from scripts.cloud_workflow.journal import Journal
    journal=Journal(tmp_path/'journal.json',{'probe_id':str(uuid4())})
    cid=str(uuid4());posts=[];remote={}
    class Client:
        def request(self,method,path,**kwargs):
            if method=='GET':
                if cid not in remote:raise CloudError('command_not_found',404)
                return remote[cid]
            assert journal.value['commands']['create']['body']==kwargs['json']
            posts.append(kwargs['json']);remote[cid]=dict(command_id=cid,status='succeeded',result={'project':{'project_id':str(uuid4())}})
            raise CloudError('cloud_connection_failed',503)
    with pytest.raises(CloudError):command(Client(),journal,'create',cid,'/projects',{'name':'probe'},timeout=1,poll=.01)
    value=command(Client(),journal,'create',cid,'/projects',{'name':'probe'},timeout=1,poll=.01)
    assert value['status']=='succeeded' and len(posts)==1 and posts[0]['command_id']==cid
    with pytest.raises(CloudError,match='probe_request_changed'):
        command(Client(),journal,'create',cid,'/projects',{'name':'other'},timeout=1,poll=.01)


def test_failed_or_unknown_receipt_never_becomes_success_or_new_command(tmp_path):
    from scripts.cloud_workflow.commands import command
    from scripts.cloud_workflow.journal import Journal
    journal=Journal(tmp_path/'journal.json',{'probe_id':str(uuid4())});cid=str(uuid4())
    class Client:
        def request(self,method,path,**kwargs):
            assert method=='GET'
            return dict(command_id=cid,status='unknown',error_code='runner_disconnected')
    with pytest.raises(CloudError,match='probe_command_unknown'):
        command(Client(),journal,'run',cid,'/runs',{},timeout=1,poll=.01)


def test_metrics_exclude_resumed_wall_clocks():
    from scripts.cloud_workflow.scenario import metrics
    value=metrics([{'resumed':False,'seconds':1.0},{'resumed':False,'seconds':3.0},{'resumed':True,'seconds':100.0}],'seconds')
    assert value==dict(samples=2,p50=1.0,p95=3.0,max=3.0)


def test_confirmation_precedes_output_or_network(tmp_path):
    from scripts.verify_cloud_workflow import run
    from scripts.cloud_workflow.settings import WorkflowSettings
    settings=WorkflowSettings(connection_file=tmp_path/'private',source=tmp_path/'source',selection_file=tmp_path/'selection',
        template_id='template',model_id='model',message='write {task_marker}')
    with pytest.raises(CloudError,match='probe_confirm_runs_required'):run(settings,tmp_path/'out',confirm_runs=1)
    assert not (tmp_path/'out').exists()
