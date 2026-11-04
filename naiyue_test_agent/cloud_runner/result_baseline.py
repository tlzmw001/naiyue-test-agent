"""One private immutable I object store; per-round O objects never enter it."""
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from naiyue_test_agent.cloud_files.archive import validate_package
from naiyue_test_agent.cloud_files.delivery_paths import rename_new
from naiyue_test_agent.cloud_files.paths import identity,source_handle
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly,open_store_file,clear_store_readonly,sync_store_directory
from .result_git import ResultGit
from .state import read_json,write_json


@dataclass(frozen=True)
class Baseline:
    directory:Path
    tree:str
    known_content:dict
    object_ids:frozenset


def package_identity(package):
    raw=json.dumps({'input_id':package.input_id,'archive_sha256':package.archive_sha256,
        'size':package.size,'codec':package.codec},sort_keys=True).encode()
    return hashlib.sha256(raw+b'\0'+package.manifest.canonical+b'\0'+package.selection.canonical).hexdigest()


def cache_files(directory,limits,*,durable=False):
    """Private cache corruption check only; never used to skip hashing O."""
    pending=[directory/'objects.git'];files={};directories=[];seen=0
    while pending:
        parent=pending.pop();check_store_directory(parent);directories.append(parent)
        for name in sorted(os.listdir(parent)):
            path=parent/name;info=path.lstat();seen+=1
            if seen>limits.max_discovery_entries+1024:raise CloudError('result_baseline_changed')
            if stat.S_ISDIR(info.st_mode):pending.append(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                if durable:
                    # Native Git creates read-only objects. Only the unpublished
                    # private cache is made writable, before recording its identity.
                    if os.name=='nt':clear_store_readonly(path)
                    else:
                        with os.fdopen(open_store_readonly(path),'rb') as stream:os.fchmod(stream.fileno(),0o600)
                    with os.fdopen(open_store_file(path),'r+b') as stream:
                        opened=os.fstat(stream.fileno())
                        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise CloudError('result_baseline_changed')
                        os.fsync(stream.fileno());info=path.lstat()
                        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise CloudError('result_baseline_changed')
                files[path.relative_to(directory).as_posix()]=list(identity(info))
            else:raise CloudError('result_baseline_changed')
    if durable:
        for path in reversed(directories):sync_store_directory(path)
    return files


def load(directory,package,result_limits,input_limits):
    try:
        check_store_directory(directory)
        if set(os.listdir(directory))!={'objects.git','baseline.json'}:raise CloudError('result_baseline_changed')
        marker=read_json(directory/'baseline.json',input_limits.max_metadata_bytes*4+input_limits.max_discovery_entries*512)
        if (marker.get('schema')!=1 or marker.get('package')!=package_identity(package)
                or marker.get('files')!=cache_files(directory,input_limits)):
            raise CloudError('result_baseline_changed')
        tree=marker['tree']
        if not isinstance(tree,str) or len(tree)!=40 or any(c not in '0123456789abcdef' for c in tree):
            raise CloudError('result_baseline_changed')
        git=ResultGit(directory,result_limits,existing=True);entries=git.entries(tree)
        if set(entries)!={entry.path for entry in package.manifest.entries}:raise CloudError('result_baseline_changed')
        known={}
        for entry in package.manifest.entries:
            mode,oid=entries[entry.path]
            expected='120000' if entry.type=='symlink' else '100755' if entry.mode==0o755 else '100644'
            if mode!=expected or len(oid)!=40:raise CloudError('result_baseline_changed')
            key=(entry.size,entry.sha256)
            if key in known and known[key]!=oid:raise CloudError('result_baseline_changed')
            known[key]=oid
        object_ids=frozenset(''.join(Path(path).parts[-2:]) for path in marker['files']
            if path.startswith('objects.git/objects/') and len(Path(path).parts)==4
                and len(Path(path).parts[-2])==2 and len(Path(path).parts[-1])==38)
        return Baseline(directory,tree,known,object_ids)
    except (OSError,ValueError,KeyError,TypeError,CloudError) as error:
        if isinstance(error,CloudError) and error.code=='result_baseline_changed':raise
        raise CloudError('result_baseline_changed') from error


def prepare(parent,input_directory,package,result_limits,input_limits):
    if (package.manifest.input_id!=package.input_id
            or package.manifest.selection['policy_sha256']!=hashlib.sha256(package.selection.canonical).hexdigest()
            or [entry.path for entry in package.manifest.entries]!=package.selection.paths):
        raise CloudError('result_input_mismatch')
    caches=parent/'.baseline-cache';caches.mkdir(mode=0o700,exist_ok=True);check_store_directory(caches)
    directory=caches/package.input_id
    if os.path.lexists(directory):return load(directory,package,result_limits,input_limits)
    with tempfile.TemporaryDirectory(prefix='.build-',dir=caches) as temporary:
        stage=Path(temporary);source=stage/'input';source.mkdir(mode=0o700)
        manifest,selection=validate_package(input_directory/'archive',package.codec,input_limits,expected_id=package.input_id,
            destination=source,expected_archive_sha256=package.archive_sha256,expected_size=package.size)
        if manifest!=package.manifest or selection!=package.selection:raise CloudError('result_input_mismatch')
        git=ResultGit(stage/'cache',result_limits);blobs={};links=stage/'links';links.mkdir(mode=0o700)
        for number,entry in enumerate(manifest.entries):
            if entry.type=='file':blobs[entry.path]=source/entry.path
            else:
                # Native git must see the symlink target bytes, never follow the link.
                blob=links/str(number)
                with source_handle(source,entry.path,identity((source/entry.path).lstat())) as (content,info):
                    if info is not None:raise CloudError('result_input_mismatch')
                    blob.write_bytes(content)
                blobs[entry.path]=blob
        tree=git.tree(manifest.entries,blobs,'baseline');(git.directory/'baseline.index').unlink()
        marker={'schema':1,'package':package_identity(package),'tree':tree,
            'files':cache_files(git.directory,input_limits,durable=True)}
        write_json(git.directory/'baseline.json',marker);sync_store_directory(git.directory)
        try:rename_new(git.directory,directory)
        except OSError as error:
            if error.errno not in (errno.EEXIST,errno.ENOTEMPTY):raise
        sync_store_directory(caches)
    return load(directory,package,result_limits,input_limits)
