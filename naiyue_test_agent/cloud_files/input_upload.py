"""One capture, one durable transfer identity, bounded unconfirmed-segment upload."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_inputs import CapturedInput,InputTransfer,InputPackage,CreateTransfer
from naiyue_test_agent.storage.files import open_store_readonly,open_store_file,sync_store_directory,windows_api_path
from .paths import identity


def _journal(directory,client,upload_id):
    path=directory/('upload-'+client.settings.instance_id+('-'+upload_id if upload_id else '')+'.json')
    return Path(windows_api_path(path)) if os.name=='nt' else path


def cancel_attachment_input(client,directory,ident,aid):
    # Release before looking up the journal command so a late transfer admission
    # must either already be recorded or reject the released use.
    client.release_operation_input(ident,aid)
    journal=_journal(directory,client,aid)
    try:
        with os.fdopen(open_store_readonly(journal),'rb') as stream:
            request=CreateTransfer.model_validate_json(stream.read(8193))
    except FileNotFoundError:return
    if request.input_id!=ident:raise CloudError('input_upload_identity')
    try:receipt=client.request('GET','/commands/'+request.command_id)
    except CloudError as error:
        if error.code=='command_not_found' and error.status==404:return
        raise
    fingerprint=digest({'operation':'input:transfer','instance_id':client.settings.instance_id,
                        'request':request.model_dump(exclude={'command_id'})})
    original=InputTransfer.model_validate(receipt.get('result',{}).get('transfer'))
    if (receipt.get('operation')!='input:transfer' or receipt.get('request_digest')!=fingerprint
            or receipt.get('command_id')!=request.command_id
            or receipt.get('resource_id')!=original.transfer_id
            or original.command_id!=request.command_id or original.input_id!=ident):
        raise CloudError('input_upload_identity')
    path='/transfers/'+original.transfer_id
    current=InputTransfer.model_validate(client.request('GET',path))
    if (current.transfer_id,current.command_id,current.input_id)!=(original.transfer_id,request.command_id,ident):
        raise CloudError('input_upload_identity')
    if current.state in ('committed','cancelled'):return
    try:cancelled=InputTransfer.model_validate(client.request('DELETE',path))
    except CloudError as error:
        if error.code=='input_already_committed':return
        raise
    if cancelled.transfer_id!=current.transfer_id or cancelled.state!='cancelled':
        raise CloudError('input_upload_identity')


def read_capture_receipt(directory):
    with os.fdopen(open_store_readonly(directory/'receipt.json'),'rb') as stream:
        return CapturedInput.model_validate_json(stream.read(8193))


def upload(client,directory,*,upload_id=None,check_cancel=None,on_progress=None,use_id=None):
    if check_cancel is not None:check_cancel()
    captured=read_capture_receipt(directory)
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
    journal=_journal(directory,client,upload_id)
    if journal.exists() or journal.is_symlink():
        with os.fdopen(open_store_readonly(journal),'rb') as stream:value=json.loads(stream.read(8193))
        value=CreateTransfer.model_validate(value).model_dump()
        if {k:v for k,v in value.items() if k!='command_id'}!=facts:raise CloudError('input_upload_identity')
    else:
        value=dict(facts,command_id=str(uuid4()));fd=open_store_file(journal,exclusive_create=True)
        with os.fdopen(fd,'w') as stream:
            stream.write(json.dumps(value));stream.flush();os.fsync(stream.fileno())
        sync_store_directory(directory)
    kwargs={'headers':{'X-Input-Use-Id':use_id}} if use_id else {}
    receipt=client.request('POST','/transfers',json=value,**kwargs)
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
    try:client.request('POST',path+'/commit',json={})
    except CloudError as error:
        if error.code=='input_retired' and transfer.state=='committed':
            journal.unlink()
            sync_store_directory(directory)
        raise
    return {'input_id':captured.input_id,'uploaded_bytes':sent,'cache_hit':transfer.cache_hit}
