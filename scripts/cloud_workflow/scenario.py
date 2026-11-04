"""Two independent tasks, immutable input reuse, authoritative result downloads."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,math,os,time
from uuid import UUID,uuid5
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_files.client import CloudClient
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_install.store import directory
from naiyue_test_agent.contracts.cloud_inputs import Selection
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask
from naiyue_test_agent.contracts.cloud_results import ResultRecord
from naiyue_test_agent.storage.files import open_store_readonly
from .commands import command,wait
from .journal import Journal


def metrics(samples,key):
    values=sorted(s[key] for s in samples if not s.get('resumed') and s.get(key) is not None)
    if not values:return dict(samples=0,p50=None,p95=None,max=None)
    return dict(samples=len(values),p50=values[math.ceil(.5*len(values))-1],
        p95=values[math.ceil(.95*len(values))-1],max=values[-1])


def checked_file(path,artifact):
    sha=hashlib.sha256();amount=0
    with os.fdopen(open_store_readonly(path),'rb') as stream:
        while data:=stream.read(256*1024):
            amount+=len(data)
            if amount>artifact.size:raise CloudError('probe_download_changed')
            sha.update(data)
    if (amount,sha.hexdigest())!=(artifact.size,artifact.sha256):raise CloudError('probe_download_changed')


def task_scenario(settings,connection,output,root,number,project_id,input_id):
    journal=Journal(output/f'task-{number}.json',dict(probe_id=root['probe_id'],samples=[]))
    ns=UUID(root['probe_id']);client=CloudClient(connection);options=dict(timeout=settings.timeout_seconds,poll=settings.poll_seconds)
    def ident(key):return str(uuid5(ns,f'{number}:{key}'))
    def submit(key,path,body):
        # A new CAS snapshot is used only before the original request is saved.
        original=journal.value['commands'].get(key)
        return command(client,journal,key,ident(key),path,original['body'] if original else body,**options)
    try:
        created=submit('create','/tasks',dict(project_id=project_id,input_id=input_id,template_id=settings.template_id,title=f'acceptance task {number}'))
        task=CloudTask.model_validate(created['result']['task']);task_id=task.task_id;path='/tasks/'+task_id
        started_before='start' in journal.value['commands'];started_at=time.monotonic()
        if 'ready' not in journal.value:
            task=CloudTask.model_validate(client.request('GET',path))
            submit('start',path+'/commands',dict(kind='start',expected_revision=task.revision,expected_generation=task.generation))
            task=CloudTask.model_validate(wait(client,path,lambda t:t['runner_phase']=='ready' and t['lifecycle']=='ready',**options))
            journal.value.update(ready=task.model_dump(mode='json'),ready_seconds=None if started_before else time.monotonic()-started_at);journal.save()
        pinned=CloudTask.model_validate(journal.value['ready'])
        for n in range(settings.rounds):
            if n<len(journal.value['samples']):continue
            key=f'run-{n}';resumed=key in journal.value['commands'];begin=time.monotonic()
            current=CloudTask.model_validate(client.request('GET',path))
            if (current.generation,current.runner_release_id,current.sandbox_id)!=(pinned.generation,pinned.runner_release_id,pinned.sandbox_id):
                raise CloudError('probe_task_pin_changed')
            marker=f'{root["probe_id"]}:task-{number}:round-{n}'
            receipt=submit(key,path+'/runs',dict(expected_revision=current.revision,expected_generation=current.generation,
                message=settings.message.replace('{task_marker}',marker),model_id=settings.model_id))
            terminal=time.monotonic();plan=receipt['result']['command']['result_plan'];rid=plan['result_id']
            result_path=path+'/results/'+rid
            result=ResultRecord.model_validate(wait(client,result_path,lambda r:r['phase']=='available',**options));available=time.monotonic()
            p=result.plan
            if (p.task_id,p.project_id,p.input_id,p.source_command_id)!=(task_id,project_id,input_id,ident(key)) or p!=result.bundle.plan:
                raise CloudError('probe_result_identity')
            destination=directory(directory(output/'downloads')/rid)
            lease_id=ident('lease-'+str(n));client.request('POST',result_path+'/lease',json=dict(lease_id=lease_id))
            primary=None;report_found=False;amount=0
            try:
                for f in result.bundle.files:
                    a=f.artifact
                    if (a.task_id,a.project_id,a.input_id,a.command_id)!=(task_id,project_id,input_id,rid):raise CloudError('probe_result_identity')
                    target=destination/a.artifact_id
                    if not os.path.lexists(target):client.download(task_id,a.artifact_id,target)
                    checked_file(target,a);amount+=a.size
                    if f.role=='artifact' and a.relative_name==settings.expected_report:
                        with os.fdopen(open_store_readonly(target),'rb') as stream:content=stream.read(128*1024+1)
                        if len(content)>128*1024 or marker.encode() not in content:raise CloudError('probe_report_marker_missing')
                        report_found=True
                if not report_found:raise CloudError('probe_report_missing')
            except BaseException as error:primary=error;raise
            finally:
                try:client.request('POST',result_path+'/release',json=dict(lease_id=lease_id))
                except Exception:
                    # Preserve the original download failure. Failure remains
                    # visible in this private journal, not silently swallowed.
                    journal.value['lease_release_failed']=True;journal.save()
                    if primary is None:raise
            finish=time.monotonic()
            journal.value['samples'].append(dict(round=n,resumed=resumed,command_id=ident(key),result_id=rid,run_id=p.source_run_id,
                generation=current.generation,runner_release_id=current.runner_release_id,downloaded_bytes=amount,
                command_ack_seconds=journal.value.get('ack_seconds',{}).get(key),
                run_terminal_seconds=terminal-begin,terminal_to_available_seconds=available-terminal,
                download_seconds=finish-available,round_total_seconds=finish-begin))
            journal.save()
        return dict(task_id=task_id,project_id=project_id,input_id=input_id,generation=pinned.generation,
            runner_release_id=pinned.runner_release_id,ready_seconds=journal.value['ready_seconds'],samples=journal.value['samples'])
    finally:client.close()


def scenario(settings,connection,output,journal):
    client=CloudClient(connection);options=dict(timeout=settings.timeout_seconds,poll=settings.poll_seconds)
    try:
        identity=client.request('GET','/instance')
        from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL
        if (identity['instance_id'],identity['protocol'])!=(connection.instance_id,OWNER_PROTOCOL):raise CloudError('probe_instance_mismatch')
        bound=journal.value.get('instance_id')
        if bound is not None and bound!=connection.instance_id:raise CloudError('probe_instance_changed')
        if bound is None:journal.value['instance_id']=connection.instance_id;journal.save()
        selection=read_settings(settings.selection_file,Selection);captures=[]
        for n in range(2):
            dest=output/f'capture-{n}';begin=time.monotonic()
            if not dest.exists():capture(settings.source,selection,dest,settings.input_limits)
            captured=time.monotonic();uploaded=client.upload(dest)
            captures.append(dict(**uploaded,capture_seconds=captured-begin,upload_seconds=time.monotonic()-captured))
        if captures[0]['input_id']!=captures[1]['input_id'] or captures[1]['uploaded_bytes']!=0:raise CloudError('probe_input_reuse_failed')
        ns=UUID(journal.value['probe_id']);cid=str(uuid5(ns,'project'))
        receipt=command(client,journal,'project',cid,'/projects',dict(name='cloud workflow '+str(ns)[:8]),**options)
        project_id=receipt['result']['project']['project_id'];input_id=captures[0]['input_id']
        with ThreadPoolExecutor(max_workers=settings.task_count) as pool:
            futures=[pool.submit(task_scenario,settings,connection,output,journal.value,n,project_id,input_id) for n in range(settings.task_count)]
            results=[f.result() for f in futures]
        isolation='not_run_single_task'
        if len(results)==2:
            try:client.request('GET','/tasks/'+results[1]['task_id']+'/results/'+results[0]['samples'][-1]['result_id'])
            except CloudError as error:
                if (error.code,error.status)!=('result_not_found',404):raise
                isolation='passed'
            else:raise CloudError('probe_cross_task_result_visible')
        samples=[s for task in results for s in task['samples']]
        return dict(instance_id=connection.instance_id,input_id=input_id,project_id=project_id,captures=captures,tasks=results,
            cross_task_result_read=isolation,metrics={key:metrics(samples,key) for key in
                ('command_ack_seconds','run_terminal_seconds','terminal_to_available_seconds','download_seconds','round_total_seconds')})
    finally:client.close()
