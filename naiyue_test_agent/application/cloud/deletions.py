"""Owner deletion: durable fence, verified physical stop, scoped IO drain and tombstone."""
import asyncio
import logging
import sqlite3
import time

from naiyue_test_agent.contracts.cloud_delete import DeleteTask, DeleteOperation
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CommandReceipt
from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure, EnvironmentOperation
from naiyue_test_agent.storage.cloud.deletions import DeletionRepository
from .lifecycle_actions import verify_existing
from .producers import CommandProducers
from .task_cleanup import remove_task_data
from .work import cloud_work

logger = logging.getLogger(__name__)
SCOPE = ['workspace', 'state', 'history', 'artifacts', 'task_command_bodies']


class CloudDeletions:
    def __init__(self, runtime, backend):
        self.runtime, self.backend = runtime, backend
        self.repository = DeletionRepository(runtime.database)
        self.producers = CommandProducers('cloud_delete_producer_failed')

    def preview(self, task_id):
        with self.runtime.gates.hold(task_id):
            task = self.runtime.registry.get_task(task_id)
            return dict(instance_id=self.runtime.instance_id, task_id=task_id, revision=task.revision,
                generation=task.generation, lifecycle=task.lifecycle, scope=SCOPE,
                requires_stop=bool(task.active_run_id or task.reclaim_blockers or task.environment_operation_id
                    or task.artifact_operation_id or task.control_operation_id
                    or task.sandbox_id and task.last_quiescent_at is None),
                preview_digest=digest(dict(instance=self.runtime.instance_id, task=task.model_dump(), scope=SCOPE)))

    async def command(self, task_id, value):
        request = DeleteTask.model_validate(value)
        fingerprint = digest(dict(task_id=task_id, request=request.model_dump()))
        return await asyncio.shield(self.producers.submit(request.command_id, fingerprint,
            lambda: self._command(task_id, request, fingerprint)))

    def reserve(self, task_id, request, fingerprint):
        r = self.runtime
        with r.gates.hold(task_id):
            try: old = r.registry.get_command(request.command_id)
            except CloudError as error:
                if error.code != 'command_not_found': raise
            else:
                if old.request_digest != fingerprint or old.operation != 'task:delete': raise CloudError('command_id_conflict')
                return old, False
            task = r.registry.get_task(task_id)
            if task.lifecycle in ('deleting', 'deleted'): raise CloudError('task_not_available')
            if task.revision != request.expected_revision or task.generation != request.expected_generation:
                raise CloudError('task_revision_conflict')
            expected = digest(dict(instance=r.instance_id, task=task.model_dump(), scope=SCOPE))
            if request.confirm_task_id != task_id or request.preview_digest != expected:
                raise CloudError('task_delete_confirmation_changed')
            blocked = bool(task.active_run_id or task.reclaim_blockers or task.environment_operation_id
                or task.artifact_operation_id or task.control_operation_id
                or task.sandbox_id and task.last_quiescent_at is None)
            if blocked and not request.acknowledge_unknown: raise CloudError('task_delete_requires_stop')
            environment = (EnvironmentOperation.model_validate(r.registry.get_command(task.environment_operation_id).result)
                if task.environment_operation_id else None)
            # Force only discards an explicitly confirmed uncertain boundary; it
            # is not a shortcut past a known running, healthy task.
            uncertain = (task.lifecycle in ('reconciling', 'failed') or task.environment_phase == 'unknown'
                or environment is not None and environment.phase in ('dispatched', 'unknown')
                or task.runner_phase in ('disconnected', 'blocked')
                or any('unknown' in b or b == 'runner_disconnected' for b in task.reclaim_blockers))
            if blocked and request.acknowledge_unknown and not uncertain:
                raise CloudError('task_delete_requires_stop')
            metadata = {'nyt-instance': r.instance_id, 'nyt-task': task_id, 'nyt-generation': str(task.generation)}
            if environment is not None: metadata = environment.metadata
            operation = DeleteOperation(command_id=request.command_id, task_id=task_id, instance_id=r.instance_id,
                generation=task.generation, template=task.template_snapshot, sandbox_id=task.sandbox_id,
                metadata=metadata, environment_command_id=task.environment_operation_id,
                creation_unconfirmed=bool(environment is not None and environment.kind=='start'
                    and environment.phase in ('dispatched','unknown') and task.sandbox_id is None))
            now = time.time()
            candidate = task.model_copy(update=dict(revision=task.revision + 1, lifecycle='deleting',
                delete_command_id=request.command_id, runner_connection_id=None, updated_at=now))
            receipt = CommandReceipt(command_id=request.command_id, resource_id=task_id, operation='task:delete',
                expected_generation=task.generation, request_digest=fingerprint, status='accepted', http_status=202,
                result=operation.model_dump(), created_at=now, updated_at=now)
            return self.repository.reserve(task, candidate, receipt, r.settings)

    async def _command(self, task_id, request, fingerprint):
        receipt, fresh = await cloud_work(self.runtime, lambda: self.reserve(task_id, request, fingerprint))
        return await self.execute(receipt) if fresh else receipt

    async def reconcile(self, command_id):
        receipt = await cloud_work(self.runtime, lambda: self.runtime.registry.get_command(command_id))
        if receipt.operation != 'task:delete': raise CloudError('invalid_delete_command')
        return await asyncio.shield(self.producers.submit(command_id, receipt.request_digest,
            lambda: self.execute(receipt)))

    def stage(self, receipt, operation, phase, *, error=None, complete=False):
        r = self.runtime
        with r.gates.hold(operation.task_id):
            previous = r.registry.get_task(operation.task_id)
            if previous.delete_command_id != receipt.command_id or previous.generation != operation.generation:
                raise CloudError('command_state_conflict')
            now = time.time(); changes = dict(revision=previous.revision + 1, updated_at=now, last_error_code=error)
            if phase in ('stopped', 'cleaning', 'finished'):
                changes.update(sandbox_id=None, environment_phase='absent', environment_operation_id=None,
                    active_run_id=None, artifact_operation_id=None, control_operation_id=None,
                    reclaim_blockers=[], runner_connection_id=None, runner_phase='unconfigured')
            if complete:
                changes.update(lifecycle='deleted', title='', result_ids=[], input_ready=False,
                    runner_recovery=None, runner_model_ids=[], runner_capabilities=[], model_profile_id=None)
                tombstone = dict(instance_id=r.instance_id, task_id=operation.task_id,
                    delete_command_id=receipt.command_id, generation=operation.generation, deleted_at=now)
                operation = operation.model_copy(update={'tombstone': tombstone})
            operation = operation.model_copy(update={'phase': phase})
            updated = receipt.model_copy(update=dict(status='succeeded' if complete else 'unknown' if error else 'running',
                http_status=200 if complete else 202, error_code=error, result=operation.model_dump(), updated_at=now))
            self.repository.save(previous, previous.model_copy(update=changes), updated, complete=complete)
            return updated, operation

    async def execute(self, receipt):
        r = self.runtime
        receipt = await cloud_work(r, lambda: r.registry.get_command(receipt.command_id))
        if receipt.status == 'succeeded': return receipt
        operation = DeleteOperation.model_validate(receipt.result)
        try:
            r.observations.delete_task(operation.task_id)
            connection = r.control.connections.pop(operation.task_id, None)
            if connection is not None:
                try:
                    async with asyncio.timeout(r.settings.control_io_timeout_seconds):
                        await connection.websocket.close(code=1008, reason='task_deleted')
                except (OSError, RuntimeError, TimeoutError) as error:
                    logger.warning('cloud_deleted_connection_close_failed error_type=%s', type(error).__name__)
            if r.models.relay is not None: await r.models.relay.close_task(operation.task_id)
            for lease in tuple(r.artifacts.downloads):
                if lease.task_id == operation.task_id: await lease.aclose()
            await cloud_work(r, lambda: r.task_io.drain(operation.task_id), drain_cancel=True)
            if operation.phase == 'reserved':
                receipt, operation = await self.stop(receipt, operation)
                receipt, operation = await cloud_work(r, lambda: self.stage(receipt, operation, 'stopped'))
            def close_history():
                with r.gates.hold(operation.task_id):
                    history = r._histories.get(operation.task_id)
                    if history is not None:
                        history.database.close(); r._histories.pop(operation.task_id)
            await cloud_work(r, close_history, drain_cancel=True)
            receipt, operation = await cloud_work(r, lambda: self.stage(receipt, operation, 'cleaning'))
            await cloud_work(r, lambda: remove_task_data(r.settings.data_dir, operation.task_id,
                r.settings.resource_limits), drain_cancel=True)
            receipt, operation = await cloud_work(r, lambda: self.stage(receipt, operation, 'finished', complete=True))
            return receipt
        except (BackendFailure, CloudError, OSError, sqlite3.Error, TimeoutError) as error:
            code = error.code if isinstance(error, (BackendFailure, CloudError)) else 'task_delete_storage_unavailable'
            logger.warning('cloud_task_delete_unconfirmed task_id=%s code=%s', operation.task_id, code)
            receipt = await cloud_work(r, lambda: r.registry.get_command(receipt.command_id))
            operation = DeleteOperation.model_validate(receipt.result)
            receipt, _ = await cloud_work(r, lambda: self.stage(receipt, operation, operation.phase, error=code))
            return receipt

    async def stop(self, receipt, operation):
        if operation.template is not None and operation.template.backend_id != self.backend.backend_id:
            raise BackendFailure('backend_identity_mismatch')
        if operation.template is None:
            if operation.sandbox_id or operation.environment_command_id: raise BackendFailure('template_unavailable')
        if operation.sandbox_id:
            facts = await self.backend.get(operation.sandbox_id)
            if facts is not None:
                verify_existing(operation, facts); await self.backend.kill(facts.sandbox_id)
            if await self.backend.get(operation.sandbox_id) is not None: raise BackendFailure('sandbox_stop_unconfirmed')
        matches = await self.backend.find(operation.metadata)
        if len(matches) > 1: raise BackendFailure('sandbox_reconcile_ambiguous')
        if not matches and operation.creation_unconfirmed: raise BackendFailure('sandbox_create_unconfirmed')
        if matches:
            if operation.template is None: raise BackendFailure('template_unavailable')
            facts = matches[0]; verify_existing(operation, facts)
            if operation.sandbox_id is None:
                operation = operation.model_copy(update={'sandbox_id': facts.sandbox_id, 'creation_unconfirmed': False})
                receipt, operation = await cloud_work(self.runtime, lambda: self.stage(receipt, operation, 'reserved'))
            await self.backend.kill(facts.sandbox_id)
            if await self.backend.get(facts.sandbox_id) is not None: raise BackendFailure('sandbox_stop_unconfirmed')
        if await self.backend.find(operation.metadata): raise BackendFailure('sandbox_stop_unconfirmed')
        return receipt, operation

    async def aclose(self): await self.producers.aclose()
