"""Freeze a sandbox result without changing its HEAD, index or working files."""
import base64
from dataclasses import asdict
from pathlib import Path

from naiyue_test_agent.contracts.git_inputs import GitInputError,input_id,git_path,sensitive_path
from naiyue_test_agent.contracts.git_results import result_request,result_ref
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.execution.git_repository import GitRepository,bundle_digest,check_cache_budget
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.storage.files import open_store_readonly


class ResultExporter:
    def __init__(self,state_root,environment,session_id,settings,inputs):
        self.root=Path(state_root)/'git-results'
        self.environment,self.session_id,self.settings,self.inputs=environment,session_id,settings,inputs
        self.policy=settings.git.model_copy(update={'executable':settings.git.container_executable})

    def bundle_path(self,ident):return self.root/input_id(ident)/'result.bundle'

    def options(self):
        repository=GitRepository(Path(self.environment.root),self.policy)
        paths=sorted(git_path(p) for p in repository.run(['ls-files','--others','--exclude-standard','-z']).split(b'\0') if p)
        if len(paths)>self.policy.max_files:raise GitInputError('git_result_budget')
        return dict(untracked=paths,blocked=[p for p in paths if sensitive_path(p)])

    def capture(self,payload,control):
        result_request(payload)
        installed=self.inputs.installation()
        if installed is None or any(installed['payload'].get(key)!=payload[key] for key in ('input_id','original_head','input_snapshot')):
            raise GitInputError('git_result_binding_mismatch')
        if len(payload['include_untracked'])>self.policy.max_files:raise GitInputError('git_result_budget')
        check_cache_budget(self.root,self.policy.max_cache_bytes)
        directory=self.root/payload['result_id']
        snapshot=SnapshotBuilder(directory,self.policy).prepare(LocalSource(Path(self.environment.root)),
            payload['result_id'],include_untracked=payload['include_untracked'])
        repository=GitRepository(directory/'objects.git',self.policy)
        if (not repository.has(payload['input_snapshot']) or repository.run(
                ['merge-base','--is-ancestor',payload['input_snapshot'],snapshot.input_snapshot],check=False)[0]):
            raise GitInputError('git_result_not_descendant')
        if control.requested:raise GitInputError('git_result_cancelled')
        ref=result_ref(payload['result_id'])
        repository.run(['update-ref',ref,snapshot.input_snapshot])
        path=self.bundle_path(payload['result_id'])
        repository.run(['bundle','create',str(path),ref])
        size,digest=bundle_digest(path,self.policy.max_bundle_bytes)
        check_cache_budget(self.root,self.policy.max_cache_bytes)
        return dict(result_id=payload['result_id'],binding_id=self.environment.binding_id,
            project_id=self.environment.project_id,session_id=self.session_id,input_id=payload['input_id'],
            original_head=payload['original_head'],input_snapshot=payload['input_snapshot'],
            output_commit=snapshot.input_snapshot,bundle_size=size,bundle_sha256=digest,
            excluded=[asdict(item) for item in snapshot.excluded])

    def chunk(self,ident,offset,limit):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=1048576:
            raise GitInputError('git_result_invalid_transfer')
        path=self.bundle_path(ident)
        import os
        with os.fdopen(open_store_readonly(path),'rb') as stream:
            size=os.fstat(stream.fileno()).st_size
            if size>self.policy.max_bundle_bytes or offset>size:raise GitInputError('git_result_invalid_transfer')
            stream.seek(offset);data=stream.read(min(limit,self.settings.max_message_bytes//4))
        return dict(offset=offset,next_offset=offset+len(data),bytes_base64=base64.b64encode(data).decode())
