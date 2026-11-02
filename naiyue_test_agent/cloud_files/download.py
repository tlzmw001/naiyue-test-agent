"""Bounded verified attachment download, published without replacing user files."""
from contextlib import ExitStack
import hashlib
import logging
import os
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import TypeAdapter

from naiyue_test_agent.contracts.cloud_tasks import CloudError,TaskId,UUIDText
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
from naiyue_test_agent.storage.files import check_store_directory,open_store_file,sync_store_directory

logger=logging.getLogger(__name__)


def download(client,task_id,artifact_id,destination):
    TypeAdapter(TaskId).validate_python(task_id);TypeAdapter(UUIDText).validate_python(artifact_id)
    destination=Path(destination).expanduser().absolute()
    check_store_directory(destination.parent)
    if os.path.lexists(destination):raise CloudError('artifact_destination_exists')
    path='/tasks/'+task_id+'/artifacts/'+artifact_id
    record=ArtifactRecord.model_validate(client.request('GET',path))
    if record.task_id!=task_id or record.artifact_id!=artifact_id:raise CloudError('artifact_identity_mismatch')
    temporary=destination.parent/('.nyt-download-'+str(uuid4()))
    created=False;stage_fd=None;stage_identity=None
    try:
        temporary.mkdir(mode=0o700);created=True
        stage_identity=temporary.lstat().st_dev,temporary.lstat().st_ino
        with ExitStack() as stack:
            parent_fd=None
            if os.open in os.supports_dir_fd:
                parent_fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,parent_fd)
                stage_fd=os.open(temporary,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,stage_fd)
                if (os.fstat(stage_fd).st_dev,os.fstat(stage_fd).st_ino)!=stage_identity:raise CloudError('artifact_destination_changed')
            fd=(os.open('bytes',os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=stage_fd)
                if stage_fd is not None else open_store_file(temporary/'bytes',exclusive_create=True))
            stream=stack.enter_context(os.fdopen(fd,'wb'))
            with client.http.stream('GET','/api/cloud/v1'+path+'/bytes') as response:
                if (response.status_code!=200 or response.headers.get('content-type')!='application/octet-stream'
                        or response.headers.get('content-encoding') not in (None,'identity')
                        or response.headers.get('content-length')!=str(record.size)
                        or response.headers.get('etag')!='"'+record.sha256+'"'):
                    raise CloudError('artifact_download_rejected',502)
                size=0;sha=hashlib.sha256()
                for chunk in response.iter_bytes(client.settings.io_bytes):
                    size+=len(chunk)
                    if size>record.size:raise CloudError('artifact_size_mismatch',502)
                    stream.write(chunk);sha.update(chunk)
                if size!=record.size or sha.hexdigest()!=record.sha256:raise CloudError('artifact_bytes_mismatch',502)
            stream.flush();os.fsync(stream.fileno());source=os.fstat(stream.fileno())
            check_store_directory(destination.parent)
            try:
                if stage_fd is not None:
                    os.link('bytes',destination.name,src_dir_fd=stage_fd,dst_dir_fd=parent_fd,follow_symlinks=False)
                    published=os.stat(destination.name,dir_fd=parent_fd,follow_symlinks=False)
                else:
                    # Win32 opener excludes FILE_SHARE_DELETE: retain that handle until publication.
                    os.link(temporary/'bytes',destination,follow_symlinks=False)
                    published=destination.lstat()
            except FileExistsError:raise CloudError('artifact_destination_exists') from None
            if (published.st_dev,published.st_ino,published.st_size)!=(source.st_dev,source.st_ino,source.st_size):
                raise CloudError('artifact_destination_changed')
            # Never remove destination after publication, even if a later directory sync fails.
            if stage_fd is not None:os.unlink('bytes',dir_fd=stage_fd);os.fsync(parent_fd)
        if (temporary.lstat().st_dev,temporary.lstat().st_ino)!=stage_identity:raise CloudError('artifact_destination_changed')
        if stage_fd is None:
            named=(temporary/'bytes').lstat()
            if (named.st_dev,named.st_ino)!=(source.st_dev,source.st_ino):raise CloudError('artifact_destination_changed')
            (temporary/'bytes').unlink()
        temporary.rmdir();created=False;sync_store_directory(destination.parent)
        return dict(artifact_id=artifact_id,sha256=record.sha256,size=record.size,path=str(destination))
    except (OSError,httpx.HTTPError) as error:
        logger.warning('cloud_artifact_download_failed error_type=%s',type(error).__name__)
        raise CloudError('artifact_download_unconfirmed',503) from None
    finally:
        if created:
            try:
                if (temporary.lstat().st_dev,temporary.lstat().st_ino)!=stage_identity:raise CloudError('artifact_destination_changed')
                if (temporary/'bytes').exists():(temporary/'bytes').unlink()
                temporary.rmdir()
            except (OSError,CloudError) as error:logger.error('cloud_download_cleanup_failed error_type=%s',type(error).__name__)
