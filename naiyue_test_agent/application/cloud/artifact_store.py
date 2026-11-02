"""Service-owned raw artifact publication; only fixed managed paths are opened."""
import hashlib
import os
from pydantic import TypeAdapter

from naiyue_test_agent.cloud_runner.state import write_json,read_json
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError,TaskId,UUIDText
from naiyue_test_agent.storage.files import open_store_readonly,sync_store_directory
from .ownership import private_directory


def file_identity(info):return [getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]


class ArtifactStore:
    def __init__(self,root,limits):self.root,self.limits=root,limits

    def directory(self,kind,task_id,artifact_id,*,create=False):
        TypeAdapter(TaskId).validate_python(task_id);TypeAdapter(UUIDText).validate_python(artifact_id)
        base=self.root/kind;parent=base/task_id
        for path in (base,parent):
            if create:path.mkdir(mode=0o700,exist_ok=True)
            private_directory(path)
        return parent/artifact_id

    def staging(self,artifact):
        path=self.directory('artifact-transfers',artifact.task_id,artifact.artifact_id,create=True)
        path.mkdir(mode=0o700,exist_ok=True);private_directory(path)
        return path

    def _hash(self,path,artifact):
        sha=hashlib.sha256();size=0
        with os.fdopen(open_store_readonly(path),'rb') as stream:
            before=file_identity(os.fstat(stream.fileno()))
            if before[2]!=artifact.size or before[2]>self.limits.max_file_bytes:raise CloudError('artifact_content_mismatch')
            while data:=stream.read(self.limits.io_bytes):
                size+=len(data)
                if size>artifact.size:raise CloudError('artifact_content_mismatch')
                sha.update(data)
            if (size,sha.hexdigest())!=(artifact.size,artifact.sha256) or file_identity(os.fstat(stream.fileno()))!=before:
                raise CloudError('artifact_content_mismatch')
        return before

    def publish(self,artifact):
        target=self.directory('artifacts',artifact.task_id,artifact.artifact_id,create=True)
        if target.exists() or target.is_symlink():
            private_directory(target)
            if ArtifactRecord.model_validate(read_json(target/'artifact.json'))!=artifact:raise CloudError('artifact_identity_conflict')
            return self._hash(target/'bytes',artifact)
        stage=self.staging(artifact);identity=self._hash(stage/'bytes',artifact)
        write_json(stage/'artifact.json',artifact.model_dump());sync_store_directory(stage)
        os.rename(stage,target);sync_store_directory(target.parent)
        return identity

    def open(self,transfer):
        artifact=transfer.artifact
        path=self.directory('artifacts',artifact.task_id,artifact.artifact_id);private_directory(path)
        if ArtifactRecord.model_validate(read_json(path/'artifact.json'))!=artifact:raise CloudError('artifact_identity_conflict')
        stream=os.fdopen(open_store_readonly(path/'bytes'),'rb')
        try:
            if file_identity(os.fstat(stream.fileno()))!=transfer.blob_identity:raise CloudError('artifact_content_mismatch')
            return stream
        except BaseException:stream.close();raise
