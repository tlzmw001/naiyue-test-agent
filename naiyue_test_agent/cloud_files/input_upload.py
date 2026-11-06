"""One capture, one durable transfer identity, bounded unconfirmed-segment upload."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_inputs import CapturedInput,InputTransfer,InputPackage,CreateTransfer
from naiyue_test_agent.storage.files import open_store_readonly,open_store_file,sync_store_directory,windows_api_path
from .paths import identity


def upload(client,directory,*,upload_id=None,check_cancel=None,on_progress=None):
    if check_cancel is not None:check_cancel()
    with os.fdopen(open_store_readonly(directory/'receipt.json'),'rb') as stream:
        captured=CapturedInput.model_validate_json(stream.read(8193))
    if captured.directory!=directory:raise CloudError('input_capture_identity')
    try:package=InputPackage.model_validate(client.request('GET','/inputs/'+captured.input_id))
    except CloudError as error:
        if error.code!='input_not_found' or error.status!=404:raise
    else:
        if package.input_id!=captured.input_id:raise CloudError('input_capture_identity')
        if on_progress is not None:on_progress(captured.size,captured.size)
        return {'input_id':captured.input_id,'uploaded_bytes':0,'cache_hit':True}
    facts=dict(purpose='input',input_id=captured.input_id,size=captured.size,
        archive_sha256=captured.archive_sha256,codec=captured.codec)
    journal=directory/('upload-'+client.settings.instance_id+('-'+upload_id if upload_id else '')+'.json')
    if os.name=='nt':journal=Path(windows_api_path(journal))
    if journal.exists() or journal.is_symlink():
        with os.fdopen(open_store_readonly(journal),'rb') as stream:value=json.loads(stream.read(8193))
        value=CreateTransfer.model_validate(value).model_dump()
        if {k:v for k,v in value.items() if k!='command_id'}!=facts:raise CloudError('input_upload_identity')
    else:
        value=dict(facts,command_id=str(uuid4()));fd=open_store_file(journal,exclusive_create=True)
        with os.fdopen(fd,'w') as stream:
            stream.write(json.dumps(value));stream.flush();os.fsync(stream.fileno())
        sync_store_directory(directory)
    receipt=client.request('POST','/transfers',json=value)
    original=InputTransfer.model_validate(receipt['result']['transfer'])
    path='/transfers/'+original.transfer_id
    def checked(raw):
        transfer=InputTransfer.model_validate(raw)
        if (transfer.transfer_id!=original.transfer_id or transfer.command_id!=value['command_id']
                or any(getattr(transfer,k)!=v for k,v in facts.items()) or transfer.offset>transfer.size
                or transfer.state=='cancelled'):
            raise CloudError('input_upload_identity')
        return transfer
    transfer=checked(client.request('GET',path));sent=0
    if on_progress is not None:on_progress(transfer.offset,captured.size)
    if not transfer.cache_hit and transfer.state not in ('uploaded','committed'):
        with os.fdopen(open_store_readonly(directory/'archive'),'rb') as stream:
            before=identity(os.fstat(stream.fileno()));sha=hashlib.sha256()
            while chunk:=stream.read(client.settings.io_bytes):sha.update(chunk)
            if stream.tell()!=captured.size or sha.hexdigest()!=captured.archive_sha256:raise CloudError('input_capture_changed')
            while transfer.offset<captured.size:
                if check_cancel is not None:check_cancel()
                start=transfer.offset;length=min(transfer.chunk_bytes,captured.size-start);stream.seek(start)
                remaining=length;part=hashlib.sha256()
                while remaining:
                    chunk=stream.read(min(remaining,client.settings.io_bytes))
                    if not chunk:raise CloudError('input_capture_changed')
                    part.update(chunk);remaining-=len(chunk)
                stream.seek(start)
                def chunks():
                    remaining=length
                    while remaining:
                        if check_cancel is not None:check_cancel()
                        chunk=stream.read(min(remaining,client.settings.io_bytes))
                        if not chunk:raise CloudError('input_capture_changed')
                        remaining-=len(chunk);yield chunk
                transfer=checked(client.request('PUT',path+'/bytes',content=chunks(),headers={
                    'Content-Length':str(length),'Content-Type':'application/octet-stream','Upload-Offset':str(start),
                    'X-Content-SHA256':part.hexdigest()}))
                if transfer.offset!=start+length:raise CloudError('transfer_ack_invalid')
                sent+=length
                if on_progress is not None:on_progress(transfer.offset,captured.size)
            if identity(os.fstat(stream.fileno()))!=before:raise CloudError('input_capture_changed')
    client.request('POST',path+'/commit',json={})
    return {'input_id':captured.input_id,'uploaded_bytes':sent,'cache_hit':transfer.cache_hit}
