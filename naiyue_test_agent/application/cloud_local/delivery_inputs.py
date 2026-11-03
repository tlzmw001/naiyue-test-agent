"""Recover the exact imported baseline, without trusting remote local paths."""
import hashlib
import os
from pathlib import Path
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_readonly,open_store_file,sync_store_directory


def source_for(domain,record):
    binding=domain.repo.binding(record.binding_id,record.local_session_id)
    if (binding['instance_id']!=record.instance_id or binding['task_id']!=record.task_id
            or binding['input_id']!=record.input_id or binding['local_project_id']!=record.local_project_id
            or record.local_project_id!=domain.project_id or not record.source_handle):
        raise CloudError('cloud_delivery_input_mismatch')
    source=domain.repo.source(record.source_handle,record.local_session_id)
    if (source['captured']['input_id']!=record.input_id or source['local_project_id']!=record.local_project_id
            or source['local_session_id']!=record.local_session_id):raise CloudError('cloud_delivery_input_mismatch')
    return binding,source


def recover_archive(client,package,destination,limits):
    """The fixed package binds every Range, then full SHA256 binds the final file."""
    destination=Path(destination);partial=destination.with_suffix('.part')
    descriptor=open_store_file(partial);sha=hashlib.sha256();size=0
    with os.fdopen(descriptor,'wb') as stream:
        stream.truncate(0)
        for offset in range(0,package.size,limits.chunk_bytes):
            end=min(package.size,offset+limits.chunk_bytes)-1
            with client.http.stream('GET','/api/cloud/v1/inputs/'+package.input_id+'/archive',
                    headers={'Range':f'bytes={offset}-{end}','If-Range':'"'+package.archive_sha256+'"'}) as response:
                if (response.status_code!=206 or response.headers.get('etag')!='"'+package.archive_sha256+'"'
                        or response.headers.get('content-range')!=f'bytes {offset}-{end}/{package.size}'
                        or response.headers.get('content-length')!=str(end-offset+1)):raise CloudError('cloud_delivery_input_changed')
                count=0
                for chunk in response.iter_bytes(limits.io_bytes):
                    count+=len(chunk);size+=len(chunk)
                    if count>end-offset+1 or size>package.size:raise CloudError('cloud_delivery_input_changed')
                    stream.write(chunk);sha.update(chunk)
                if count!=end-offset+1:raise CloudError('cloud_delivery_input_changed')
        stream.flush();os.fsync(stream.fileno())
    if size!=package.size or sha.hexdigest()!=package.archive_sha256:raise CloudError('cloud_delivery_input_changed')
    os.replace(partial,destination);sync_store_directory(destination.parent)
    return destination


def original_archive(source,package):
    path=Path(source['captured']['directory'])/'archive'
    if not os.path.lexists(path):return None
    # No symlinks or foreign cache paths: open_store_readonly checks the file.
    with os.fdopen(open_store_readonly(path),'rb') as stream:
        if os.fstat(stream.fileno()).st_size!=package.size:raise CloudError('cloud_delivery_input_changed')
    return path
