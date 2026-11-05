"""Fixed Range segments acknowledge only fsynced local bytes, never hidden retries."""
import os,time
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .result_lease import renew
from naiyue_test_agent.storage.files import open_store_file,sync_store_directory


def transfer(client,files,record,index,repo,limits,deadline,explicit_retry=False):
    item=record.files[index];artifact=item.artifact;target=files.blob(artifact.sha256)
    if os.path.lexists(target):
        files.inspect(target,artifact.size,artifact.sha256)
    else:
        stage=files.stage(record.receive_id,artifact.artifact_id)
        fd=open_store_file(stage)
        with os.fdopen(fd,'r+b') as output:
            length=os.fstat(output.fileno()).st_size
            restart=length<item.offset
            if restart:
                if not explicit_retry or item.verified:raise CloudError('cloud_receive_offset_unconfirmed')
                item=item.model_copy(update={'offset':0})
            output.truncate(item.offset);output.seek(item.offset)
            output.flush();os.fsync(output.fileno());sync_store_directory(stage.parent)
            if restart:
                values=list(record.files);values[index]=item
                record=repo.save(record.model_copy(update={'files':values}))
            offset=item.offset
            while offset<artifact.size:
                segment_deadline=deadline if deadline is not None else time.monotonic()+limits.transfer_timeout_seconds
                remaining=segment_deadline-time.monotonic()
                if remaining<=0:raise CloudError('cloud_receive_timeout',408)
                renew(client,record)
                end=min(artifact.size,offset+limits.segment_bytes)-1
                headers={'Range':f'bytes={offset}-{end}','If-Range':'"'+artifact.sha256+'"','Accept-Encoding':'identity'}
                path='/api/cloud/v1/tasks/'+record.task_id+'/results/'+record.result_id+'/files/'+artifact.artifact_id+'/bytes'
                with client.http.stream('GET',path,headers=headers,timeout=min(limits.transfer_timeout_seconds,remaining)) as response:
                    if (response.status_code!=206 or response.headers.get('etag')!=headers['If-Range']
                            or response.headers.get('content-range')!=f'bytes {offset}-{end}/{artifact.size}'
                            or response.headers.get('content-length')!=str(end-offset+1)
                            or response.headers.get('content-encoding') not in (None,'identity')):
                        raise CloudError('cloud_receive_identity_mismatch',502)
                    count=0
                    for chunk in response.iter_bytes(min(limits.segment_bytes,256*1024)):
                        if time.monotonic()>=segment_deadline:raise CloudError('cloud_receive_timeout',408)
                        count+=len(chunk)
                        if count>end-offset+1:raise CloudError('cloud_receive_size_mismatch',502)
                        output.write(chunk)
                    if count!=end-offset+1:raise CloudError('cloud_receive_size_mismatch',502)
                output.flush();os.fsync(output.fileno());offset=end+1
                item=item.model_copy(update={'offset':offset})
                values=list(record.files);values[index]=item
                record=repo.save(record.model_copy(update={'files':values}))
            output.flush();os.fsync(output.fileno())
        sync_store_directory(stage.parent)
        files.publish(stage,artifact)
    item=item.model_copy(update={'offset':artifact.size,'verified':True})
    values=list(record.files);values[index]=item
    return repo.save(record.model_copy(update={'files':values}))
