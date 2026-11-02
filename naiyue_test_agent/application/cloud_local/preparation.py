"""Resume the original project/input/task/environment steps; never start a model."""
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask,CloudProject,CommandReceipt
from naiyue_test_agent.contracts.cloud_lifecycle import EnvironmentCommand
from naiyue_test_agent.contracts.cloud_local_tasks import checked_task


def receipt(client,path,body,operation,fingerprint,*,dispatch=True,reconcile=False):
    found=True
    try:raw=client.request('GET','/commands/'+body['command_id'])
    except CloudError as error:
        if error.code!='command_not_found' or error.status!=404:raise
        if not dispatch:raise CloudError('cloud_preparation_unknown') from None
        found=False
        raw=client.request('POST',path,json=body)
        raw=raw.get('receipt',raw)
    for can_reconcile in (found and reconcile,False):
        try:value=CommandReceipt.model_validate(raw)
        except ValidationError:raise CloudError('cloud_receipt_invalid',502) from None
        if value.command_id!=body['command_id'] or value.operation!=operation or value.request_digest!=fingerprint:
            raise CloudError('cloud_receipt_mismatch',502)
        if value.status=='failed':raise CloudError(value.error_code or 'cloud_command_failed')
        if value.status=='succeeded':return value
        if not can_reconcile:raise CloudError('cloud_preparation_pending',409)
        raw=client.request('POST','/commands/'+body['command_id']+'/reconcile',json={})


def reconcile_created(domain,client,binding,session_id):
    op=domain.repo.operation(binding['create_command_id'],session_id)
    if not op['may_have_task']:return binding
    request=dict(command_id=op['steps']['task']['command_id'],project_id=binding['cloud_project_id'],
        input_id=binding['input_id'],template_id=binding['template_id'],title='')
    value=receipt(client,'/tasks',request,'task:create',digest({'operation':'task:create',
        'instance_id':binding['instance_id'],'request':{k:v for k,v in request.items() if k!='command_id'}}),dispatch=False)
    task=checked_task(value.result.get('task'),binding)
    if value.resource_id!=task.task_id:raise CloudError('cloud_task_identity_mismatch',502)
    op['steps']['task']['receipt']=value.model_dump(mode='json')
    return domain.repo.record_task(op['command_id'],session_id,task.model_dump(mode='json'),steps=op['steps'])['binding']


def prepare(domain,client,command_id,session_id):
    repo=domain.repo;op=repo.operation(command_id,session_id);binding=repo.binding(op['binding_id'],session_id)
    if op['cancel_requested']:return
    if op['phase']=='prepared':return
    steps=op['steps']
    def save(**kwargs):return repo.update(command_id,session_id,**kwargs)
    save(phase='project')
    body={k:steps['project'][k] for k in ('command_id','name')}
    project_receipt=receipt(client,'/projects',body,'project:create',digest({
        'operation':'project:create','instance_id':binding['instance_id'],'payload':{'name':body['name']}}))
    try:project=CloudProject.model_validate(project_receipt.result['project'])
    except (KeyError,ValidationError):raise CloudError('cloud_project_invalid',502) from None
    if project_receipt.resource_id!=project.project_id:raise CloudError('cloud_project_invalid',502)
    steps['project']['receipt']=project_receipt.model_dump(mode='json')
    result=save(phase='upload',steps=steps,binding={'cloud_project_id':project.project_id});binding=result['binding']
    source=repo.source(op['request']['source_handle'],session_id)
    from pathlib import Path
    uploaded=client.upload(Path(source['captured']['directory']))
    if uploaded.get('input_id')!=binding['input_id']:raise CloudError('cloud_input_identity_mismatch',502)
    # Cancellation and the first potentially side-effectful task dispatch are atomic.
    task_request=steps['task'].get('request') or dict(command_id=steps['task']['command_id'],project_id=project.project_id,
        input_id=binding['input_id'],template_id=binding['template_id'],title='')
    steps['task']['request']=task_request
    save(phase='task',steps=steps,dispatch_task=True)
    try:
        task_receipt=receipt(client,'/tasks',task_request,'task:create',digest({
            'operation':'task:create','instance_id':binding['instance_id'],
            'request':{k:v for k,v in task_request.items() if k!='command_id'}}))
    except CloudError as error:
        # These owner admission failures occur before task/receipt commit. A
        # transport error, unexpected response or missing ACK is never proof.
        if error.status in (404,409) and error.code in ('environment_retained_capacity','command_capacity',
                'project_not_found','input_not_ready','template_unavailable'):
            repo.reject_task(command_id,session_id)
        raise
    task=checked_task(task_receipt.result.get('task'),binding)
    if task_receipt.resource_id!=task.task_id:raise CloudError('cloud_task_identity_mismatch',502)
    # Persist identity even when cancellation arrived during the remote call.
    steps['task']['receipt']=task_receipt.model_dump(mode='json')
    result=repo.record_task(command_id,session_id,task.model_dump(mode='json'),steps=steps);binding=result['binding']
    if result['operation']['cancel_requested']:return
    if 'request' not in steps['environment']:
        steps['environment']['request']=EnvironmentCommand(command_id=steps['environment']['command_id'],kind='start',
            expected_revision=task.revision,expected_generation=task.generation).model_dump(mode='json')
        save(phase='environment',steps=steps)
    body=steps['environment']['request']
    environment_receipt=receipt(client,'/tasks/'+task.task_id+'/commands',body,'environment:start',
        digest({'task_id':task.task_id,'request':body,'idle_cutoff':None}),reconcile=op['phase']!='reserved')
    steps['environment']['receipt']=environment_receipt.model_dump(mode='json')
    save(phase='environment',steps=steps)
    current=checked_task(client.request('GET','/tasks/'+task.task_id),binding)
    if current.environment_phase!='backend_ready':raise CloudError('cloud_preparation_pending')
    repo.record_task(command_id,session_id,current.model_dump(mode='json'))
    latest=repo.operation(command_id,session_id)
    if not latest['cancel_requested']:save(phase='prepared')
