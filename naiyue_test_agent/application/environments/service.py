"""Thread-owned Docker lifecycle; no database transaction crosses provider IO."""
import logging
from naiyue_test_agent.diagnostics import safe_log, diagnostic_text
import time
from threading import RLock
from uuid import uuid4
from naiyue_test_agent.contracts.sandbox import SandboxError,SandboxRecord
from naiyue_test_agent.execution.docker_cli import DockerCLI
from naiyue_test_agent.execution.docker_environment import DockerEnvironment
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
from naiyue_test_agent.contracts.session_environment import SandboxProfileSelection
from .activity import EnvironmentActivity

logger=logging.getLogger(__name__)


class EnvironmentService:
    def __init__(self,projects,settings):
        self.projects=projects
        self.configuration=SandboxConfiguration.model_validate(settings)
        self._locks={}
        self._guard=RLock()
        self.operations=None
        self.activity=EnvironmentActivity(self.records)
        from naiyue_test_agent.execution.sandbox_transport import SandboxTransport
        self.transport=SandboxTransport(projects.data_dir,self.configuration)

    @property
    def settings(self):
        return self.configuration.execution_settings()

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
        engine=DockerEnvironment(DockerCLI(SandboxSettings.model_validate(row.settings),self.projects.data_dir))
        engine.transport=self.transport
        return engine

    def _project(self,project_id):
        facts=self.projects.registry.execution_facts(project_id)
        if facts.lifecycle!='active' or not facts.trusted:raise SandboxError('env_project_unavailable')

    def create(self,project_id,session_id,*,binding_id=None,selection=None):
        binding_id=binding_id or uuid4().hex
        with self.activity.use(binding_id):
            return self._prepare_create(project_id,session_id,binding_id=binding_id,selection=selection)

    def _prepare_create(self,project_id,session_id,*,binding_id,selection):
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
                    if selection is not None:
                        try:matches=SandboxProfileSelection.from_record(existing)==selection
                        except ValueError as error:raise SandboxError('env_binding_mismatch') from error
                        if not matches:raise SandboxError('env_binding_mismatch')
                    return existing
                return self._create(project_id,session_id,binding_id,selection)

    def _create(self,project_id,session_id,binding_id,selection):
        self._project(project_id)
        selection=selection or self.configuration.select()
        settings=selection.settings
        cli=DockerCLI(settings,self.projects.data_dir)
        daemon,image=cli.preflight(settings.image)
        if image.get('Os')!='linux':raise SandboxError('env_platform_unsupported')
        row=SandboxRecord(binding_id,project_id,session_id,'creating',daemon,
            'nyt-e-'+binding_id,'nyt-v-'+binding_id,image['Id'],settings.model_dump(mode='json'),
            profile_id=selection.profile_id,profile_name=selection.name,profile_revision=selection.revision,
            created_at=time.time(),last_used_at=time.time())
        records=self.records()
        with self._lock(binding_id):
            records.reserve(row,limits=self.configuration.lifecycle)
            engine=self._engine(row)
            phase='create'
            try:
                row=engine.create(row,records.save)
                phase='handshake'
                engine.handshake(row)
                row=row.changed(lifecycle='running')
                phase='ready_save'
                records.save(row)
            except Exception as error:
                safe_log(logger.error, 'sandbox_creation_failed error_type=%s diagnostic=%s',type(error).__name__,
                    diagnostic_text(error=error,phase=phase,binding_id=binding_id,project_id=project_id,
                        session_id=session_id,code=getattr(error,'code',None)))
                saved=records.get(binding_id)
                records.save(saved.changed(lifecycle='unknown',error_code=getattr(error,'code','env_operation_unknown')))
                raise
        return self.get(binding_id)

    def get(self,binding_id):return self.records().get(binding_id)
    def list(self,project_id):return self.records().list(project_id)

    def capacity_error(self):
        usage=self.records().usage();limits=self.configuration.lifecycle
        if usage['retained']>=limits.max_retained:return 'env_retained_capacity'
        if usage['running']>=limits.max_running:return 'env_running_capacity'
        return None

    def validate(self,binding_id,*,require_input=True):
        row=self.check(binding_id,require_input=require_input)
        self.transport.validate(row)
        return row

    def check(self,binding_id,*,require_input=True):
        row=self.get(binding_id)
        self._project(row.project_id)
        if row.lifecycle=='destroyed':raise SandboxError('env_expired')
        if row.lifecycle!='running':raise SandboxError('env_stopped' if row.lifecycle=='stopped' else 'env_operation_unknown')
        if require_input and row.input_id is not None and row.input_snapshot is None:
            raise SandboxError('git_input_install_unconfirmed')
        return row

    async def arequest(self,binding_id,operation,params,*,mutates=False,control=None):
        from naiyue_test_agent.agent_runtime.async_utils import run_blocking
        async with self.activity.ause(binding_id):
            if mutates and self.operations is not None:
                return await self.operations.afile_call(binding_id,operation,params,control)
            row=await run_blocking(self.check,binding_id)
            if operation in ('find','grep') and control is not None:
                import asyncio
                from naiyue_test_agent.tools.control import check_tool_stop
                request=asyncio.create_task(self.transport.arequest(row,operation,params,mutates=False))
                try:
                    while True:
                        done,_=await asyncio.wait((request,),timeout=row.settings['poll_milliseconds']/1000)
                        check_tool_stop(control,'Sandbox search was stopped; partial results were not returned.')
                        if done:return request.result()
                finally:
                    if not request.done():request.cancel()
                    await asyncio.gather(request,return_exceptions=True)
            return await self.transport.arequest(row,operation,params,mutates=mutates)

    def action(self,binding_id,action):
        with self.activity.manage(binding_id):return self._perform_action(binding_id,action)

    def _perform_action(self,binding_id,action,*,shutdown=False):
        """Caller owns activity.manage, including asynchronous management callers."""
        with self._lock(binding_id):
            row=self.get(binding_id)
            if row.lifecycle=='destroyed':
                if action=='destroy':return row
                raise SandboxError('env_expired')
            if action not in ('start','stop','recover','destroy'):raise SandboxError('env_operation_unsupported')
            if action=='stop' and not shutdown and self.operations is not None and self.operations.unsettled(binding_id):
                raise SandboxError('env_busy')
            if action=='start':
                self._project(row.project_id)
                if row.lifecycle not in ('running','stopped'):raise SandboxError('env_operation_unknown')
                row=self.records().reserve_start(binding_id,limits=self.configuration.lifecycle)
            engine=self._engine(row)
            phase='action'
            try:
                self.transport.invalidate(binding_id,wait=action in ('start','recover'))
                row=engine.action(row,action)
                if action!='recover':row=row.changed(last_used_at=time.time())
                phase='state_save'
                self.records().save(row)
                phase='state_read'
                return self.get(binding_id)
            except Exception as error:
                safe_log(logger.error, 'sandbox_lifecycle_failed action=%s error_type=%s diagnostic=%s',action,type(error).__name__,
                    diagnostic_text(error=error,phase=phase,binding_id=binding_id,session_id=row.session_id,
                        project_id=row.project_id,code=getattr(error,'code',None)))
                self.records().save(row.changed(lifecycle='unknown',error_code=getattr(error,'code','env_operation_unknown')))
                raise
