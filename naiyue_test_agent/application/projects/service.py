"""User-confirmed project registration, distinct from project runtime ownership."""
from dataclasses import replace
import os
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

from naiyue_test_agent.contracts.projects import GitSource, LocalSource, ProjectError, RegisteredProject
from naiyue_test_agent.config_paths import StorageSettings
from naiyue_test_agent.storage.project_registry import ProjectRegistry
from .locations import availability, canonical, directory_facts, directory_identity, display_name, folder_name as validate_folder
from .operations import resume

class ProjectService:
    def __init__(self, data_dir, settings, *, storage_settings=None):
        self.data_dir = canonical(data_dir)
        self.settings = settings
        self.storage_settings = storage_settings if storage_settings is not None else StorageSettings()
        self._lock, self._repo, self._closed = RLock(), None, False

    @property
    def registry(self):
        if self._closed:
            raise ProjectError('project_registry_closed')
        if self._repo is None:
            self._repo = ProjectRegistry(self.data_dir / 'projects.sqlite')
            self.reconcile()
        return self._repo

    def close(self):
        with self._lock:
            self._closed = True
            if self._repo is not None:
                self._repo.close()

    def environment_records(self):
        from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
        with self._lock:
            return EnvironmentRegistry(self.registry.database)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _view(self, project):
        if not isinstance(project.source, LocalSource):
            return project
        root = project.source.path
        nested = tuple(other.project_id for other in self.registry.list()
            if other.project_id != project.project_id and isinstance(other.source, LocalSource)
            and (root.is_relative_to(other.source.path) or other.source.path.is_relative_to(root)))
        return replace(project, availability=availability(root), nested_project_ids=nested)

    def list(self):
        with self._lock:
            return tuple(self._view(project) for project in self.registry.list())

    def get(self, project_id):
        with self._lock:
            return self._view(self.registry.get(project_id))

    def operations(self):
        with self._lock:
            return self.registry.operations()

    def _retry(self, operation_id, action, request):
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 200 or '\0' in operation_id:
            raise ProjectError('project_invalid_operation_id')
        operation = self.registry.operation(operation_id)
        if operation is not None:
            matching = operation.action == action or action == 'add_local' and operation.action == 'select_existing'
            if not matching or operation.request != request:
                raise ProjectError('project_operation_conflict')
            return resume(self.registry, operation, busy_timeout_ms=self.storage_settings.busy_timeout_ms)
        return None

    def _finish(self, operation):
        result = resume(self.registry, operation, busy_timeout_ms=self.storage_settings.busy_timeout_ms)
        return self._view(self.registry.get(result['project_id']))

    def _active(self, project_id):
        project = self.registry.get(project_id)
        if project.lifecycle != 'active':
            raise ProjectError('project_operation_pending')
        return project

    def add_local(self, root, *, trusted, operation_id, name=None, expected_directory=None):
        with self._lock:
            root = canonical(root)
            label = display_name(name if name is not None else root.name or str(root))
            request = {'root': str(root), 'name': label, 'trusted': trusted}
            if expected_directory is not None:
                request['expected_directory'] = expected_directory
            retry = self._retry(operation_id, 'add_local', request)
            if retry is not None:
                return self.get(retry['project_id'])
            if trusted is not True:
                raise ProjectError('project_trust_required')
            facts = directory_facts(root)
            if expected_directory is not None and directory_identity(root, facts) != expected_directory:
                raise ProjectError('project_location_changed')
            existing = self.registry.by_path(root)
            if existing is not None:
                self._active(existing.project_id)
                operation = self.registry.reserve(operation_id, 'select_existing', request, existing,
                    facts={'directory': facts})
                return self._finish(operation)
            if os.path.lexists(root / '.naiyue/workspace.json'):
                raise ProjectError('project_legacy_import_required')
            identity = str(uuid4())
            project = RegisteredProject(identity, label, LocalSource(root),
                self.data_dir / 'workspaces' / identity / 'sessions.sqlite', trusted=True)
            return self._finish(self.registry.reserve(operation_id, 'add_local', request, project,
                new=True, facts={'directory': facts}))

    def create_local(self, *, folder_name, trusted, operation_id, parent=None, name=None, git_init=False, expected_directory=None):
        with self._lock:
            folder = validate_folder(folder_name)
            parent = parent if parent is not None else self.settings.default_parent_dir
            if parent is None:
                raise ProjectError('project_parent_required')
            parent = canonical(parent)
            root = parent / folder
            label = display_name(name if name is not None else folder)
            request = {'root': str(root), 'name': label, 'trusted': trusted, 'git_init': git_init}
            if expected_directory is not None:
                request['expected_directory'] = expected_directory
            retry = self._retry(operation_id, 'create_local', request)
            if retry is not None:
                return self.get(retry['project_id'])
            if trusted is not True:
                raise ProjectError('project_trust_required')
            if type(git_init) is not bool:
                raise ProjectError('project_invalid_request')
            if os.path.lexists(root):
                raise ProjectError('project_directory_exists')
            facts = directory_facts(parent)
            if expected_directory is not None and directory_identity(parent, facts) != expected_directory:
                raise ProjectError('project_location_changed')
            identity = str(uuid4())
            project = RegisteredProject(identity, label, LocalSource(root),
                self.data_dir / 'workspaces' / identity / 'sessions.sqlite', trusted=True)
            return self._finish(self.registry.reserve(operation_id, 'create_local', request, project,
                new=True, facts={'parent': facts}))

    def add_git(self, url, *, ref, name, operation_id):
        with self._lock:
            if (not isinstance(url, str) or not url.strip() or '\0' in url
                    or not isinstance(ref, str) or not ref.strip() or '\0' in ref):
                raise ProjectError('project_invalid_source')
            try:
                parsed = urlsplit(url)
                if parsed.password is not None or parsed.fragment or parsed.scheme in ('http', 'https') and (parsed.username or parsed.query):
                    raise ValueError('embedded_credentials')
            except ValueError as error:
                raise ProjectError('project_invalid_source') from error
            request = {'url': url, 'ref': ref, 'name': display_name(name)}
            retry = self._retry(operation_id, 'add_git', request)
            if retry is not None:
                return self.get(retry['project_id'])
            identity = str(uuid4())
            project = RegisteredProject(identity, request['name'], GitSource(url, ref),
                self.data_dir / 'workspaces' / identity / 'sessions.sqlite')
            return self._finish(self.registry.reserve(operation_id, 'add_git', request, project, new=True))

    def rename(self, project_id, name, *, operation_id):
        with self._lock:
            request = {'project_id': project_id, 'name': display_name(name)}
            retry = self._retry(operation_id, 'rename', request)
            if retry is not None:
                return self.get(retry['project_id'])
            project = self._active(project_id)
            return self._finish(self.registry.reserve(operation_id, 'rename', request, project))

    def ensure_git_store(self,project_id):
        with self._lock:
            project=self._active(project_id)
            if not isinstance(project.source,GitSource):
                raise ProjectError('project_invalid_source')
            identity='initialize_git:'+project_id
            request={'project_id':project_id}
            if self._retry(identity,'initialize_git',request) is None:
                self._finish(self.registry.reserve(identity,'initialize_git',request,project))

    def trust_git(self,project_id,*,expected_revision,operation_id):
        with self._lock:
            request={'project_id':project_id,'expected_revision':expected_revision}
            retry=self._retry(operation_id,'trust_git',request)
            if retry is not None:return self.get(project_id)
            project=self._active(project_id)
            if not isinstance(project.source,GitSource):raise ProjectError('project_invalid_source')
            if type(expected_revision) is not int or project.revision!=expected_revision:
                raise ProjectError('project_location_changed')
            return self._finish(self.registry.reserve(operation_id,'trust_git',request,project))

    def relocate(self, project_id, *, expected_root, root, trusted, operation_id):
        with self._lock:
            expected, root = canonical(expected_root), canonical(root)
            request = {'project_id': project_id, 'expected_root': str(expected), 'root': str(root), 'trusted': trusted}
            retry = self._retry(operation_id, 'relocate', request)
            if retry is not None:
                return self.get(retry['project_id'])
            if trusted is not True:
                raise ProjectError('project_trust_required')
            project = self._active(project_id)
            if not isinstance(project.source, LocalSource) or project.source.path != expected:
                raise ProjectError('project_location_conflict')
            other = self.registry.by_path(root)
            if other is not None and other.project_id != project_id:
                raise ProjectError('project_identity_conflict')
            facts = directory_facts(root)
            operation = self.registry.reserve(operation_id, 'relocate', request, project,
                lifecycle='migrating', facts={'directory': facts})
            return self._finish(operation)

    def remove(self, project_id, *, operation_id):
        with self._lock:
            request = {'project_id': project_id}
            retry = self._retry(operation_id, 'remove', request)
            if retry is not None:
                return retry
            project = self._active(project_id)
            operation = self.registry.reserve(operation_id, 'remove', request, project,
                lifecycle='deleting', facts={'database_path': str(project.database_path)})
            return resume(self.registry, operation, busy_timeout_ms=self.storage_settings.busy_timeout_ms)

    def import_legacy(self, paths=None):
        from .legacy import import_legacy
        with self._lock:
            return import_legacy(self, paths)

    def reconcile(self, operation_id=None):
        with self._lock:
            results = []
            if operation_id is None:
                operations = self.registry.operations()
            else:
                operation = self.registry.operation(operation_id)
                if operation is None:
                    raise ProjectError('project_operation_not_found')
                if operation.action not in ('add_local', 'create_local', 'select_existing'):
                    raise ProjectError('project_invalid_operation')
                operations = (operation,)
            for operation in operations:
                if operation.stage == 'done' and operation_id is None:
                    continue
                try:
                    resume(self.registry, operation, busy_timeout_ms=self.storage_settings.busy_timeout_ms)
                    results.append({'operation_id': operation.operation_id, 'error': None})
                except ProjectError as error:
                    results.append({'operation_id': operation.operation_id, 'error': error.code})
            return tuple(results)
