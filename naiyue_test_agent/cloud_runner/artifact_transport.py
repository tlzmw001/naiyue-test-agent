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
            async def chunks():
                size=0;sha=hashlib.sha256()
                while data:=await run_blocking(stream.read,b.artifact_limits.io_bytes):
                    size+=len(data);sha.update(data)
                    if size>record.size:raise CloudError('artifact_spool_changed')
                    yield data
                if (size,sha.hexdigest())!=(record.size,record.sha256):raise CloudError('artifact_spool_changed')
            await task_request(client.http,client.bootstrap,'PUT',path+'/bytes',content=chunks(),headers={
                'Content-Type':'application/octet-stream','Content-Length':str(record.size),
                'X-Content-SHA256':record.sha256,'Upload-Offset':'0'})
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
            async with asyncio.timeout(client.bootstrap.artifact_limits.transfer_timeout_seconds):
                value=await upload_artifact(client,value)
        if ident in client.artifact_reported:continue
        if value.status in ('available','failed'):
            await client.send(dict(kind='artifact_finished',command_id=ident,artifact_id=value.command.artifact_id,
                status=value.status,error_code=value.error_code,phase=client.kernel.phase,quiescent=client.kernel.session_quiet()))
            client.artifact_reported.add(ident)
        elif value.status=='unknown':
            await client.send(dict(kind='artifact_state',command_id=ident,artifact_id=value.command.artifact_id,status='unknown'))
            client.artifact_reported.add(ident)
