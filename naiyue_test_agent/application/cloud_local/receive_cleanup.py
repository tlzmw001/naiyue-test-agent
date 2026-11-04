"""Explicit orphan-only reclamation under the local cache's exclusive IO reservation."""
import logging
import os
import re
import stat
from naiyue_test_agent.cloud_files.delivery_paths import parent,lstat
from naiyue_test_agent.storage.files import sync_store_directory
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def reclaim(files,repo,limits):
    hashes,stages=repo.cache_references()
    result=dict(removed_files=0,freed_bytes=0)
    budget=limits.max_results*(limits.max_files*3+4)
    root=files.directory()

    def entries(relative):
        nonlocal budget
        with parent(root,relative+'/.scan') as (fd,directory,_):
            with os.scandir(fd if fd is not None else directory) as rows:
                for item in rows:
                    if budget<=0:return
                    budget-=1
                    yield item.name,item.stat(follow_symlinks=False)

    def remove(relative,observed):
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink!=1 or getattr(observed,'st_file_attributes',0)&0x400:
            raise CloudError('cloud_receive_cache_unavailable',503)
        with parent(root,relative) as (fd,directory,name):
            current=lstat(fd,directory,name)
            if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=(observed.st_dev,observed.st_ino,observed.st_size,observed.st_mtime_ns):
                raise CloudError('cloud_receive_source_changed')
            if fd is None:os.unlink(directory/name)
            else:os.unlink(name,dir_fd=fd)
            sync_store_directory(directory)
        result['removed_files']+=1;result['freed_bytes']+=observed.st_size

    try:
        if os.path.lexists(root/'blobs'):
            for name,info in entries('blobs'):
                if not re.fullmatch(r'[0-9a-f]{64}',name) or not stat.S_ISREG(info.st_mode):
                    raise CloudError('cloud_receive_cache_unavailable',503)
                if name not in hashes:remove('blobs/'+name,info)
        if os.path.lexists(root/'staging'):
            for rid,info in entries('staging'):
                if not re.fullmatch(UUID,rid) or not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                    raise CloudError('cloud_receive_cache_unavailable',503)
                for name,info in entries('staging/'+rid):
                    if not re.fullmatch(UUID+'|quarantine-[0-9a-f]{32}',name):raise CloudError('cloud_receive_cache_unavailable',503)
                    if name not in stages.get(rid,set()):remove('staging/'+rid+'/'+name,info)
                if rid not in stages:
                    with parent(root,'staging/'+rid) as (fd,directory,name):
                        try:
                            if fd is None:os.rmdir(directory/name)
                            else:os.rmdir(name,dir_fd=fd)
                        except OSError as error:
                            import errno
                            if error.errno not in (errno.ENOTEMPTY,errno.EEXIST):raise
                        else:sync_store_directory(directory)
        return result
    except (OSError,ValueError,CloudError) as error:
        logger.warning('cloud_receive_cleanup_failed error_type=%s removed_files=%s',type(error).__name__,result['removed_files'])
        if isinstance(error,CloudError):raise
        raise CloudError('cloud_receive_cache_unavailable',503) from error
