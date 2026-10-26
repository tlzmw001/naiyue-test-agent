"""Explicit project/chats host ownership; no current-project or cwd based routing."""
import asyncio
from dataclasses import dataclass
import os
import logging
from contextlib import asynccontextmanager,ExitStack
from naiyue_test_agent.application.errors import SessionBusyError

from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking
from naiyue_test_agent.contracts.projects import GitSource,LocalSource, ProjectError
from naiyue_test_agent.execution_roots import ExecutionRootUnavailable, ExecutionRoot
from naiyue_test_agent.application.lifetime import _register_cleanup
from naiyue_test_agent.application.lifetime import ScopeTasks
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.storage.project_store import ProjectStore
from .host_assembly import assemble_host

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class RegisteredExecution:
    registry: object
    project_id: str

    def resolve(self, project_id):
        if project_id != self.project_id:
            raise ExecutionRootUnavailable('wrong_project')
        try:
            facts = self.registry.execution_facts(project_id)
        except ProjectError as error:
            raise ExecutionRootUnavailable('not_registered') from error
        except (SessionPersistenceError, OSError) as error:
            logger.error('execution_registration_check_failed error_type=%s', type(error).__name__)
            raise ExecutionRootUnavailable('unreadable') from error
        if facts.source_kind != 'local' or facts.root is None:
            raise ExecutionRootUnavailable('not_registered')
        return ExecutionRoot(facts.root, authority=self, binding='project_local', project_revision=facts.revision)

    def validate(self, root, project_id):
        if project_id != self.project_id:
            raise ExecutionRootUnavailable('wrong_project')
        try:
            project = self.registry.execution_facts(self.project_id)
        except ProjectError as error:
            raise ExecutionRootUnavailable('not_registered') from error
        except (SessionPersistenceError, OSError) as error:
            logger.error('execution_registration_check_failed error_type=%s', type(error).__name__)
            raise ExecutionRootUnavailable('unreadable') from error
        if project.lifecycle != 'active' or not project.trusted or project.source_kind != 'local':
            raise ExecutionRootUnavailable('not_registered')
        if root.project_revision is not None and root.project_revision != project.revision:
            raise ExecutionRootUnavailable('not_registered')
        try:
            current = root.path.resolve()
        except OSError as error:
            logger.error('execution_registration_check_failed error_type=%s', type(error).__name__)
            raise ExecutionRootUnavailable('unreadable') from error
        if os.path.normcase(str(current)) != project.path_key:
            raise ExecutionRootUnavailable('wrong_project')
        return project


class ChatAPI:
    def __init__(self, manager):
        self._manager = manager

    async def open(self):
        with self._manager.owner.operation():
            return await self._manager.open(None)

    async def close(self):
        with self._manager.owner.operation():
            await self._manager.close(None)

    async def list_sessions(self):
        with self._manager.owner.operation():
            return await self._manager.list_sessions(None)


class TemporaryAPI:
    def __init__(self, manager):
        self._manager = manager

    async def open(self, identity):
        with self._manager.owner.operation():
            return await self._manager.open_temporary(identity)

    async def close(self, identity):
        await self._manager.close_temporary(identity)


class HostManager:
    def __init__(self, owner, *, catalog, personal, admission, projects, settings, tools,
                 system_prompt, response_format, initial_active_tools, catalog_loader=None):
        self.owner, self.projects, self.settings = owner, projects, settings
        self._hosts, self._temporary = {}, {}
        self._scopes = ScopeTasks(owner._opening_tasks)
        self._catalog_loader = catalog_loader
        self.chats = ChatAPI(self)
        self.temporary = TemporaryAPI(self)
        self._options = dict(catalog=catalog, parent=owner, personal=personal, admission=admission,
            tools=tools, system_prompt=system_prompt, response_format=response_format, settings=settings,
            initial_active_tools=initial_active_tools)
        _register_cleanup(owner._resources, 'temporary_storage', self.close_temporary_resources)

    async def list_sessions(self, project_id):
        async with self._scopes.serial(('host', project_id)):
            self.owner.check_open()
            project = await run_blocking(self.projects.get, project_id) if project_id is not None else None
            if project is not None and project.lifecycle != 'active':
                raise ProjectError('project_operation_pending')
            return await self._session_rows(project)

    async def _session_rows(self, project):
        """Caller owns this scope's lifecycle gate, never another project's gate."""
        project_id = project.project_id if project is not None else None
        path = project.database_path if project is not None else self.projects.data_dir / 'chats/sessions.sqlite'
        host = self._hosts.get(project_id)
        if host is not None:
            return await host.sessions.list()
        def read():
            try:
                if project is None or isinstance(project.source,GitSource):
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        return []
                with ProjectStore(path, require_existing=True, runtime_profile=project_id or 'chats',
                                  busy_timeout_ms=self.settings.storage.busy_timeout_ms) as store:
                    return store.sessions.list_sessions()
            except OSError as error:
                logger.error('project_directory_read_failed error_type=%s', type(error).__name__)
                raise ProjectError('project_history_unavailable') from error
        return await run_blocking(read)

    async def open(self, project_id, *, history_only=False):
        if self.projects is None:
            raise ProjectError('project_registry_unavailable')
        self.owner.check_open()
        try:
            return await self._scopes.open(('host', project_id),
                lambda: self._open_host(project_id, history_only=history_only))
        except asyncio.CancelledError:
            if self.owner._closing and not asyncio.current_task().cancelling():
                raise RuntimeError('Async application must be open on its owning event loop') from None
            raise

    async def _open_host(self, project_id, *, history_only):
        with self.owner.operation():
            self.owner.check_open()
            if project_id in self._hosts:
                return self._hosts[project_id]
            options = {}
            if project_id is None:
                path = self.projects.data_dir / 'chats/sessions.sqlite'
                options['chats'] = True
            else:
                project = await run_blocking(self.projects.get, project_id)
                if project.lifecycle != 'active':
                    raise ProjectError('project_operation_pending')
                if not project.trusted and not history_only:
                    raise ProjectError('project_trust_required')
                if not isinstance(project.source, LocalSource):
                    await run_blocking(self.projects.ensure_git_store,project_id)
                path = project.database_path
                authority = RegisteredExecution(self.projects.registry, project_id)
                options.update(registered_project=project, execution_authority=authority)
            host = await assemble_host(database_path=path, **options, **await self._host_options())
            if self.owner._closing:
                await await_settled(host._lifetime.aclose())
                raise RuntimeError('Async application must be open on its owning event loop')
            self._hosts[project_id] = host
            self.owner._children.append(host._lifetime)
            _register_cleanup(self.owner._resources, 'host_storage', host._lifetime._resources.__aexit__)
            return host

    def _is_open(self, project_id):
        return project_id in self._hosts

    @asynccontextmanager
    async def reserve_delivery(self,project_id):
        async with self._scopes.serial(('delivery',project_id)):
            with ExitStack() as guards:
                async with self._scopes.serial(('host',project_id)):
                    self.owner.check_open()
                    guards.enter_context(self._options['admission'].manage_project(project_id))
                yield

    @asynccontextmanager
    async def project_change(self,project_id):
        async with self._scopes.serial(('host',project_id)):
            self.owner.check_open()
            try:self._options['admission'].check_project(project_id)
            except SessionBusyError as error:raise ProjectError('project_in_use') from error
            yield

    async def _host_options(self):
        options = dict(self._options)
        options['environments']=getattr(self,'environments',None)
        if self._catalog_loader is not None:
            options['catalog'] = await self._catalog_loader()
        return options

    async def open_temporary(self, identity):
        self.owner.check_open()
        return await self._scopes.open(('temporary', identity), lambda: self._open_temporary(identity))

    async def _open_temporary(self, identity):
        with self.owner.operation():
            if identity not in self._temporary:
                host = await assemble_host(database_path=None, **await self._host_options())
                if self.owner._closing:
                    await await_settled(host._lifetime.aclose())
                    raise RuntimeError('Cannot publish a host while its owner is closing')
                self._temporary[identity] = host
                self.owner._children.append(host._lifetime)
            return self._temporary[identity]

    async def close_temporary(self, identity):
        key = ('temporary', identity)
        await self._scopes.cancel(key)
        async with self._scopes.serial(key):
            host = self._temporary.get(identity)
            if host is None:
                return
            try:
                await await_settled(host._lifetime.aclose())
            finally:
                if host._lifetime._closed and self._temporary.get(identity) is host:
                    self._temporary.pop(identity)
                    self.owner._children.remove(host._lifetime)

    async def close_temporary_resources(self, *args):
        # Root shutdown has already stopped runtimes; RAM resources are not
        # accumulated as one root-stack callback per temporary conversation.
        first = None
        for identity, host in tuple(self._temporary.items()):
            try:
                await await_settled(host._lifetime._resources.aclose())
            except BaseException as error:
                if first is None:
                    first = error
                else:
                    logger.error('temporary_storage_cleanup_secondary_failed error_type=%s', type(error).__name__)
            finally:
                self._temporary.pop(identity)
                self.owner._children.remove(host._lifetime)
        if first is not None:
            raise first

    async def remove(self, project_id, *, operation_id):
        async with self.project_change(project_id):
            self.owner.check_open()
            if self._is_open(project_id):
                raise ProjectError('project_in_use')
            if getattr(self,'environments',None) is not None:
                rows=await self.environments.list(project_id)
                if any(row.lifecycle!='destroyed' for row in rows):raise ProjectError('project_in_use')
            return await run_blocking(self.projects.remove, project_id, operation_id=operation_id)

    async def relocate(self, project_id, **request):
        from .locations import canonical
        async with self.project_change(project_id):
            self.owner.check_open()
            if self._is_open(project_id) and canonical(request['expected_root']) != canonical(request['root']):
                raise ProjectError('project_in_use')
            return await run_blocking(self.projects.relocate, project_id, **request)

    async def close(self, project_id):
        async with self.project_change(project_id):
            self.owner.check_open()
            host = self._hosts.get(project_id)
            if host is None:
                raise ProjectError('project_not_open')
            try:
                await await_settled(host._close())
            finally:
                if host._lifetime._closed:
                    del self._hosts[project_id]
                    self.owner._children.remove(host._lifetime)
