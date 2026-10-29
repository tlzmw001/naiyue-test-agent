"""Receive bounded bundles into private state; checkout is a separate journaled action."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock

from naiyue_test_agent.contracts.git_inputs import GitInputError, input_id, object_id, input_payload
from naiyue_test_agent.execution.git_repository import GitRepository, bundle_digest, check_cache_budget
from naiyue_test_agent.execution.git_snapshot import validate_tree
from naiyue_test_agent.storage.files import check_store_directory, open_store_file, open_store_readonly, sync_store_directory
from .identity import TaskProcessRunner,check_private_directory,trusted_git_executable
from naiyue_test_agent.contracts.sandbox import TASK_UID,TASK_GID


class InputReceiver:
    def __init__(self, state_root, environment, settings):
        self.root = Path(state_root)/'git-inputs'
        self.code, self.settings = Path(environment.root).resolve(strict=True), settings
        executable=settings.git.container_executable
        if os.geteuid()==0:executable=trusted_git_executable(executable)
        self.policy = settings.git.model_copy(update={'executable':executable})
        self._lock = RLock()
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        check_store_directory(self.root)
        self.repository = GitRepository(self.root/'objects.git',self.policy)

    def _directory(self, ident):
        path = self.root/input_id(ident)
        check_store_directory(path)
        return path

    @staticmethod
    def _read_json(path):
        with os.fdopen(open_store_readonly(path),'rb') as stream:
            raw = stream.read(16385)
        if len(raw)>16384:
            raise GitInputError('git_input_corrupt')
        try:
            value = json.loads(raw)
        except (ValueError,UnicodeError):
            raise GitInputError('git_input_corrupt') from None
        if not isinstance(value,dict):
            raise GitInputError('git_input_corrupt')
        return value

    @staticmethod
    def _write_json(path, value):
        with os.fdopen(open_store_file(path,exclusive_create=True),'wb') as stream:
            stream.write(json.dumps(value,sort_keys=True).encode())
            stream.flush();os.fsync(stream.fileno())
        sync_store_directory(path.parent)

    def begin(self, metadata):
        fields={'transfer_id','input_id','original_head','input_snapshot','size','sha256','base'}
        if set(metadata)!=fields:
            raise GitInputError('git_input_invalid_transfer')
        ident = input_id(metadata['transfer_id']);input_id(metadata['input_id'])
        object_id(metadata['original_head']);object_id(metadata['input_snapshot'])
        if (type(metadata['size']) is not int or not 0<metadata['size']<=self.policy.max_bundle_bytes
                or not isinstance(metadata['sha256'],str) or re.fullmatch('[0-9a-f]{64}',metadata['sha256']) is None
                or metadata['base'] is not None and metadata['base']!=metadata['original_head']):
            raise GitInputError('git_input_invalid_transfer')
        with self._lock:
            directory=self.root/ident
            directory.mkdir(mode=0o700,exist_ok=True)
            check_store_directory(directory)
            saved=directory/'metadata.json'
            if saved.exists() or saved.is_symlink():
                if self._read_json(saved)!=metadata:
                    raise GitInputError('git_input_conflict')
            else:
                check_cache_budget(self.root,self.policy.max_cache_bytes-metadata['size'])
                self._write_json(saved,metadata)
                fd=open_store_file(directory/'input.bundle',exclusive_create=True);os.close(fd)
            return self.status(ident)

    def status(self, ident):
        with self._lock:
            directory=self._directory(ident)
            metadata=self._read_json(directory/'metadata.json')
            fd=open_store_readonly(directory/'input.bundle')
            try:size=os.fstat(fd).st_size
            finally:os.close(fd)
            if size>metadata['size']:
                raise GitInputError('git_input_corrupt')
            return {'transfer_id':ident,'offset':size}

    def chunk(self, ident, offset, encoded):
        if (type(offset) is not int or offset<0 or not isinstance(encoded,str)
                or len(encoded)>self.settings.max_message_bytes//2):
            raise GitInputError('git_input_invalid_transfer')
        try:data=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):raise GitInputError('git_input_invalid_transfer') from None
        if not data or len(data)>min(1048576,self.settings.max_message_bytes//4):
            raise GitInputError('git_input_invalid_transfer')
        with self._lock:
            directory=self._directory(ident)
            metadata=self._read_json(directory/'metadata.json')
            with os.fdopen(open_store_file(directory/'input.bundle'),'r+b') as stream:
                size=os.fstat(stream.fileno()).st_size
                if offset+len(data)>metadata['size']:
                    raise GitInputError('git_input_chunk_conflict')
                if offset<size:
                    stream.seek(offset)
                    if offset+len(data)>size or stream.read(len(data))!=data:
                        raise GitInputError('git_input_chunk_conflict')
                elif offset!=size:
                    raise GitInputError('git_input_chunk_conflict')
                else:
                    check_cache_budget(self.root,self.policy.max_cache_bytes-len(data))
                    stream.seek(offset);stream.write(data);stream.flush();os.fsync(stream.fileno())
            return self.status(ident)

    def probe(self, original_head):
        object_id(original_head)
        with self._lock:
            return {'original_head':original_head,'has_base':
                self.repository.path.exists() and self.repository.has(original_head)}

    def cache(self, ident):
        with self._lock:
            directory=self._directory(ident)
            metadata=self._read_json(directory/'metadata.json')
            bundle=directory/'input.bundle'
            size,digest=bundle_digest(bundle,self.policy.max_bundle_bytes)
            if size!=metadata['size'] or digest!=metadata['sha256']:
                raise GitInputError('git_input_corrupt')
            repo=self.repository
            if not repo.path.exists():
                repo.run(['init','--bare','--quiet',str(repo.path)],cwd=self.root)
            check_store_directory(repo.path)
            if metadata['base'] is not None and not repo.has(metadata['base']):
                raise GitInputError('git_input_base_missing')
            ref='refs/naiyue/input/'+metadata['input_id']
            heads=repo.run(['bundle','list-heads',str(bundle)]).decode().splitlines()
            if heads!=[metadata['input_snapshot']+' '+ref]:
                raise GitInputError('git_input_invalid_bundle')
            status,_=repo.run(['bundle','verify',str(bundle)],check=False)
            if status:
                raise GitInputError('git_input_invalid_bundle')
            marker=self.root/('cached-'+metadata['input_id']+'.json')
            payload={key:metadata[key] for key in ('input_id','original_head','input_snapshot')}
            if marker.exists() and self._read_json(marker)!=payload:
                raise GitInputError('git_input_conflict')
            repo.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',str(bundle),ref+':'+ref])
            check_cache_budget(self.root,self.policy.max_cache_bytes)
            repo.run(['fsck','--strict','--no-dangling',metadata['input_snapshot']])
            parents=repo.run(['rev-list','--parents','-n','1',metadata['input_snapshot']]).decode().split()
            expected=[metadata['input_snapshot'],metadata['original_head']]
            if metadata['input_snapshot']!=metadata['original_head'] and parents!=expected:
                raise GitInputError('git_input_invalid_bundle')
            validate_tree(repo,metadata['input_snapshot'])
            if not marker.exists():self._write_json(marker,payload)
            return payload

    def reserve_install(self, ident, payload):
        input_payload(payload)
        with self._lock:
            marker=self.root/('cached-'+payload['input_id']+'.json')
            if not marker.exists() or self._read_json(marker)!=payload:
                raise GitInputError('git_input_not_cached')
            check_store_directory(self.code)
            if any(self.code.iterdir()):
                raise GitInputError('git_input_workspace_not_empty')
            self._write_json(self.root/'installation.json',dict(operation_id=ident,payload=payload))

    def installation(self):
        path=self.root/'installation.json'
        with self._lock:
            if not path.exists() and not path.is_symlink():return None
            return self._read_json(path)

    def install(self, payload, control):
        input_payload(payload)
        with self._lock:
            repo=self.repository
            if not repo.has(payload['original_head']) or not repo.has(payload['input_snapshot']):
                raise GitInputError('git_input_base_missing')
            validate_tree(repo,payload['input_snapshot'])
            if any(self.code.iterdir()):
                raise GitInputError('git_input_workspace_not_empty')
            stage=self.code.parent/'.envd-stage'
            stage.mkdir(mode=0o700,exist_ok=True);check_private_directory(stage)
            with tempfile.TemporaryDirectory(dir=stage) as temporary:
                directory=Path(temporary)
                target=GitRepository(directory,self.policy)
                target.run(['init','--quiet'],control=control)
                branch='naiyue-task/'+payload['input_id']
                target.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',str(repo.path),
                    'refs/naiyue/input/'+payload['input_id']+':refs/heads/'+branch],control=control)
                target.run(['checkout','--quiet',branch],control=control)
                if target.oid('HEAD^{commit}')!=payload['input_snapshot']:
                    raise GitInputError('git_input_install_unconfirmed')
                if os.geteuid()==0:
                    for parent,dirs,files in os.walk(directory,followlinks=False):
                        for name in [*dirs,*files]:
                            os.chown(Path(parent)/name,TASK_UID,TASK_GID,follow_symlinks=False)
                source_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    destination_fd=os.open(self.code,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    try:
                        if os.listdir(destination_fd):raise GitInputError('git_input_workspace_not_empty')
                        for name in os.listdir(source_fd):
                            if control.requested:raise GitInputError('git_input_cancelled')
                            os.rename(name,name,src_dir_fd=source_fd,dst_dir_fd=destination_fd)
                        os.fsync(destination_fd)
                    finally:os.close(destination_fd)
                finally:os.close(source_fd)
            target=GitRepository(self.code,self.policy,runner=TaskProcessRunner())
            if target.oid('HEAD^{commit}')!=payload['input_snapshot']:raise GitInputError('git_input_install_unconfirmed')
            return payload
