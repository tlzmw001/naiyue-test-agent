"""Opaque private paths and verified content-addressed local blobs."""
import hashlib,os,stat
from uuid import uuid4
from contextlib import contextmanager,ExitStack
from threading import Lock
from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText,ContentId
from naiyue_test_agent.storage.files import check_store_directory,open_store_file,open_store_readonly,sync_store_directory
from naiyue_test_agent.application.cloud.ownership import private_directory


def identity(info):return [info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


class ReceiveFiles:
    def __init__(self,storage):
        self.root=storage.path.parent/'cloud-results' if storage.path else None
        self.publication=Lock()
        self.users=0;self.cleaning=False

    @contextmanager
    def use(self,*,cleanup=False):
        # Only the counters are locked; no mutex is held across IO or await.
        with self.publication:
            if self.cleaning or cleanup and self.users:raise CloudError('cloud_receive_busy',409)
            if cleanup:self.cleaning=True
            else:self.users+=1
        try:yield
        finally:
            with self.publication:
                if cleanup:self.cleaning=False
                else:self.users-=1

    def directory(self,*parts):
        if self.root is None:raise CloudError('cloud_material_unavailable')
        parent=self.root.parent;check_store_directory(parent)
        target=self.root
        target.mkdir(mode=0o700,exist_ok=True);private_directory(target)
        for part in parts:
            target=target/part;target.mkdir(mode=0o700,exist_ok=True);private_directory(target)
        return target

    def blob(self,sha):
        TypeAdapter(ContentId).validate_python(sha)
        return self.directory('blobs')/sha

    def stage(self,rid,fid):
        for value in (rid,fid):TypeAdapter(UUIDText).validate_python(value)
        return self.directory('staging',rid)/fid

    @contextmanager
    def verified(self,path,size,sha,expected=None):
        check_store_directory(path.parent)
        with self.use(),os.fdopen(open_store_readonly(path),'rb') as stream:
            before=identity(os.fstat(stream.fileno()));actual=hashlib.sha256();count=0
            while chunk:=stream.read(256*1024):
                count+=len(chunk)
                if count>size:raise CloudError('cloud_material_unavailable')
                actual.update(chunk)
            if ((count,actual.hexdigest())!=(size,sha) or before!=identity(os.fstat(stream.fileno()))
                    or expected is not None and before!=expected):raise CloudError('cloud_material_unavailable')
            stream.seek(0)
            yield stream
            if identity(os.fstat(stream.fileno()))!=before:raise CloudError('cloud_material_unavailable')

    def inspect(self,path,size,sha,expected=None):
        with self.verified(path,size,sha,expected) as stream:return identity(os.fstat(stream.fileno()))

    def publish(self,stage,artifact):
        source_identity=self.inspect(stage,artifact.size,artifact.sha256)
        target=self.blob(artifact.sha256)
        with self.publication:
            with ExitStack() as stack:
                stream=stack.enter_context(os.fdopen(open_store_readonly(stage),'rb'))
                if identity(os.fstat(stream.fileno()))!=source_identity:raise CloudError('cloud_receive_source_changed')
                kwargs={'follow_symlinks':False}
                if os.link in os.supports_dir_fd:
                    oldfd=os.open(stage.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,oldfd)
                    newfd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,newfd)
                    kwargs|={'src_dir_fd':oldfd,'dst_dir_fd':newfd};source,destination=stage.name,target.name
                else:source,destination=stage,target
                try:
                    os.link(source,destination,**kwargs)
                    current=target.lstat()
                    if (current.st_dev,current.st_ino)!=(source_identity[0],source_identity[1]):raise CloudError('cloud_receive_source_changed')
                except FileExistsError:pass  # Existing immutable content is verified below, never replaced.
            if (stage.lstat().st_dev,stage.lstat().st_ino)!=(source_identity[0],source_identity[1]):raise CloudError('cloud_receive_source_changed')
            stage.unlink()
        sync_store_directory(target.parent);sync_store_directory(stage.parent)
        return self.inspect(target,artifact.size,artifact.sha256)

    def read(self,artifact,limit):
        path=self.blob(artifact.sha256);self.inspect(path,artifact.size,artifact.sha256)
        with os.fdopen(open_store_readonly(path),'rb') as stream:
            data=stream.read(limit+1)
        if len(data)>limit:raise CloudError('cloud_material_budget',413)
        return data

    def used_bytes(self,limits):
        root=self.directory();pending=[root];total=0;entries=0
        while pending:
            parent=pending.pop();check_store_directory(parent)
            for path in parent.iterdir():
                entries+=1
                if entries>limits.max_results*(limits.max_files*3+4):raise CloudError('cloud_receive_capacity',429)
                info=path.lstat()
                if stat.S_ISDIR(info.st_mode):pending.append(path)
                elif stat.S_ISREG(info.st_mode):total+=info.st_size
                else:raise CloudError('cloud_receive_cache_unavailable',503)
                if total>limits.max_cache_bytes:raise CloudError('cloud_receive_capacity',429)
        return total

    def prepare_refresh(self,record):
        for item in record.files:
            artifact=item.artifact;path=self.blob(artifact.sha256)
            if not os.path.lexists(path):continue
            before=path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or getattr(before,'st_file_attributes',0)&0x400:
                raise CloudError('cloud_receive_source_changed')
            try:self.inspect(path,artifact.size,artifact.sha256)
            except CloudError:
                quarantine=self.directory('staging',record.receive_id)/('quarantine-'+uuid4().hex)
                with self.publication:
                    if identity(path.lstat())!=identity(before):raise CloudError('cloud_receive_source_changed')
                    os.rename(path,quarantine)
                    sync_store_directory(path.parent);sync_store_directory(quarantine.parent)
