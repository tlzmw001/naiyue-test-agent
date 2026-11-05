"""Explicit private content cache; local import evidence is never input authority."""
from contextlib import ExitStack
import os
import json
from pathlib import Path
import stat
import tempfile
from uuid import uuid4

from naiyue_test_agent.application.cloud.ownership import private_directory
from naiyue_test_agent.cloud_runner.state import read_json,write_json
from naiyue_test_agent.contracts.cloud_inputs import CapturedInput,InputPackage
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_file,acquire_store_lock,release_store_lock,sync_store_directory
from naiyue_test_agent.storage.files import open_store_readonly,check_store_directory
from naiyue_test_agent.storage.cloud.schema import private_file
from .capture import capture, publish_capture_directory
from .archive import validate_package


def _used(root):
    total=0
    for parent,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:
            private_directory(Path(parent)/name)
        for name in files:
            path=Path(parent)/name;info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise CloudError('input_cache_invalid')
            total+=info.st_size
    return total


def capture_cached(source,selection,cache,limits,*,codec='gzip',source_receipt=None):
    from naiyue_test_agent.contracts.cloud_inputs import Selection
    selection=Selection.model_validate(selection);source=Path(source).absolute();cache=Path(cache).absolute()
    if cache.is_relative_to(source) or source.is_relative_to(cache):raise CloudError('input_capture_overlap')
    parent=cache.parent
    while not parent.exists():parent=parent.parent
    check_store_directory(parent);cache.mkdir(mode=0o700,parents=True,exist_ok=True)
    marker=cache/'cache.json'
    signature=b'nyt-input-cache-init-v1\n';entries={p.name for p in cache.iterdir()}
    def initializing(header):return header==b'\0' or signature.startswith(header)
    if not marker.exists() and entries:
        if not entries<={'cache.lock','cache.json.next'} or 'cache.lock' not in entries:raise CloudError('input_cache_foreign')
        private_directory(cache);private_file(cache/'cache.lock')
        with os.fdopen(open_store_readonly(cache/'cache.lock'),'rb') as stream:
            if not initializing(stream.read(128)):raise CloudError('input_cache_foreign')
    private_directory(cache)
    with ExitStack() as stack:
        fd=open_store_file(cache/'cache.lock');stack.callback(os.close,fd)
        try:acquire_store_lock(fd)
        except BlockingIOError:raise CloudError('input_cache_busy') from None
        stack.callback(release_store_lock,fd)
        os.lseek(fd,0,os.SEEK_SET);header=os.read(fd,128)
        if header!=signature and initializing(header) and not marker.exists():
            os.lseek(fd,0,os.SEEK_SET);os.write(fd,signature);os.ftruncate(fd,len(signature))
            os.fsync(fd);sync_store_directory(cache)
        elif header!=signature:raise CloudError('input_cache_foreign')
        expected=dict(format='nyt-input-cache',schema=1)
        if marker.exists():
            if read_json(marker)!=expected:raise CloudError('input_cache_foreign')
        else:write_json(marker,expected)
        for name in ('inputs','staging','imports'):
            (cache/name).mkdir(mode=0o700,exist_ok=True);private_directory(cache/name)
        used=_used(cache)
        # Conservative temporary-byte reservation; no claim of a filesystem hard quota.
        size=sum((source/p).lstat().st_size for p in selection.paths)
        reserve=size*2+len(selection.paths)*8192+limits.max_metadata_bytes*2+32768
        if used+reserve>limits.max_cache_bytes:raise CloudError('input_cache_capacity')
        with tempfile.TemporaryDirectory(prefix='capture-',dir=cache/'staging') as temporary:
            local=Path(temporary);captured=capture(source,selection,local/'input',limits,codec=codec,source_receipt=source_receipt)
            target=cache/'inputs'/captured.input_id
            if target.exists() or target.is_symlink():
                private_directory(target);package=InputPackage.model_validate(read_json(target/'input.json',limits.max_metadata_bytes*4))
                validate_package(target/'archive',package.codec,limits,expected_id=captured.input_id,
                    expected_archive_sha256=package.archive_sha256,expected_size=package.size)
                result=CapturedInput.model_validate_json(json.dumps(read_json(target/'receipt.json')))
                if result.directory!=target or result.input_id!=captured.input_id:raise CloudError('input_cache_invalid')
            else:
                result=captured.model_copy(update={'directory':target,'source_receipt':None})
                write_json(captured.directory/'receipt.json',result.model_dump(mode='json'))
            if (captured.directory/'source.json').exists():(captured.directory/'source.json').unlink()
            receipt=None
            if source_receipt is not None:
                receipt=cache/'imports'/(str(uuid4())+'.json')
                write_json(local/'import.json',source_receipt.model_dump(mode='json'))
            # All final bytes exist before the quota decision. Subsequent publication only renames.
            if _used(cache)>limits.max_cache_bytes:raise CloudError('input_cache_capacity')
            if result.directory==target and not target.exists():
                sync_store_directory(captured.directory)
                publish_capture_directory(captured.directory,target)
                sync_store_directory(target.parent)
            if receipt is not None:
                os.rename(local/'import.json',receipt);sync_store_directory(receipt.parent)
                result=result.model_copy(update={'source_receipt':receipt})
            return result
