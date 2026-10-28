"""Registered source preparation; no model, project host or user Git writes."""
from dataclasses import replace
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import stat
from threading import RLock
import time

from naiyue_test_agent.contracts.git_inputs import GitInputError, input_id
from naiyue_test_agent.diagnostics import safe_log
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.execution.git_repository import GitRepository, make_bundle, bundle_digest
from naiyue_test_agent.contracts.sandbox import SandboxError, operation_fingerprint
from naiyue_test_agent.sandbox_settings import GitInputSettings
from naiyue_test_agent.storage.git_inputs import GitInputs
from naiyue_test_agent.storage.files import open_store_readonly, check_store_directory

logger = logging.getLogger(__name__)


class InputService:
    def __init__(self, environments, operations):
        self.environments,self.operations = environments,operations
        self._guard,self._locks = RLock(),{}

    def _lock(self, ident):
        with self._guard:
            return self._locks.setdefault(ident,RLock())

    def records(self):
        return GitInputs(self.environments.projects.registry.database)

    def directory(self, project_id, ident):
        input_id(ident)
        return self.environments.projects.data_dir/'workspaces'/project_id/'git-inputs'/ident

    def _remove_directory(self, project_id, ident):
        directory = self.directory(project_id, ident)
        failed_path, operation = directory, 'inspect_directory'
        callback_error = None

        def failed(function, path, error):
            nonlocal failed_path, operation, callback_error
            # rmtree may report the same exception again while unwinding.
            if error is not callback_error:
                failed_path, operation, callback_error = Path(path), function.__name__, error
            raise error

        try:
            if not directory.exists() and not directory.is_symlink():
                return
            operation = 'validate_parent'
            check_store_directory(directory.parent)
            operation = 'validate_directory'
            check_store_directory(directory)
            operation = 'rmtree'
            shutil.rmtree(directory, onexc=failed)
        except OSError as error:
            try:
                relative = failed_path.absolute().relative_to(directory.absolute())
                inside = '..' not in relative.parts
            except ValueError:
                inside = False
            path_label = relative.as_posix() if inside else '<outside_input>'
            readonly = reparse = links = inspect_errno = inspect_winerror = None
            kind = 'unknown'
            if inside:
                try:
                    info = failed_path.lstat()
                    attributes = getattr(info, 'st_file_attributes', None)
                    readonly = bool(attributes & stat.FILE_ATTRIBUTE_READONLY) if attributes is not None else None
                    reparse = bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) if attributes is not None else None
                    links = info.st_nlink
                    kind = ('regular' if stat.S_ISREG(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode)
                        else 'symlink' if stat.S_ISLNK(info.st_mode) else 'other')
                except OSError as inspection_error:
                    inspect_errno, inspect_winerror = inspection_error.errno, getattr(inspection_error, 'winerror', None)
            safe_log(logger.error, 'git_input_directory_cleanup_failed project_id=%s input_id=%s operation=%s '
                'error_type=%s errno=%s winerror=%s relative_path=%s readonly=%s '
                'file_kind=%s reparse_point=%s links=%s inspect_errno=%s inspect_winerror=%s',
                project_id, ident, operation, type(error).__name__, error.errno, getattr(error, 'winerror', None),
                json.dumps(path_label[:256], ensure_ascii=True), readonly, kind, reparse, links,
                inspect_errno, inspect_winerror)
            raise

    def retire(self, project_id, ident, sessions):
        input_id(ident)
        with self._lock(ident):
            records = self.records()
            try:
                row = records.get(project_id, ident)
            except GitInputError as error:
                if error.code == 'git_input_not_found':
                    return False
                raise
            if row.state not in ('preparing','ready','failed'):
                return False
            if sessions is not None and sessions.references_input(project_id,ident):return False
            if not records.can_retire(project_id,ident):return False
            try:self._remove_directory(project_id, ident)
            except OSError as error:
                logger.error('git_input_retirement_failed error_type=%s',type(error).__name__)
                raise
            return records.delete_unreferenced(project_id, ident)

    def get(self, project_id, ident):
        return self.records().get(project_id,ident)

    def options(self,project_id):
        from naiyue_test_agent.contracts.git_inputs import git_path,sensitive_path
        configuration=self.environments.configuration
        settings=configuration.execution_settings()
        project=self.environments.projects.registry.get(project_id)
        local=isinstance(project.source,LocalSource)
        paths=[];error=None
        if local and project.trusted:
            try:
                repo=GitRepository(project.source.path,settings.git)
                if not (project.source.path/'.git').is_dir():raise GitInputError('git_input_no_repository')
                if repo.run(['rev-parse','--verify','HEAD^{commit}'],check=False)[0]:raise GitInputError('git_input_no_head')
                paths=sorted(git_path(raw) for raw in repo.run(['ls-files','--others','--exclude-standard','-z'],source_config=True).split(b'\0') if raw)
                if len(paths)>settings.git.max_files:raise GitInputError('git_input_budget')
            except GitInputError as failure:error=failure.code
        if not settings.image:error='env_image_required'
        return dict(host_available=local,sandbox_available=error is None,error_code=error,
            requires_trust=not project.trusted,project_revision=project.revision,
            **configuration.catalog(),network=settings.network.mode,untracked=paths,
            blocked=[path for path in paths if sensitive_path(path)])

    def _project(self, project_id):
        project = self.environments.projects.registry.get(project_id)
        if project.lifecycle!='active' or isinstance(project.source,LocalSource) and not project.trusted:
            raise GitInputError('git_input_project_unavailable')
        return project

    def prepare(self, project_id, *, input_id: str, include_untracked=(), git_settings=None):
        from naiyue_test_agent.contracts.git_inputs import input_id as require_id
        require_id(input_id)
        with self.operations.accepting(),self._lock(input_id):
            project = self._project(project_id)
            records = self.records()
            row,new = records.reserve(project_id,project.revision,input_id,include_untracked,
                git_settings if git_settings is not None else self.environments.settings.git)
            if not new:
                if row.state=='ready':
                    return row
                raise GitInputError(row.error_code or 'git_input_unconfirmed',row.error_paths)
            try:
                builder = SnapshotBuilder(self.directory(project_id,input_id),GitInputSettings.model_validate(row.settings))
                snapshot = builder.prepare(project.source,input_id,include_untracked=row.include_untracked)
                if self._project(project_id).revision!=row.project_revision:
                    raise GitInputError('git_input_source_changed')
                return records.save(replace(row,state='ready',snapshot=snapshot))
            except GitInputError as error:
                logger.warning('git_input_preparation_rejected code=%s',error.code)
                records.save(replace(row,state='failed',error_code=error.code,error_paths=error.paths))
                try:self._remove_directory(project_id,input_id)
                except OSError as cleanup_error:
                    logger.error('git_input_partial_cleanup_failed error_type=%s',type(cleanup_error).__name__)
                raise
            except OSError as error:
                logger.error('git_input_preparation_io_failed error_type=%s',type(error).__name__)
                records.save(replace(row,state='failed',error_code='git_input_io_failed'))
                try:self._remove_directory(project_id,input_id)
                except OSError as cleanup_error:
                    logger.error('git_input_partial_cleanup_failed error_type=%s',type(cleanup_error).__name__)
                raise GitInputError('git_input_io_failed') from error

    @staticmethod
    def _payload(snapshot):
        return dict(input_id=snapshot.input_id,original_head=snapshot.original_head,
            input_snapshot=snapshot.input_snapshot)

    def _transfer(self, row, record, *, base):
        snapshot=record.snapshot
        directory=self.directory(record.project_id,record.input_id)
        check_store_directory(directory)
        settings=GitInputSettings.model_validate(record.settings)
        path=directory/'input.bundle'
        if base is not None:
            path=directory/('delta-'+base+'.bundle')
            with self._lock(record.input_id):
                if not path.exists():
                    make_bundle(GitRepository(directory/'objects.git',settings),path,record.input_id,base=base)
        size,digest=bundle_digest(path,settings.max_bundle_bytes)
        if base is None and (size!=snapshot.bundle_size or digest!=snapshot.bundle_sha256):
            raise GitInputError('git_input_corrupt')
        transfer=hashlib.sha256((record.input_id+digest).encode()).hexdigest()[:32]
        engine=self.environments._engine(row)
        metadata=dict(transfer_id=transfer,**self._payload(snapshot),size=size,sha256=digest,base=base)
        progress=engine.request(row,'input_begin',metadata,mutates=True)

        def offset(value):
            if (not isinstance(value,dict) or set(value)!={'transfer_id','offset'}
                    or value['transfer_id']!=transfer or type(value['offset']) is not int
                    or not 0<=value['offset']<=size):
                raise GitInputError('git_input_invalid_transfer')
            return value['offset']

        current=offset(progress)
        with os.fdopen(open_store_readonly(path),'rb') as stream:
            while current<size:
                stream.seek(current)
                data=stream.read(min(1048576,row.settings['max_message_bytes']//4,size-current))
                if not data:raise GitInputError('git_input_corrupt')
                try:
                    result=engine.request(row,'input_chunk',dict(transfer_id=transfer,offset=current,
                        data=base64.b64encode(data).decode()),mutates=True)
                except SandboxError as error:
                    if error.code not in ('env_operation_unknown','env_unreachable'):raise
                    logger.warning('git_input_chunk_ack_unconfirmed')
                    result=engine.request(row,'input_status',dict(transfer_id=transfer))
                advanced=offset(result)
                if advanced<current+len(data):raise GitInputError('git_input_install_unconfirmed')
                current=advanced
        cached=engine.request(row,'input_cache',dict(transfer_id=transfer),mutates=True)
        if cached!=self._payload(snapshot):raise GitInputError('git_input_invalid_transfer')

    def install(self, binding, ident):
        input_id(ident)
        # The complete transfer/installation/save lifetime participates in the old close gate.
        with self.operations.accepting(),self.environments._lock(binding):
            try:
                row=self.environments.validate(binding,require_input=False)
                with self._lock(ident):
                    record=self.get(row.project_id,ident)
                    project=self._project(row.project_id)
                    if project.revision!=record.project_revision:
                        raise GitInputError('git_input_source_changed')
                    if record.state!='ready' or record.snapshot is None:
                        raise GitInputError(record.error_code or 'git_input_unconfirmed',record.error_paths)
                    if row.input_id is not None and row.input_id!=ident:
                        raise GitInputError('git_input_binding_conflict')
                    if row.input_snapshot is not None:return row
                    if row.input_operation_id is None:
                        row=row.changed(input_id=ident)
                        self.environments.records().save(row)
                if row.input_operation_id is None:
                    engine=self.environments._engine(row)
                    probe=engine.request(row,'input_probe',{'original_head':record.snapshot.original_head})
                    if (not isinstance(probe,dict) or set(probe)!={'original_head','has_base'}
                            or probe['original_head']!=record.snapshot.original_head or type(probe['has_base']) is not bool):
                        raise GitInputError('git_input_invalid_transfer')
                    base=record.snapshot.original_head if probe['has_base'] and (
                        record.snapshot.input_snapshot!=record.snapshot.original_head) else None
                    try:self._transfer(row,record,base=base)
                    except GitInputError as error:
                        if base is None or error.code!='git_input_base_missing':raise
                        logger.warning('git_input_base_missing action=full_bundle')
                        self._transfer(row,record,base=None)
                    if self._project(row.project_id).revision!=record.project_revision:
                        raise GitInputError('git_input_source_changed')
                    operation=hashlib.sha256((binding+':git_input:'+ident).encode()).hexdigest()[:32]
                    row=row.changed(input_operation_id=operation)
                    self.environments.records().save(row)
                    current=self.operations.submit(binding,operation,'git_input',self._payload(record.snapshot),{})
                else:
                    try:current=self.operations.refresh(binding,row.input_operation_id)
                    except SandboxError as error:
                        if error.code!='env_operation_not_found':raise
                        raise GitInputError('git_input_install_unconfirmed') from error
                deadline=time.monotonic()+record.settings['timeout_seconds']
                while current['state'] not in ('completed','failed','stopped'):
                    if current.get('stale') or current['state']=='unknown' or time.monotonic()>=deadline:
                        raise GitInputError('git_input_install_unconfirmed')
                    time.sleep(row.settings['poll_milliseconds']/1000)
                    current=self.operations.refresh(binding,row.input_operation_id)
                if current['state']!='completed':
                    result=current.get('result') or {}
                    raise GitInputError(result.get('error',{}).get('code','git_input_install_unconfirmed'))
                return self.confirm(binding,current)
            except OSError as error:
                logger.error('git_input_installation_io_failed error_type=%s',type(error).__name__)
                raise GitInputError('git_input_io_failed') from error

    def confirm(self, binding, operation):
        with self.environments._lock(binding):
            row=self.environments.get(binding)
            if (operation['kind']!='git_input' or operation['state']!='completed'
                    or row.input_operation_id!=operation['operation_id'] or row.input_id is None):
                raise GitInputError('git_input_install_unconfirmed')
            record=self.get(row.project_id,row.input_id)
            expected=self._payload(record.snapshot)
            if (operation['fingerprint']!=operation_fingerprint('git_input',expected)
                    or operation.get('result')!={'ok':True,'value':expected}):
                raise GitInputError('git_input_install_unconfirmed')
            confirmed=row.changed(input_snapshot=record.snapshot.input_snapshot)
            self.environments.records().save(confirmed)
            return self.environments.get(binding)
