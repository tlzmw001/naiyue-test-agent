"""Thread-owned Docker lifecycle; no database transaction crosses provider IO."""
import logging
from threading import RLock
from uuid import uuid4
from naiyue_test_agent.contracts.sandbox import SandboxError,SandboxRecord
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.execution.docker_environment import DockerEnvironment,verify_container,verify_container_identity,verify_volume,state_volume
from naiyue_test_agent.sandbox_settings import SandboxSettings

logger=logging.getLogger(__name__)


class EnvironmentService:
    def __init__(self,projects,settings):
        self.projects,self.settings=projects,settings
        self._locks={}
        self._guard=RLock()
        self.operations=None

    def _lock(self,binding_id):
        with self._guard:
            lock=self._locks.get(binding_id)
            if lock is None:
                lock=RLock()
                self._locks[binding_id]=lock
            return lock

    def records(self):
        return self.projects.environment_records()

    def _engine(self,row):
        return DockerEnvironment(DockerCLI(SandboxSettings.model_validate(row.settings),self.projects.data_dir))

    def _project(self,project_id):
        facts=self.projects.registry.execution_facts(project_id)
        if facts.lifecycle!='active' or not facts.trusted:raise SandboxError('env_project_unavailable')

    def create(self,project_id,session_id,*,binding_id=None):
        if binding_id is not None:
            from naiyue_test_agent.contracts.git_inputs import input_id
            input_id(binding_id)
            with self._lock(binding_id):
                try:existing=self.get(binding_id)
                except SandboxError as error:
                    if error.code!='env_not_registered':raise
                else:
                    if existing.project_id!=project_id or existing.session_id!=session_id:
                        raise SandboxError('env_binding_mismatch')
                    return existing
                return self._create(project_id,session_id,binding_id)
        return self._create(project_id,session_id,uuid4().hex)

    def _create(self,project_id,session_id,binding_id):
        self._project(project_id)
        if not self.settings.image:raise SandboxError('env_image_required')
        cli=DockerCLI(self.settings,self.projects.data_dir)
        daemon=cli.daemon()
        image=cli.json(['image','inspect',self.settings.image])[0]
        if image.get('Os')!='linux':raise SandboxError('env_platform_unsupported')
        row=SandboxRecord(binding_id,project_id,session_id,'creating',daemon,
            'nyt-e-'+binding_id,'nyt-v-'+binding_id,image['Id'],self.settings.model_dump(mode='json'))
        records=self.records()
        with self._lock(binding_id):
            records.reserve(row)
            engine=DockerEnvironment(cli)
            try:
                row=engine.create(row,records.save)
                engine.handshake(row)
                row=row.changed(lifecycle='running')
                records.save(row)
            except Exception as error:
                logger.error('sandbox_creation_failed error_type=%s',type(error).__name__)
                saved=records.get(binding_id)
                records.save(saved.changed(lifecycle='unknown',error_code=getattr(error,'code','env_operation_unknown')))
                raise
        return self.get(binding_id)

    def get(self,binding_id):return self.records().get(binding_id)
    def list(self,project_id):return self.records().list(project_id)

    def validate(self,binding_id,*,require_input=True):
        row=self.get(binding_id)
        self._project(row.project_id)
        if row.lifecycle=='destroyed':raise SandboxError('env_expired')
        if row.lifecycle!='running':raise SandboxError('env_stopped' if row.lifecycle=='stopped' else 'env_operation_unknown')
        if require_input and row.input_id is not None and row.input_snapshot is None:
            raise SandboxError('git_input_install_unconfirmed')
        self._engine(row).validate(row)
        return row

    def request(self,binding_id,operation,params,*,mutates=False,control=None):
        if mutates and self.operations is not None:
            return self.operations.file_call(binding_id,operation,params,control)
        row=self.validate(binding_id)
        return self._engine(row).request(row,operation,params,mutates=mutates)

    def action(self,binding_id,action):
        with self._lock(binding_id):
            row=self.get(binding_id)
            if row.lifecycle=='destroyed':
                if action=='destroy':return row
                raise SandboxError('env_expired')
            engine=self._engine(row)
            engine.cli.daemon(row.daemon_id)
            try:
                if action=='recover':
                    data=engine.cli.container(row.container_id) if row.container_id else engine.cli.container(row.container_name,by_name=True)
                    if data is None:raise SandboxError('env_expired')
                    row=row.changed(container_id=data['Id'])
                    verify_container(row,data);verify_volume(row,engine.cli.volume(row.volume_name))
                    verify_volume(row,engine.cli.volume(state_volume(row)),state=True)
                    state='running' if data.get('State',{}).get('Running') else 'stopped'
                    if state=='running':engine.handshake(row)
                elif action=='destroy':
                    data=engine.cli.container(row.container_id) if row.container_id else engine.cli.container(row.container_name,by_name=True)
                    if data is not None:
                        checked=row.changed(container_id=data['Id'])
                        verify_container_identity(checked,data)
                        engine.cli.run(['container','rm','--force',data['Id']],mutates=True)
                    for name,is_state in ((row.volume_name,False),(state_volume(row),True)):
                        volume=engine.cli.volume(name)
                        if volume is not None:
                            verify_volume(row,volume,state=is_state)
                            engine.cli.run(['volume','rm',name],mutates=True)
                    state='destroyed'
                elif action in ('start','stop'):
                    self._project(row.project_id)
                    data=engine.validate(row,running=False)
                    if action=='start':
                        if not data['State']['Running']:engine.cli.run(['container','start',row.container_id],mutates=True)
                        engine.handshake(row);state='running'
                    else:
                        engine.cli.run(['container','stop','--time',str(row.settings['stop_timeout_seconds']),row.container_id],mutates=True)
                        checked=engine.validate(row,running=False)
                        if checked['State']['Running']:raise SandboxError('env_operation_unknown')
                        state='stopped'
                else:raise SandboxError('env_operation_unsupported')
                row=row.changed(lifecycle=state,error_code=None)
                self.records().save(row)
                return self.get(binding_id)
            except Exception as error:
                logger.error('sandbox_lifecycle_failed action=%s error_type=%s',action,type(error).__name__)
                self.records().save(row.changed(lifecycle='unknown',error_code=getattr(error,'code','env_operation_unknown')))
                raise
