"""One explicit owner for two apps; no model, Docker or local Host startup."""
from contextlib import ExitStack, contextmanager
import logging
import sqlite3
import time
from threading import Condition

from pydantic import ValidationError

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.registry import CloudRegistry
from .ownership import CloudOwner
from .commands import CloudCommands
from .credentials import TaskCredentials
from .task_gates import TaskGates
from naiyue_test_agent.contracts.cloud_events import EventBatch, canonical_json
from naiyue_test_agent.storage.cloud.history import CloudHistory

logger = logging.getLogger(__name__)


class CloudRuntime:
    def __init__(self, settings, owner, database):
        self.settings, self.owner, self.database = settings, owner, database
        self.instance_id = owner.record['instance_id']
        self.registry = CloudRegistry(database)
        self.registry.bind_instance(self.instance_id, initialize=owner.record['status'] == 'initializing')
        self.commands = CloudCommands(self.registry, settings, self.instance_id)
        self.gates = TaskGates(self.registry)
        self.credentials = TaskCredentials(self.registry, self.gates, settings)
        self._histories = {}
        self._requests = Condition()
        self._active = 0
        self._closing = False
        from .models import CloudModels
        self.models=CloudModels(self)
        self.lifecycle = None
        self.deletions = None
        from .task_io import TaskIO
        self.task_io = TaskIO(self)
        from .resources import CloudResources
        self.resources = CloudResources(self)
        from .inputs import CloudInputs
        self.inputs = CloudInputs(self)
        from .runner_control import RunnerControl
        self.control = RunnerControl(self)
        from .artifacts import CloudArtifacts
        self.artifacts=CloudArtifacts(self)
        from .history import CloudArchive
        self.archive=CloudArchive(self)
        from .observations import CloudObservations
        self.observations=CloudObservations(self)

    @contextmanager
    def operation(self):
        with self._requests:
            if self._closing:
                raise CloudError('cloud_closing', 503)
            self._active += 1
        try:
            yield
        finally:
            with self._requests:
                self._active -= 1
                self._requests.notify_all()

    def close(self):
        with self._requests:
            self._closing = True
            self._requests.notify_all()
            self._requests.wait_for(lambda: self._active == 0)
        with ExitStack() as stack:
            for history in self._histories.values():
                stack.callback(history.database.close)

    def available_task(self, task_id):
        task = self.registry.get_task(task_id)
        if task.lifecycle in ('deleting', 'deleted'): raise CloudError('task_not_available')
        return task

    def _history(self, task_id):
        # Caller holds this registered task's CT1, and the runtime is still owned.
        self.available_task(task_id)
        if task_id not in self._histories:
            path = self.settings.data_dir / 'history' / (task_id + '.sqlite')
            status = self.registry.history_status(task_id)
            if status is None:
                if path.exists() or path.is_symlink():
                    raise CloudError('history_index_missing', 503)
                self.registry.history_status_set(task_id, 'initializing')
            initialize = status != 'ready'
            database = Database.open_cloud(path, profile='history', initialize=initialize)
            try:
                history = CloudHistory(database, task_id, initialize=initialize,limits=self.settings.history_limits,
                    read_timeout=self.settings.history_read_timeout_seconds)
                if initialize:
                    self.registry.history_status_set(task_id, 'ready')
                self._histories[task_id] = history
            except BaseException:
                try:
                    database.close()
                except BaseException as secondary:
                    logger.error('cloud_history_cleanup_secondary_failed error_type=%s', type(secondary).__name__)
                raise
        return self._histories[task_id]

    def history_page(self, task_id, *, epoch=None, after_seq=0, limit=None):
        limit = self.settings.history_page_limit if limit is None else limit
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= self.settings.history_page_limit:
            raise CloudError('invalid_history_page', 422)
        with self.gates.hold(task_id):
            return self._history(task_id).page(epoch=epoch, after_seq=after_seq, limit=limit,
                byte_limit=self.settings.max_frame_bytes, read_timeout=self.settings.history_read_timeout_seconds)

    def identity(self, raw, task_id):
        self.credentials.authorize(raw, task_id, 'identity:read')
        with self.gates.hold(task_id):
            task = self.credentials.authorize(raw, task_id, 'identity:read')
            cursor = self._history(task_id).page(limit=1, byte_limit=self.settings.max_frame_bytes,
                read_timeout=self.settings.history_read_timeout_seconds)
            return dict(task_id=task_id, project_id=task.project_id, generation=task.generation,
                epoch=cursor['epoch'], last_seq=cursor['last_seq'])

    def append_events(self, raw, value):
        try:
            batch = EventBatch.model_validate(value)
            size = len(canonical_json(value).encode())
        except (ValidationError, ValueError, UnicodeError, RecursionError):
            raise CloudError('invalid_request', 422) from None
        if len(batch.events) > self.settings.history_batch_events or size > self.settings.max_frame_bytes:
            raise CloudError('request_too_large', 413)
        self.credentials.authorize(raw, batch.task_id, 'events:append')
        with self.gates.hold(batch.task_id):
            task = self.credentials.authorize(raw, batch.task_id, 'events:append')
            if batch.generation != task.generation:
                raise CloudError('event_generation_mismatch')
            inputs={}
            for event in batch.events:
                payload=event.payload
                if payload.get('version')!=2:continue
                header=payload.get('header',payload)
                command_id=header.get('command_id') if isinstance(header,dict) else None
                if not isinstance(command_id,str):continue
                try:receipt=self.registry.get_command(command_id)
                except CloudError as error:
                    if error.code!='command_not_found':raise
                    continue
                if (receipt.operation!='run:start' or receipt.resource_id!=batch.task_id
                        or receipt.result['command']['run_id']!=event.run_id):raise CloudError('history_command_mismatch',403)
                inputs[command_id]=receipt.result['command']
            return self._history(batch.task_id).append(batch,owner_inputs=inputs)

    def replace_task(self, replacement, *, expected_revision, expected_generation):
        from naiyue_test_agent.contracts.cloud_tasks import CloudTask
        replacement = CloudTask.model_validate(replacement)
        with self.gates.hold(replacement.task_id):
            previous = self.available_task(replacement.task_id)
            if previous.revision != expected_revision or previous.generation != expected_generation:
                raise CloudError('task_revision_conflict')
            if previous.environment_operation_id or previous.artifact_operation_id:
                raise CloudError('task_busy')
            if previous.template_snapshot is not None and any(getattr(previous, key) != getattr(replacement, key)
                    for key in ('template_id', 'runner_release_id', 'template_digest', 'template_snapshot')):
                raise CloudError('task_immutable_identity')
            if any(getattr(previous, k) != getattr(replacement, k) for k in ('project_id', 'input_id', 'created_at')):
                raise CloudError('task_immutable_identity')
            if replacement.revision != previous.revision + 1 or replacement.generation not in (previous.generation, previous.generation + 1):
                raise CloudError('task_revision_conflict')
            self.registry.save_task_cas(replacement, expected_revision=expected_revision,
                expected_generation=expected_generation, revoked_at=time.time())
        return replacement


@contextmanager
def open_cloud_runtime(settings):
    stack, primary = ExitStack(), None
    try:
        owner = stack.enter_context(CloudOwner(settings))
        if owner.record['status'] == 'ready' and not (settings.data_dir / 'cloud.sqlite').exists():
            raise CloudError('cloud_registry_missing', 503)
        database = Database.open_cloud(settings.data_dir / 'cloud.sqlite', profile='registry',
            initialize=owner.record['status'] == 'initializing')
        stack.callback(database.close)
        runtime = CloudRuntime(settings, owner, database)
        stack.callback(runtime.close)
        owner.ready()
        yield runtime
    except BaseException as error:
        primary = error
        if isinstance(error, (OSError, sqlite3.Error)):
            logger.error('cloud_runtime_io_failed error_type=%s', type(error).__name__)
            raise CloudError('cloud_storage_unavailable', 503) from error
        raise
    finally:
        try:
            stack.close()
        except BaseException as error:
            logger.error('cloud_cleanup_secondary_failed' if primary else 'cloud_cleanup_failed',
                extra={'error_type': type(error).__name__})
            if primary is None:
                raise CloudError('cloud_storage_unavailable', 503) from error
