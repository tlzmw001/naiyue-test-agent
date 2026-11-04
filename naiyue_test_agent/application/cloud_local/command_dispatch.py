"""Frozen owner commands: ACK is not terminal history; observation never replays."""
import time
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt
from naiyue_test_agent.contracts.cloud_runner import StartRun,TaskControl,RunCommand,RunnerActionCommand
from naiyue_test_agent.contracts.cloud_lifecycle import EnvironmentCommand
from naiyue_test_agent.contracts.cloud_delete import DeleteTask
from naiyue_test_agent.contracts.cloud_local_tasks import checked_task


def validate(raw,op,step):
    try:
        receipt=CommandReceipt.model_validate(raw);item=op['steps'][step];payload=item['request']
        if (receipt.command_id!=item['command_id'] or receipt.resource_id!=op['task_id']
                or receipt.operation!=item['operation'] or receipt.request_digest!=item['request_digest']
                or not item['operation'].startswith('result:') and receipt.expected_generation!=payload['expected_generation']):raise CloudError('cloud_receipt_mismatch',502)
        if item['operation'].startswith('result:') and (receipt.result.get('result_id')!=op['request']['result_id']
                or receipt.result.get('revision')!=payload['expected_revision']+1):raise CloudError('cloud_receipt_mismatch',502)
        if step=='primary' and op['kind']=='message':
            command=RunCommand.model_validate(receipt.result['command'])
            if (command.command_id!=receipt.command_id or command.message!=payload['message']
                    or command.model_id!=payload['model_id'] or command.thinking_level!=payload.get('thinking_level','model_default')):raise CloudError('cloud_receipt_mismatch',502)
        if step=='primary' and op['kind'] in ('stop','recover'):
            command=RunnerActionCommand.model_validate(receipt.result['command'])
            if any(getattr(command,k)!=payload.get(k) for k in ('command_id','kind','run_id','checkpoint_id')):
                raise CloudError('cloud_receipt_mismatch',502)
        return receipt.model_dump(mode='json')
    except (ValidationError,KeyError,TypeError):raise CloudError('cloud_receipt_invalid',502) from None


def fresh(domain,client,op,*,timeout=None):
    binding=domain.repo.binding(op['binding_id'],op['local_session_id'])
    if binding['task_id']!=op['task_id'] or binding['instance_id']!=op['instance_id']:raise CloudError('cloud_binding_changed')
    options={} if timeout is None else {'timeout':timeout}
    task=checked_task(client.request('GET','/tasks/'+op['task_id'],**options),binding)
    domain.repo.cache_task(binding['binding_id'],op['local_session_id'],task.model_dump(mode='json'))
    return task


def fixed(domain,op,step,body,operation):
    path='/tasks/'+op['task_id']+('/delete' if operation=='task:delete' else '/runs' if operation=='run:start' else '/control' if operation.startswith('control:') else '/commands')
    data=dict(task_id=op['task_id'],request=body)
    if operation.startswith('result:'):
        action=operation.split(':',1)[1];path='/tasks/'+op['task_id']+'/results/'+op['request']['result_id']+'/'+action
        data=dict(task_id=op['task_id'],result_id=op['request']['result_id'],action=action,request=body)
    if operation.startswith('environment:'):data['idle_cutoff']=None
    return domain.repo.commands.fix(op['command_id'],op['local_session_id'],step,body,path,operation,digest(data))


def send(domain,client,op,step,*,explicit=False):
    item=op['steps'][step];journal=domain.repo.commands;cid=op['command_id'];sid=op['local_session_id']
    try:raw=client.request('GET','/commands/'+item['command_id'])
    except CloudError as error:
        if error.code!='command_not_found' or error.status!=404:raise
        if item['sent'] and not (explicit and item['operation'].startswith(('control:','task:delete','result:'))):
            if item.get('rejected'):raise CloudError(item['rejected']) from None
            raise CloudError('cloud_command_unknown') from None
        op=journal.dispatch(cid,sid,step);item=op['steps'][step]
        try:raw=client.request('POST',item['path'],json=item['request'])
        except CloudError as rejected:
            if rejected.code in ('runner_not_ready','model_unavailable','unsupported_thinking_level','model_profile_changed','task_revision_conflict','run_identity_mismatch',
                    'task_busy','control_unavailable','request_too_large','invalid_request','environment_already_running',
                    'command_capacity','result_output_conflict','result_capacity','result_busy','result_not_retryable','result_revision_conflict','environment_capacity','environment_retained_capacity',
                    'task_delete_requires_stop','task_delete_confirmation_changed','task_not_available',
                    'resource_disk_capacity','resource_disk_unknown'):
                journal.reject(cid,sid,step,rejected.code)
            raise
    value=validate(raw,op,step)
    if explicit and item['sent'] and value['status'] not in ('succeeded','failed'):
        if item['operation'].startswith('environment:') or item['operation']=='task:delete':
            value=validate(client.request('POST','/commands/'+item['command_id']+'/reconcile',json={}),op,step)
        elif item['operation'].startswith('control:'):
            value=validate(client.request('POST',item['path'],json=item['request']),op,step)
    journal.record(cid,sid,step,value)
    return value


def observe(domain,client,op):
    for step,item in op['steps'].items():
        if 'request' not in item or not item['sent']:continue
        try:raw=client.request('GET','/commands/'+item['command_id'])
        except CloudError as error:
            if error.code!='command_not_found' or error.status!=404:raise
            if item.get('rejected'):continue
            domain.repo.commands.fail(op['command_id'],op['local_session_id'],'unknown','cloud_command_unknown')
            continue
        current=domain.repo.commands.record(op['command_id'],op['local_session_id'],step,validate(raw,op,step))
        if op['kind']=='delete' and current['phase']=='succeeded':return current
    fresh(domain,client,op)
    return domain.repo.operation(op['command_id'],op['local_session_id'])


def execute(domain,client,op,settings,*,explicit=False):
    sid=op['local_session_id'];cid=op['command_id'];journal=domain.repo.commands
    if 'request' in op['steps']['primary']:
        value=send(domain,client,op,'primary',explicit=explicit)
        if op['kind']=='delete' and value['status']=='succeeded':return
        fresh(domain,client,op);return
    task=fresh(domain,client,op);kind=op['kind'];request=op['request']
    if kind=='delete':
        body=DeleteTask(command_id=cid,confirm_task_id=op['task_id'],expected_revision=request['expected_revision'],
            expected_generation=request['expected_generation'],preview_digest=request['preview_digest'],
            acknowledge_unknown=request['acknowledge_unknown']).model_dump(mode='json')
        operation='task:delete'
    elif kind in ('result_retry','result_abandon'):
        from naiyue_test_agent.contracts.cloud_results import ResultAction
        body=ResultAction(command_id=cid,expected_revision=request['expected_revision']).model_dump(mode='json')
        operation='result:'+request['action']
    elif kind=='message':
        if task.environment_phase=='absent' or 'request' in op['steps']['environment']:
            if 'request' not in op['steps']['environment']:
                body=EnvironmentCommand(command_id=op['steps']['environment']['command_id'],kind='start',
                    expected_revision=task.revision,expected_generation=task.generation).model_dump(mode='json')
                op=fixed(domain,op,'environment',body,'environment:start')
            value=send(domain,client,op,'environment',explicit=explicit)
            if value['status']!='succeeded':raise CloudError('cloud_environment_pending')
            deadline=time.monotonic()+settings.runner_wait_seconds
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise CloudError('cloud_runner_pending')
                task=fresh(domain,client,op,timeout=min(remaining,client.settings.timeout_seconds))
                if task.runner_phase=='ready':break
                if task.runner_phase=='waiting_model':raise CloudError('model_unavailable')
                if time.monotonic()>=deadline:raise CloudError('cloud_runner_pending')
                time.sleep(settings.runner_poll_seconds)
        if task.runner_phase=='waiting_model':raise CloudError('model_unavailable')
        if (task.lifecycle!='ready' or task.runner_phase!='ready' or task.active_run_id
                or task.control_operation_id or task.environment_operation_id or task.artifact_operation_id
                or any(x not in ('background_commands','result_pending') for x in task.reclaim_blockers)):
            raise CloudError('runner_not_ready')
        if request['model_id'] not in task.runner_model_ids:raise CloudError('model_unavailable')
        body=StartRun(command_id=cid,expected_revision=task.revision,expected_generation=task.generation,
            message=request['text'],model_id=request['model_id'],thinking_level=request.get('thinking_level','model_default')).model_dump(mode='json')
        operation='run:start'
    elif kind in ('stop','recover'):
        if kind=='recover':
            recovery=task.runner_recovery or {}
            if (not recovery or recovery.get('run_id')!=request['run_id']
                    or recovery.get('checkpoint_id')!=request['checkpoint_id']):raise CloudError('cloud_recovery_changed')
        body=TaskControl(command_id=cid,kind=kind,expected_revision=task.revision,expected_generation=task.generation,
            run_id=request['run_id'],checkpoint_id=request['checkpoint_id']).model_dump(mode='json')
        operation='control:'+kind
    else:
        if kind=='release':
            if request['acknowledge_unknown']:
                if request['expected_generation']!=task.generation:raise CloudError('cloud_generation_changed')
                if any(getattr(task,k)!=v for k,v in op['confirmation'].items()):raise CloudError('cloud_confirmation_changed')
            elif (task.active_run_id or task.reclaim_blockers or task.control_operation_id
                    or task.artifact_operation_id or task.environment_operation_id
                    or task.lifecycle=='ready' and task.last_quiescent_at is None):raise CloudError('cloud_task_not_quiescent')
        body=EnvironmentCommand(command_id=cid,kind='start' if kind=='start' else 'stop_environment',
            expected_revision=task.revision,expected_generation=task.generation,
            acknowledge_unknown=request['acknowledge_unknown']).model_dump(mode='json')
        operation='environment:'+body['kind']
    op=fixed(domain,op,'primary',body,operation)
    value=send(domain,client,op,'primary',explicit=explicit)
    if kind=='delete' and value['status']=='succeeded':return
    fresh(domain,client,op)
