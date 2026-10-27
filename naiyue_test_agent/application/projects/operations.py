"""Resume only the recorded project operation, preserving ambiguous outcomes."""
from contextlib import nullcontext
from dataclasses import replace
import logging
from pathlib import Path
import subprocess

from naiyue_test_agent.contracts.projects import LocalSource, ProjectError
from naiyue_test_agent.config_paths import DEFAULT_BUSY_TIMEOUT_MS
from naiyue_test_agent.storage.project_databases import bind_reserved_identity, deletion_guard, initialize_reserved_store, legacy_guard
from .locations import check_legacy_marker, mkdir_under, require_same_directory

logger = logging.getLogger(__name__)


def resume(registry, operation, *, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
    if operation.stage == 'done':
        return operation.result
    try:
        return _resume(registry, operation, busy_timeout_ms=busy_timeout_ms)
    except Exception as error:
        code = error.code if isinstance(error, ProjectError) else 'project_operation_failed'
        logger.error('project_operation_failed action=%s error_type=%s', operation.action, type(error).__name__)
        try:
            registry.fail(operation.operation_id, code)
        except Exception as secondary:
            logger.error('project_operation_error_save_failed error_type=%s', type(secondary).__name__)
        if isinstance(error, ProjectError):
            raise
        raise ProjectError(code) from error


def _resume(registry, operation, *, busy_timeout_ms):
    project = registry.get(operation.project_id)
    request, facts = operation.request, operation.facts
    action, identity = operation.action, operation.operation_id
    if action == 'create_local':
        root = project.source.path
        if operation.stage == 'reserved':
            require_same_directory(root.parent, facts['parent'])
            registry.progress(identity, 'mkdir_planned', facts)
            operation = registry.operation(identity)
        if operation.stage == 'mkdir_planned':
            require_same_directory(root.parent, facts['parent'])
            if root.exists():
                raise ProjectError('project_creation_unconfirmed')
            created = mkdir_under(root.parent, root.name, facts['parent'])
            facts = {**facts, 'directory': created}
            registry.progress(identity, 'directory_created', facts)
            operation = registry.operation(identity)
        require_same_directory(root, facts['directory'])
        if request['git_init'] and operation.stage not in ('git_initialized', 'database_created', 'database_ready'):
            result = subprocess.run(['git', 'init', '--quiet'], cwd=root, capture_output=True, timeout=30)
            if result.returncode:
                raise ProjectError('project_git_init_failed')
            registry.progress(identity, 'git_initialized', facts)
    elif action in ('add_local', 'relocate', 'select_existing'):
        target = Path(request['root'])
        require_same_directory(target, facts['directory'])
    if action in ('add_local', 'create_local','initialize_git'):
        with initialize_reserved_store(project, expected_file=facts.get('database_file'),
                                       busy_timeout_ms=busy_timeout_ms) as (store, receipt):
            facts = {**facts, 'database_file': receipt}
            registry.progress(identity, 'database_created', facts)
            bind_reserved_identity(store, project)
        registry.progress(identity, 'database_ready', facts)
    elif action == 'relocate':
        project = replace(project, source=LocalSource(Path(request['root'])), trusted=True)
    elif action == 'rename':
        project = replace(project, name=request['name'])
    elif action == 'select_existing':
        project = replace(project, trusted=True)
    elif action == 'trust_git':
        project = replace(project, trusted=True)
    elif action == 'import_legacy':
        with legacy_guard(project.database_path) as (identity, root):
            if identity != project.project_id or str(root) != request['root']:
                raise ProjectError('project_identity_conflict')
            check_legacy_marker(root, identity)
            registry.finish(operation.operation_id, project=project)
        return {'project_id': project.project_id}
    elif action == 'remove':
        guard = deletion_guard(project) if project.database_path.exists() else nullcontext()
        with guard:
            result = {'project_id': project.project_id, 'data_retained': True}
            registry.finish(identity, remove=True, result=result)
        return result
    if isinstance(project.source, LocalSource) and action != 'rename':
        require_same_directory(project.source.path, facts['directory'])
        check_legacy_marker(project.source.path, project.project_id)
    registry.finish(identity, project=project)
    return {'project_id': project.project_id}
