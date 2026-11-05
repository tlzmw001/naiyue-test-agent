"""Retry immutable captures, never capture again because an HTTP acknowledgement was lost."""
import asyncio
import hashlib
import os

from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled,acquire_blocking
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord,ArtifactTransfer
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_readonly
from .http import task_request


async def upload_artifact(client,value):
    worker=client.kernel.artifacts
    await upload_record(client,value.artifact,worker.blob(value.artifact.artifact_id))
    return await run_blocking(worker.available,value)


async def upload_record(client,record,blob):
    b=client.bootstrap
    path='/internal/cloud/v1/tasks/'+b.task_id+'/artifacts/'+record.artifact_id
    transfer=ArtifactTransfer.model_validate(await task_request(client.http,client.bootstrap,'POST',path+'/prepare',value=record.model_dump()))
    if transfer.artifact!=record:raise CloudError('artifact_identity_conflict')
    if transfer.state not in ('uploaded','available'):
        stream=await acquire_blocking(lambda:os.fdopen(open_store_readonly(blob),'rb'),
            lambda value:run_blocking(value.close))
        try:
            offset=transfer.offset
            await run_blocking(stream.seek,offset)
            while offset<record.size or record.size==0 and not transfer.segments:
                data=await run_blocking(stream.read,min(b.artifact_limits.segment_bytes,record.size-offset))
                if len(data)!=min(b.artifact_limits.segment_bytes,record.size-offset):raise CloudError('artifact_spool_changed')
                async with asyncio.timeout(b.artifact_limits.transfer_timeout_seconds):
                    raw=await task_request(client.http,client.bootstrap,'PUT',path+'/bytes',content=data,headers={
                        'Content-Type':'application/octet-stream','Content-Length':str(len(data)),
                        'X-Content-SHA256':hashlib.sha256(data).hexdigest(),'Upload-Offset':str(offset),'X-Upload-Segmented':'1'})
                transfer=ArtifactTransfer.model_validate(raw)
                if transfer.artifact!=record or transfer.offset!=offset+len(data):raise CloudError('artifact_identity_conflict')
                offset=transfer.offset
                if record.size==0:break
        finally:await await_settled(run_blocking(stream.close))
    received=ArtifactRecord.model_validate(await task_request(client.http,client.bootstrap,'POST',path+'/commit',value={}))
    if received!=record:raise CloudError('artifact_identity_conflict')
    return received


async def publish_artifacts(client):
    worker=client.kernel.artifacts
    if worker.active_task and not worker.active_task.done():return
    for value in await run_blocking(worker.journal.list):
        ident=value.command.command_id
        if value.status=='captured':
            value=await upload_artifact(client,value)
        if ident in client.artifact_reported:continue
        if value.status in ('available','failed'):
            await client.send(dict(kind='artifact_finished',command_id=ident,artifact_id=value.command.artifact_id,
                status=value.status,error_code=value.error_code,phase=client.kernel.phase,quiescent=client.kernel.session_quiet()))
            client.artifact_reported.add(ident)
        elif value.status=='unknown':
            await client.send(dict(kind='artifact_state',command_id=ident,artifact_id=value.command.artifact_id,status='unknown'))
            client.artifact_reported.add(ident)
