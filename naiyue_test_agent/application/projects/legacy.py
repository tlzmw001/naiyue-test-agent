"""Explicit, repeatable import from known application stores, not disk discovery."""
import hashlib
import logging
from pathlib import Path

from naiyue_test_agent.contracts.projects import LocalSource, ProjectError, RegisteredProject
from naiyue_test_agent.storage.project_databases import read_legacy
from .locations import check_legacy_marker

logger = logging.getLogger(__name__)


def import_legacy(service, paths):
    explicit = paths is not None
    candidates = (list(paths) if explicit else sorted((service.data_dir / 'workspaces').glob('*/sessions.sqlite')))
    results = []
    for raw in candidates:
        path = Path(raw).expanduser().absolute()
        try:
            registry = service.registry
            if not explicit and registry.was_removed(path):
                continue
            identity, root = read_legacy(path)
            if not explicit and path.parent.name != identity:
                raise ProjectError('project_identity_conflict')
            try:
                existing = registry.get(identity)
            except ProjectError as error:
                if error.code != 'project_not_found':
                    raise
                existing = None
            if existing is not None:
                if existing.database_path != path or not isinstance(existing.source, LocalSource):
                    raise ProjectError('project_identity_conflict')
                check_legacy_marker(existing.source.path, identity)
                if existing.lifecycle != 'active':
                    pending = next((op for op in registry.operations()
                        if op.project_id == identity and op.action == 'import_legacy' and op.stage != 'done'), None)
                    if pending is None:
                        raise ProjectError('project_operation_pending')
                    service._finish(pending)
                results.append({'database_path': path, 'project': service.get(identity), 'error': None})
                continue
            check_legacy_marker(root, identity)
            generation = sum(op.action == 'remove' and op.stage == 'done'
                and op.facts.get('database_path') == str(path) for op in registry.operations())
            operation_id = 'import:' + hashlib.sha256(str(path).encode()).hexdigest() + ':' + str(generation)
            request = {'database_path': str(path), 'project_id': identity, 'root': str(root)}
            project = RegisteredProject(identity, root.name or str(root), LocalSource(root), path)
            operation = registry.reserve(operation_id, 'import_legacy', request, project, new=True)
            project = service._finish(operation)
            results.append({'database_path': path, 'project': project, 'error': None})
        except ProjectError as error:
            logger.warning('project_import_failed code=%s', error.code)
            results.append({'database_path': path, 'project': None, 'error': error.code})
    return tuple(results)
