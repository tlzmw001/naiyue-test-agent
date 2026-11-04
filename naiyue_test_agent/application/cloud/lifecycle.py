"""Durable admission plus owner-loop producers; HTTP cancellation does not own work."""
import asyncio
import logging
import sqlite3
import time
from uuid import uuid4

from .work import cloud_work
from pydantic import ValidationError

from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure, EnvironmentCommand, EnvironmentOperation
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CloudTask, CommandReceipt
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from .directories import TaskDirectories
from . import lifecycle_actions
from .producers import CommandProducers

logger = logging.getLogger(__name__)


class CloudLifecycle:
    def __init__(self, runtime, backend):
        self.runtime, self.backend, self.settings = runtime, backend, runtime.settings
        self._idle_after = ''
        self.repository = LifecycleRepository(runtime.database)
        self.directories = TaskDirectories(runtime.settings.data_dir)
        self.producers=CommandProducers('cloud_environment_producer_failed')

    async def local(self, action):
        return await cloud_work(self.runtime, action)

    async def command(self, task_id, value, *, idle_cutoff=None):
        request = EnvironmentCommand.model_validate(value)
        if request.acknowledge_unknown and (idle_cutoff is not None or request.kind=='refresh'):raise CloudError('invalid_environment_acknowledgement')
        fingerprint = digest({'task_id': task_id, 'request': request.model_dump(), 'idle_cutoff': idle_cutoff})
        return await asyncio.shield(self.producers.submit(request.command_id, fingerprint,
            lambda: self._command(task_id, request, fingerprint, idle_cutoff)))

    def _reserve(self, task_id, request, fingerprint, idle_cutoff):
        with self.runtime.gates.hold(task_id):
            try:
                previous = self.runtime.registry.get_command(request.command_id)
            except CloudError as error:
                if error.code != 'command_not_found': raise
            else:
                if previous.request_digest != fingerprint: raise CloudError('command_id_conflict')
                return previous, False
            if request.kind == 'start': self.runtime.resources.ensure()
            task = self.runtime.registry.get_task(task_id)
            if task.revision != request.expected_revision or task.generation != request.expected_generation:
                raise CloudError('task_revision_conflict')
            if task.environment_operation_id or task.artifact_operation_id or (task.control_operation_id and not request.acknowledge_unknown) or task.lifecycle in ('deleted', 'deleting', 'reconciling'):
                raise CloudError('task_busy')
            template = task.template_snapshot or self.settings.templates.get(task.template_id)
            if template is None or template.runner_release_id != task.runner_release_id:
                raise CloudError('template_unavailable')
            if template.backend_id != self.backend.backend_id: raise CloudError('backend_identity_mismatch')
            if task.template_digest is not None and task.template_digest != template.fingerprint:
                raise CloudError('template_pin_mismatch')
            if request.kind in ('start','refresh'):self.runtime.releases.validate_template(template)
            if request.kind == 'start' and task.sandbox_id:
                raise CloudError('environment_already_running')
            if request.kind == 'start' and (task.active_run_id or task.reclaim_blockers) and not (request.acknowledge_unknown and task.lifecycle=='stopped' and task.environment_phase=='absent'):
                raise CloudError('task_busy')
            if request.kind == 'stop_environment':
                if not request.acknowledge_unknown and (task.active_run_id or task.reclaim_blockers
                        or task.lifecycle == 'ready' and task.last_quiescent_at is None):
                    raise CloudError('task_busy')
                if idle_cutoff is not None and not self.idle(task, idle_cutoff): raise CloudError('task_busy')
            now = time.time()
            generation = task.generation + (request.kind == 'start' and task.lifecycle == 'stopped')
            operation = EnvironmentOperation(command_id=request.command_id, task_id=task_id,
                generation=generation, kind=request.kind, phase='reserved', backend_id=self.backend.backend_id,
                template=template, instance_id=self.runtime.instance_id, sandbox_id=task.sandbox_id,
                idle_cutoff=idle_cutoff,acknowledge_unknown=request.acknowledge_unknown)
            candidate = CloudTask.model_validate(task.model_dump() | dict(generation=generation,
                revision=task.revision + 1, lifecycle='starting' if request.kind == 'start' else 'stopping',
                updated_at=now, environment_phase='preparing', environment_operation_id=request.command_id,
                template_digest=template.fingerprint, template_snapshot=template))
            if request.kind=='start':
                candidate=candidate.model_copy(update={'runner_boot_id':None,'runner_connection_id':None,
                    'runner_phase':'unconfigured','runner_model_ids':[],'runner_capabilities':[],
                    'model_profile_id':None})
            receipt = CommandReceipt(command_id=request.command_id, resource_id=task_id,
                operation='environment:' + request.kind, expected_generation=request.expected_generation,
                request_digest=fingerprint, status='accepted', http_status=202,
                result=operation.model_dump(), created_at=now, updated_at=now)
            return self.repository.reserve(task, candidate, receipt, self.settings)

    async def _command(self, task_id, request, fingerprint, idle_cutoff):
        receipt, fresh = await self.local(lambda: self._reserve(task_id, request, fingerprint, idle_cutoff))
        if not fresh: return receipt
        return await self._execute(receipt)

    async def reconcile(self, command_id):
        receipt = await self.local(lambda: self.runtime.registry.get_command(command_id))
        try: operation = EnvironmentOperation.model_validate(receipt.result)
        except ValidationError: raise CloudError('command_not_environment', 422) from None
        if operation.backend_id != self.backend.backend_id: raise CloudError('backend_identity_mismatch')
        return await asyncio.shield(self.producers.submit(command_id, receipt.request_digest, lambda: self._execute(receipt, reconcile=True)))

    async def _execute(self, receipt, *, reconcile=False):
        async with self.runtime.task_io.use(receipt.resource_id):
            if reconcile:
                # Admission may have observed a running producer that finished
                # before this producer was submitted. Persisted facts win.
                receipt = await self.local(lambda: self.runtime.registry.get_command(receipt.command_id))
            if receipt.status in ('succeeded', 'failed'): return receipt
            operation = EnvironmentOperation.model_validate(receipt.result)
            try:
                if operation.backend_id != self.backend.backend_id: raise BackendFailure('backend_identity_mismatch')
                if operation.kind in ('start','refresh'):
                    await self.local(lambda:self.runtime.releases.validate_template(operation.template))
                if operation.kind == 'start':
                    return await lifecycle_actions.start(self, receipt, operation, reconcile=reconcile)
                if operation.kind == 'refresh':
                    return await lifecycle_actions.refresh(self, receipt, operation)
                return await lifecycle_actions.stop(self, receipt, operation)
            except (BackendFailure, CloudError, OSError) as error:
                logger.warning('cloud_environment_action_unconfirmed code=%s', getattr(error, 'code', 'directory_unavailable'))
                current = await self.local(lambda: self.runtime.registry.get_command(receipt.command_id))
                operation = EnvironmentOperation.model_validate(current.result)
                failed = (operation.phase == 'reserved' or isinstance(error, BackendFailure)
                    and error.rejected and operation.kind == 'start' and operation.sandbox_id is None)
                return await self.finish(current, operation, status='failed' if failed else 'unknown',
                    sandbox_id=operation.sandbox_id, error_code=getattr(error, 'code', 'directory_unavailable'))

    def _update(self, receipt, operation, **changes):
        with self.runtime.gates.hold(operation.task_id):
            current = self.runtime.registry.get_task(operation.task_id)
            if current.lifecycle in ('deleting', 'deleted'): raise CloudError('task_not_available')
            if current.generation != operation.generation or current.environment_operation_id != receipt.command_id:
                raise CloudError('command_state_conflict')
            updated = operation.model_copy(update=changes)
            receipt = receipt.model_copy(update={'status': 'running', 'result': updated.model_dump(), 'updated_at': time.time()})
            self.repository.save_receipt(receipt)
            return receipt, updated

    async def dispatch(self, receipt, operation):
        return await self.local(lambda: self._update(receipt, operation, phase='dispatched'))

    async def discovered(self, receipt, operation, sandbox_id):
        return await self.local(lambda: self._update(receipt, operation, sandbox_id=sandbox_id))

    async def finish(self, receipt, operation, *, status, sandbox_id, error_code=None):
        def commit():
            with self.runtime.gates.hold(operation.task_id):
                task = self.runtime.registry.get_task(operation.task_id)
                if task.lifecycle in ('deleting', 'deleted'): raise CloudError('task_not_available')
                if task.generation != operation.generation or task.environment_operation_id != operation.command_id:
                    raise CloudError('command_state_conflict')
                now = time.time()
                unknown = status == 'unknown'
                running = status == 'succeeded' and sandbox_id is not None and operation.kind in ('start', 'refresh')
                candidate = CloudTask.model_validate(task.model_dump() | dict(revision=task.revision + 1,
                    updated_at=now, lifecycle='reconciling' if unknown else 'starting' if running else 'stopped',
                    environment_phase='unknown' if unknown else 'backend_ready' if running else 'absent',
                    environment_operation_id=operation.command_id if unknown else None,
                    sandbox_id=sandbox_id, last_error_code=error_code))
                if running:
                    candidate = candidate.model_copy(update={'input_ready': True})
                    if task.runner_phase=='ready' and task.runner_connection_id:
                        candidate=candidate.model_copy(update={'lifecycle':'ready'})
                        if not task.active_run_id and not task.reclaim_blockers:
                            candidate=candidate.model_copy(update={'last_quiescent_at':now})
                related=[]
                if status=='succeeded' and operation.kind=='stop_environment':
                    candidate=candidate.model_copy(update={'runner_connection_id':None,'runner_phase':'unconfigured'})
                    if operation.acknowledge_unknown and task.control_operation_id:
                        control=self.runtime.registry.get_command(task.control_operation_id)
                        if control.status not in ('succeeded','failed'):
                            related.append(control.model_copy(update={'status':'failed','http_status':200,
                                'error_code':'control_environment_stopped','updated_at':now}))
                        candidate=candidate.model_copy(update={'control_operation_id':None,
                            'reclaim_blockers':[x for x in task.reclaim_blockers if x not in ('control_operation','control_unknown')]})
                result = operation.model_copy(update={'phase': 'unknown' if unknown else 'finished', 'sandbox_id': sandbox_id})
                updated = receipt.model_copy(update={'status': status, 'http_status': 202 if unknown else 200,
                    'result': result.model_dump(), 'error_code': error_code, 'updated_at': now})
                self.repository.finish(task, candidate, updated,related=related)
                return updated
        return await self.local(commit)

    @staticmethod
    def idle(task, cutoff):
        return bool(task.sandbox_id and task.environment_phase == 'backend_ready'
            and task.lifecycle in ('ready', 'starting')
            and task.last_quiescent_at is not None and task.last_quiescent_at <= cutoff
            and not task.active_run_id and not task.reclaim_blockers and not task.environment_operation_id
            and not task.artifact_operation_id and not task.control_operation_id)

    async def sweep_idle(self, *, now=None, stopping=None):
        if not self.settings.idle_stop_seconds: return []
        cutoff = (time.time() if now is None else now) - self.settings.idle_stop_seconds
        limit = self.settings.resource_limits.task_batch_size
        rows = await self.local(lambda: self.repository.task_page(self._idle_after, limit))
        self._idle_after = rows[-1].task_id if len(rows) == limit else ''
        results = []
        for task in rows:
            if stopping is not None and stopping.is_set(): break
            if self.idle(task, cutoff):
                try:
                    results.append(await self.command(task.task_id, dict(command_id=str(uuid4()), kind='stop_environment',
                        expected_revision=task.revision, expected_generation=task.generation), idle_cutoff=cutoff))
                except CloudError as error:
                    if error.code in ('task_busy', 'task_revision_conflict'):
                        logger.info('cloud_idle_stop_raced code=%s', error.code)
                    else:
                        logger.warning('cloud_idle_stop_failed task_id=%s code=%s', task.task_id, error.code)
                except (OSError, sqlite3.Error) as error:
                    logger.error('cloud_idle_stop_failed task_id=%s error_type=%s', task.task_id, type(error).__name__)
        return results

    async def aclose(self):
        await self.producers.aclose()
