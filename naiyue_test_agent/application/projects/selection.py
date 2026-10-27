"""Select project/session metadata before creating a model-backed execution host."""
import os
from contextlib import AsyncExitStack
from pathlib import Path

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.projects import LocalSource, ProjectError,GitSource
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.workspaces import locate_workspace, prepare_workspace


class ProjectSelection:
    def __init__(self, projects):
        self.projects = projects
        self.hosts = projects._hosts

    async def prepare(self, *, root=None, project_id=None, database_path=None,
                      new_workspace=False, resume=False, trusted=False, operation_id):
        with self.projects._owner.operation():
            root = await run_blocking(lambda: Path(root).expanduser().resolve()) if root is not None else None
            path = Path(database_path).expanduser().absolute() if database_path is not None else None
            rows = None
            async with AsyncExitStack() as gates:
                if root is not None:
                    await gates.enter_async_context(self.hosts._scopes.serial(('selection_root', os.path.normcase(str(root)))))
                legacy_path = path
                if legacy_path is None and root is not None and project_id is None:
                    marker_exists = await run_blocking(lambda: (root / '.naiyue/workspace.json').exists())
                    if marker_exists:
                        if not new_workspace:
                            rows = await run_blocking(self.projects._require().list)
                        registered = any(isinstance(p.source, LocalSource) and p.source.path == root for p in rows or ())
                        if not registered:
                            location = await run_blocking(locate_workspace, root, data_dir=self.projects._require().data_dir)
                            legacy_path = location.database_path if location is not None else None
                if legacy_path is not None:
                    key = await run_blocking(lambda: os.path.normcase(str(legacy_path.resolve())))
                    await gates.enter_async_context(self.hosts._scopes.serial(('selection_store', key)))
                    # A concurrent path-based import may have registered it while we waited.
                    rows = None
                return await self._prepare(root=root, project_id=project_id, database_path=path,
                    new_workspace=new_workspace, resume=resume, trusted=trusted, operation_id=operation_id, rows=rows)

    async def _prepare(self, *, root, project_id, database_path, new_workspace, resume, trusted, operation_id, rows=None):
        service = self.projects._require()
        path = database_path
        if resume and path is not None and not os.path.lexists(path):
            return {'status': 'empty', 'sessions': []}
        # Avoid creating the registry merely to report an empty history list.
        if resume and path is None and project_id is None and not (service.data_dir / 'projects.sqlite').exists():
            marker = root / '.naiyue/workspace.json' if root is not None else None
            if marker is None or not marker.exists():
                return {'status': 'empty', 'sessions': []}
        rows = rows if rows is not None else await run_blocking(service.list)
        if project_id is not None:
            project = await run_blocking(service.get, project_id)
            if path is not None or root is not None and (
                    not isinstance(project.source, LocalSource) or project.source.path != root):
                raise ProjectError('project_location_changed')
        else:
            project = next((p for p in rows if p.database_path.resolve() == path.resolve()), None) if path else None
            if project is None and path is None and not new_workspace:
                project = next((p for p in rows if isinstance(p.source, LocalSource) and p.source.path == root), None)
            if project is None:
                if root is None and path is None:
                    raise ProjectError('project_location_unavailable')
                legacy = path is not None or new_workspace or root is not None and (root / '.naiyue/workspace.json').exists()
                if legacy:
                    if not resume and not trusted:
                        return {'status': 'trust_required', 'root': str(root), 'sessions': []}
                    if path is None or not os.path.lexists(path):
                        location = await run_blocking(prepare_workspace, root, data_dir=service.data_dir,
                            database_path=path, new_workspace=new_workspace, create=not resume)
                        if location is None or resume and not location.database_path.exists():
                            return {'status': 'empty', 'sessions': []}
                        path = location.database_path
                        if not resume:
                            def bind():
                                with ProjectStore(path, busy_timeout_ms=service.storage_settings.busy_timeout_ms) as store:
                                    store.sessions.bind_workspace(location.workspace_id, location.root)
                            await run_blocking(bind)
                    imported = await run_blocking(service.import_legacy, [path])
                    if imported[0]['error']:
                        raise ProjectError(imported[0]['error'])
                    project = imported[0]['project']
                else:
                    if resume:
                        return {'status': 'empty', 'sessions': []}
                    if not trusted:
                        return {'status': 'trust_required', 'root': str(root), 'sessions': []}
                    project = await run_blocking(service.add_local, root, trusted=True, operation_id=operation_id)
        async with self.hosts._scopes.serial(('host', project.project_id)):
            project = await run_blocking(service.get, project.project_id)
            if isinstance(project.source,GitSource):
                if not resume and not project.trusted:
                    if not trusted:return {'status':'trust_required','root':None,'sessions':[],'source_kind':'git'}
                    project=await run_blocking(service.trust_git,project.project_id,
                        expected_revision=project.revision,operation_id=operation_id+':trust')
                sessions=await self.hosts._session_rows(project)
                return {'status':'ready','scope_id':project.project_id,'root':None,'sessions':sessions,'source_kind':'git'}
            if root is not None and root != project.source.path:
                raise ProjectError('project_location_changed')
            if not project.trusted and project.availability == 'available':
                if not trusted:
                    return {'status': 'trust_required', 'root': str(project.source.path), 'sessions': []}
                project = await run_blocking(service.relocate, project.project_id,
                    expected_root=project.source.path, root=project.source.path, trusted=True,
                    operation_id=operation_id + ':trust')
            sessions = await self.hosts._session_rows(project)
            return {'status': 'ready', 'scope_id': project.project_id, 'root': str(project.source.path),
                    'sessions': sessions}
