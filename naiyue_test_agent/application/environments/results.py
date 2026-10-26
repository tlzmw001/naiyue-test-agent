"""Capture once, resume immutable downloads, validate, then publish a fixed ref."""
import base64
import logging
import os
import time

from naiyue_test_agent.contracts.git_inputs import GitInputError,input_id,input_path
from naiyue_test_agent.contracts.git_results import result_receipt
from naiyue_test_agent.contracts.sandbox import SandboxError,operation_fingerprint
from naiyue_test_agent.execution.git_results import ResultImporter
from naiyue_test_agent.execution.git_repository import check_cache_budget,bundle_digest
from naiyue_test_agent.sandbox_settings import GitInputSettings
from naiyue_test_agent.storage.files import open_store_file,check_store_directory,sync_store_directory

logger=logging.getLogger(__name__)


class ResultService:
    def __init__(self,environments,operations,inputs):
        self.environments,self.operations,self.inputs=environments,operations,inputs

    def directory(self,project,ident):
        return self.environments.projects.data_dir/'workspaces'/project/'git-results'/input_id(ident)

    def _row(self,binding,ident):
        row=self.operations.records().get(binding,ident)
        if row['kind']!='git_result':raise GitInputError('git_result_not_found')
        return row

    @staticmethod
    def _view(row):
        delivery=row['metadata'].get('delivery',{})
        return dict(result_id=row['operation_id'],state=delivery.get('state','capturing'),
            discarded=delivery.get('discarded',False),
            include_untracked=row['metadata'].get('selection',[]),
            operation_state=row['state'],stale=row.get('stale',False),
            error_code=delivery.get('error_code') or row.get('error_code') or
                (row.get('result') or {}).get('error',{}).get('code'),
            created_at=row['created_at'],receipt=delivery.get('receipt'),
            files=delivery.get('files',[]),warnings=delivery.get('warnings',[]))

    def get(self,binding,ident):return self._view(self._row(binding,ident))

    def list(self,binding):
        return [self._view(row) for row in self.operations.records().result_rows(binding)]

    def options(self,binding):
        row=self.environments.validate(binding)
        value=self.environments._engine(row).request(row,'result_options',{})
        if (not isinstance(value,dict) or set(value)!={'untracked','blocked'}
                or any(not isinstance(value[k],list) or len(value[k])>row.settings['git']['max_files']
                    for k in ('untracked','blocked'))):raise GitInputError('git_result_invalid_receipt')
        for key in ('untracked','blocked'):
            for path in value[key]:input_path(path)
        if not set(value['blocked'])<=set(value['untracked']):raise GitInputError('git_result_invalid_receipt')
        return value

    def _save(self,row,**changes):
        metadata=dict(row['metadata']);metadata['delivery']={**metadata['delivery'],**changes}
        return self.operations.records().save(row['binding_id'],row['operation_id'],dict(metadata=metadata))

    def _download(self,environment,receipt,directory):
        directory.mkdir(mode=0o700,parents=True,exist_ok=True);check_store_directory(directory)
        policy=GitInputSettings.model_validate(environment.settings['git'])
        if receipt['bundle_size']>policy.max_bundle_bytes:raise GitInputError('git_result_budget')
        path=directory/'result.bundle'
        check_cache_budget(directory.parent,policy.max_cache_bytes)
        with os.fdopen(open_store_file(path),'r+b') as stream:
            offset=os.fstat(stream.fileno()).st_size
            if offset>receipt['bundle_size']:raise GitInputError('git_result_corrupt')
            stream.seek(offset)
            while offset<receipt['bundle_size']:
                limit=min(1048576,environment.settings['max_message_bytes']//4,receipt['bundle_size']-offset)
                frame=self.environments._engine(environment).request(environment,'result_chunk',
                    dict(result_id=receipt['result_id'],offset=offset,limit=limit))
                if (not isinstance(frame,dict) or set(frame)!={'offset','next_offset','bytes_base64'}
                        or type(frame['offset']) is not int or frame['offset']!=offset
                        or type(frame['next_offset']) is not int or not isinstance(frame['bytes_base64'],str)
                        or len(frame['bytes_base64'])>(limit+2)//3*4):
                    raise GitInputError('git_result_invalid_transfer')
                try:data=base64.b64decode(frame['bytes_base64'],validate=True)
                except ValueError as error:raise GitInputError('git_result_invalid_transfer') from error
                if not 0<len(data)<=limit or frame['next_offset']!=offset+len(data):
                    raise GitInputError('git_result_invalid_transfer')
                stream.write(data);stream.flush();os.fsync(stream.fileno());offset+=len(data)
        sync_store_directory(directory)
        return path

    def capture(self,binding,ident,include):
        input_id(ident)
        if not isinstance(include,(tuple,list)) or any(not isinstance(p,str) for p in include):
            raise GitInputError('git_result_invalid_selection')
        selected=sorted(input_path(p) for p in include)
        if len(set(selected))!=len(selected):raise GitInputError('git_result_invalid_selection')
        with self.operations.accepting(),self.environments._lock(binding),self.operations._lock(ident):
            environment=self.environments.get(binding)
            if environment.input_id is None or environment.input_snapshot is None:
                raise GitInputError('git_result_binding_mismatch')
            record=self.inputs.get(environment.project_id,environment.input_id)
            payload=dict(result_id=ident,input_id=record.input_id,original_head=record.snapshot.original_head,
                input_snapshot=record.snapshot.input_snapshot,include_untracked=selected)
            try:row=self._row(binding,ident)
            except SandboxError as error:
                if error.code!='env_operation_not_found':raise
                project=self.inputs._project(environment.project_id)
                metadata=dict(delivery=dict(state='capturing',project_revision=project.revision),selection=selected)
                row=self.operations.submit(binding,ident,'git_result',payload,metadata)
            if row['fingerprint']!=operation_fingerprint('git_result',payload):raise SandboxError('env_operation_conflict')
            if row['metadata'].get('delivery',{}).get('state')=='ready':return self._view(row)
            try:
                if row['state'] not in ('completed','failed','stopped'):
                    row=self.operations.refresh(binding,ident)
                deadline=time.monotonic()+record.settings['timeout_seconds']
                while row['state'] not in ('completed','failed','stopped'):
                    if row.get('stale') or row['state']=='unknown' or time.monotonic()>=deadline:
                        raise GitInputError('git_result_unconfirmed')
                    time.sleep(environment.settings['poll_milliseconds']/1000)
                    row=self.operations.refresh(binding,ident)
                if row['state']!='completed':
                    raise GitInputError((row.get('result') or {}).get('error',{}).get('code','git_result_unconfirmed'))
                receipt=result_receipt(self.operations.value(row))
                if receipt['result_id']!=ident:raise GitInputError('git_result_binding_mismatch')
                directory=self.directory(environment.project_id,ident)
                path=self._download(environment,receipt,directory)
                row=self._save(row,state='verifying',receipt=receipt,error_code=None)
                importer=ResultImporter(directory,GitInputSettings.model_validate(record.settings))
                expected=dict(binding_id=binding,project_id=environment.project_id,session_id=environment.session_id,
                    input_id=record.input_id,original_head=record.snapshot.original_head,input_snapshot=record.snapshot.input_snapshot)
                verified=importer.verify(path,receipt,expected)
                check_cache_budget(directory.parent,record.settings['max_cache_bytes'])
                project=self.inputs._project(environment.project_id)
                if project.revision!=row['metadata']['delivery']['project_revision']:
                    raise GitInputError('git_result_project_changed')
                row=self._save(row,state='publishing')
                location=importer.publish(receipt,project.source)
                row=self._save(row,state='ready',import_location=location,**verified)
                return self._view(row)
            except (GitInputError,SandboxError) as error:
                logger.warning('git_result_delivery_unconfirmed code=%s',error.code)
                self._save(row,error_code=error.code)
                raise
            except OSError as error:
                logger.error('git_result_delivery_io_failed error_type=%s',type(error).__name__)
                raise GitInputError('git_result_io_failed') from error

    def diff(self,binding,ident,path):
        row=self._row(binding,ident);delivery=row['metadata']['delivery']
        if delivery['state']!='ready':raise GitInputError('git_result_unconfirmed')
        record=self.inputs.get(row['project_id'],delivery['receipt']['input_id'])
        directory=self.directory(row['project_id'],ident);check_store_directory(directory)
        receipt=delivery['receipt']
        if bundle_digest(directory/'result.bundle',record.settings['max_bundle_bytes'])!=(receipt['bundle_size'],receipt['bundle_sha256']):
            raise GitInputError('git_result_corrupt')
        return ResultImporter(directory,GitInputSettings.model_validate(record.settings)).diff(receipt,path)
