"""Application-managed connections, isolated by logical session and agent instance."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from itertools import count
import logging
import os
import secrets

from ..agent_runtime.async_utils import await_settled
from .owner import ConnectionOwner, MCPLease, MCPResourceError
from .settings import MCPSettings
from ..tools.web_settings import NetworkSettings


logger = logging.getLogger(__name__)


class MCPSessionResources:
    def __init__(self, settings: MCPSettings, network: NetworkSettings, *, output_settings=None):
        self._loop = asyncio.get_running_loop()
        settings = MCPSettings.model_validate(settings.model_dump())
        network = NetworkSettings.model_validate(network.model_dump())
        from ..tools.settings import ToolOutputSettings
        self._salt = secrets.token_bytes(32)
        self._network = network
        self._output_settings = output_settings or ToolOutputSettings()
        self._definitions = {name: server.model_copy(deep=True) for name, server in settings.servers.items()}
        self._catalogs = {}
        self._authorization_ids = {}
        self._refresh_lock = asyncio.Lock()
        self._candidates = set()
        self._last_refresh = {}
        self._configs = {}
        self._slots = {}
        for server_id, server in settings.servers.items():
            if not server.enabled:
                continue
            environment, identity = self._configuration(server_id, server)
            self._configs[server_id] = (server, network, environment, identity)
            self._authorization_ids[server_id] = identity
            self._slots[server_id] = asyncio.Semaphore(server.max_concurrency)
        self._owners = {}
        self._requests = {}
        self._shutdown_cancellations = {}
        self._settling = set()
        self._releasing = set()
        self._close_task = None
        self._closing = False
        self._cleanup_failures = []
        self._clock = self._loop.time
        self._idle_handles = {}
        self._idle_tasks = {}
        self._idle_claims = {}
        self._active_sessions = set()
        self._activity_sequence = count()
        self._activity_revisions = {}

    def set_session_occupied(self, session_id, occupied):
        """Run admission is immediate; worker-thread finish retains notification order."""
        if self._closing:
            return
        revision = next(self._activity_sequence)
        try:
            same_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            self._apply_session_occupied(session_id, occupied, revision)
        else:
            self._loop.call_soon_threadsafe(self._apply_session_occupied, session_id, occupied, revision)

    def _apply_session_occupied(self, session_id, occupied, revision):
        if self._closing or revision <= self._activity_revisions.get(session_id, -1):
            return
        self._activity_revisions[session_id] = revision
        if occupied:
            self._active_sessions.add(session_id)
        else:
            self._active_sessions.discard(session_id)
        for key, owner in tuple(self._owners.items()):
            if key[0] == session_id:
                self._cancel_idle(key, owner)
                if not occupied:
                    self._arm_idle(key, owner)

    def _cancel_idle(self, key, owner):
        item = self._idle_handles.get(key)
        if item is not None and item[0] is owner:
            self._idle_handles.pop(key)
            item[1].cancel()

    def _arm_idle(self, key, owner, deadline=None):
        if (self._closing or self._owners.get(key) is not owner or owner.invalid or owner.task.done()
                or not owner.ready.done() or not owner.settings.idle_timeout_seconds
                or key[0] in self._active_sessions or self._idle_claims.get(key)
                or owner.users or owner.waiters
                or any(key[:len(prefix)] == prefix for prefix in self._releasing)):
            return
        self._cancel_idle(key, owner)
        deadline = self._clock() + owner.settings.idle_timeout_seconds if deadline is None else deadline
        handle = self._loop.call_later(max(0, deadline - self._clock()), self._expire_idle, key, owner, deadline)
        self._idle_handles[key] = (owner, handle, deadline)

    def _expire_idle(self, key, owner, deadline):
        item = self._idle_handles.get(key)
        if item is None or item[0] is not owner or item[2] != deadline:
            return
        self._cancel_idle(key, owner)
        if self._clock() < deadline:
            self._arm_idle(key, owner, deadline)
            return
        if (self._closing or self._owners.get(key) is not owner or owner.invalid
                or key[0] in self._active_sessions or self._idle_claims.get(key)
                or owner.users or owner.waiters):
            return
        owner.retiring = owner.invalid = True
        task = asyncio.create_task(self._close_idle(key, owner), name='mcp-idle-close')
        self._idle_tasks[task] = key
        task.add_done_callback(lambda done: self._idle_tasks.pop(done, None))

    async def _close_idle(self, key, owner):
        try:
            await await_settled(self._retire(key, owner))
        except Exception as error:
            # _retire retains the necessary failure for borrowers and final close.
            logger.error('mcp_idle_cleanup_failed error_type=%s', type(error).__name__)

    def _configuration(self, server_id, server):
        references = set(server.headers_env.values()) | {ref[2:-1] for ref in server.env.values()}
        environment = {name: os.environ.get(name) for name in references}
        connection = server.model_dump(exclude={'enabled', 'allowed_tools'})
        content = json.dumps([server_id, connection, self._network.model_dump(), environment],
                             sort_keys=True, separators=(',', ':')).encode()
        return environment, hmac.new(self._salt, content, hashlib.sha256).hexdigest()

    def tool_unavailable(self, server_id, remote_tool, identity, fingerprint):
        config = self._configs.get(server_id)
        if config is None:
            return 'server_disabled'
        if remote_tool not in config[0].allowed_tools:
            return 'tool_disabled'
        catalog = self._catalogs.get(server_id)
        if self._authorization_ids.get(server_id) != identity or catalog is None or catalog.fingerprint != fingerprint:
            return 'definition_changed'
        return None

    def server_status(self):
        result = {}
        for name, server in self._definitions.items():
            enabled = name in self._configs
            identity = self._configs[name][3] if enabled else None
            result[name] = {'server_id': name, 'status': 'enabled' if enabled else 'disabled',
                'enabled': enabled, 'selected_tools': list(server.allowed_tools) if enabled else [],
                'draining': any(owner.invalid and (owner.users or owner.waiters) for key, owner in self._owners.items()
                                if owner.server_id == name and (not enabled or key[2] != identity))}
        result.update(self._last_refresh)
        return {'servers': list(result.values())}

    def _check_open(self, scope=None):
        if asyncio.get_running_loop() is not self._loop:
            raise MCPResourceError('mcp_wrong_event_loop')
        if self._closing:
            raise MCPResourceError('mcp_resources_closed')
        if scope and any(scope[:len(prefix)] == prefix for prefix in self._releasing):
            raise MCPResourceError('mcp_scope_closing')

    def _check_configuration(self, server_id, identity):
        config = self._configs.get(server_id)
        if config is None:
            raise MCPResourceError('mcp_server_not_enabled')
        if config[3] != identity:
            raise MCPResourceError('mcp_configuration_changed')

    async def discover(self):
        """Freeze registration facts using temporary, fully owned connections."""
        self._check_open()
        from uuid import uuid4
        scope = uuid4().hex
        snapshots = []
        primary = None
        try:
            for server_id, (server, _network, _environment, _identity) in self._configs.items():
                async with self.borrow(scope, 'root', server_id) as lease:
                    snapshots.append((server.model_copy(deep=True), lease.config_identity, lease.catalog))
            self._catalogs.update({catalog.server_id: catalog for _, _, catalog in snapshots})
            return tuple(snapshots)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await self.release_session(scope)
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('mcp_discovery_cleanup_failed error_type=%s', type(error).__name__)

    async def _retire(self, key, owner, *, primary=None, wait=False):
        self._cancel_idle(key, owner)
        if owner.users or owner.waiters:
            if not wait:
                return
            if not owner.retiring:
                raise MCPResourceError('mcp_connection_invalid')
            await asyncio.shield(owner.task)
        try:
            await owner.close()
        except Exception as error:
            logger.error('mcp_cleanup_failed error_type=%s', type(error).__name__)
            if not any(error is known for _, known in self._cleanup_failures):
                self._cleanup_failures.append((key, error))
            if primary is None:
                raise
        finally:
            if self._owners.get(key) is owner:
                self._owners.pop(key)

    @asynccontextmanager
    async def borrow(self, session_id, agent_path, server_id, *, control=None):
        scope = (session_id, agent_path)
        self._check_open(scope)
        if not all(isinstance(value, str) and value for value in scope):
            raise MCPResourceError('mcp_invalid_scope')
        if server_id not in self._configs:
            raise MCPResourceError('mcp_server_not_enabled')
        task = asyncio.current_task()
        if task in self._requests:
            raise MCPResourceError('mcp_nested_borrow')
        self._requests[task] = scope
        stopped = False
        monitor = None
        owner = None
        acquired = False
        primary = None
        server, network, environment, identity = self._configs[server_id]
        slot = self._slots[server_id]
        key = (*scope, identity)
        self._idle_claims[key] = self._idle_claims.get(key, 0) + 1
        existing = self._owners.get(key)
        if existing is not None:
            self._cancel_idle(key, existing)

        async def watch_stop():
            nonlocal stopped
            while True:
                if control.requested:
                    stopped = True
                    task.cancel()
                    return
                await asyncio.sleep(.05)

        try:
            if control is not None:
                from ..tools.control import check_tool_stop
                check_tool_stop(control, 'MCP request stopped before execution.')
                monitor = asyncio.create_task(watch_stop())
            await slot.acquire()
            acquired = True
            self._check_open(scope)
            self._check_configuration(server_id, identity)
            for old_key, old_owner in tuple(self._owners.items()):
                if old_key[:2] == scope and old_key != key and old_owner.server_id == server_id and old_owner.invalid:
                    await self._retire(old_key, old_owner, wait=True)
            owner = self._owners.get(key)
            while owner is not None and (owner.invalid or owner.task.done()):
                await self._retire(key, owner, wait=True)
                self._check_open(scope)
                owner = self._owners.get(key)
            self._check_configuration(server_id, identity)
            if any(failed_key == key for failed_key, _ in self._cleanup_failures):
                raise MCPResourceError('mcp_previous_cleanup_failed')
            if owner is None:
                owner = ConnectionOwner(server_id, server, network, environment, identity)
                self._owners[key] = owner
            owner.waiters.add(task)
            try:
                lease = await asyncio.shield(owner.ready)
            finally:
                owner.waiters.discard(task)
            if owner.invalid:
                raise MCPResourceError('mcp_connection_invalid')
            self._check_configuration(server_id, identity)
            owner.users.add(task)
            # The RPC runs in the borrowing task, distinct from the SDK owner.
            from .connections import _safe_library_logs
            with _safe_library_logs():
                if server_id in self._catalogs:
                    lease = MCPLease(lease.client, self._catalogs[server_id], self._authorization_ids[server_id])
                yield lease
        except BaseException as error:
            primary = error
            if owner is not None:
                if task in owner.users:
                    owner.invalid = True
                    owner.observed.append(error)
                elif not owner.users and not owner.waiters:
                    owner.invalid = True
            if isinstance(error, asyncio.CancelledError):
                owned = int(stopped)
                if control is not None and control.requested:
                    owned += self._shutdown_cancellations.get(task, 0)
                for _ in range(owned):
                    task.uncancel()
                if owned and task.cancelling() == 0:
                    from langchain_core.tools import ToolException
                    raise ToolException('MCP request stopped; remote side effects may already have occurred.') from None
            raise
        finally:
            self._settling.add(task)
            async def finish():
                try:
                    if monitor is not None:
                        monitor.cancel()
                        await asyncio.gather(monitor, return_exceptions=True)
                    if owner is not None:
                        owner.users.discard(task)
                        if owner.invalid:
                            await self._retire(key, owner, primary=primary)
                finally:
                    if acquired:
                        slot.release()
                    self._requests.pop(task, None)
                    self._shutdown_cancellations.pop(task, None)
                    self._settling.discard(task)
                    remaining = self._idle_claims[key] - 1
                    if remaining:
                        self._idle_claims[key] = remaining
                    else:
                        self._idle_claims.pop(key)
                        current = self._owners.get(key)
                        if current is not None:
                            self._arm_idle(key, current)
            await await_settled(finish())

    async def _release(self, prefix):
        current = asyncio.current_task()
        targets = [task for task, scope in self._requests.items() if scope[:len(prefix)] == prefix]
        if current in targets:
            raise MCPResourceError('mcp_cannot_close_own_borrow')
        self._releasing.add(prefix)
        for key, (owner, _, _) in tuple(self._idle_handles.items()):
            if key[:len(prefix)] == prefix:
                self._cancel_idle(key, owner)
        try:
            if not prefix:
                for owner in tuple(self._candidates):
                    await self._retire(('_refresh', owner.server_id, owner.identity), owner)
            for task in targets:
                # A settling borrower already has a primary result/cancellation.
                # Wait for it without overwriting that outcome during cleanup.
                if task not in self._settling and task.cancel():
                    self._shutdown_cancellations[task] = self._shutdown_cancellations.get(task, 0) + 1
            await asyncio.gather(*targets, return_exceptions=True)
            idle_tasks = [task for task, key in tuple(self._idle_tasks.items()) if key[:len(prefix)] == prefix]
            await asyncio.gather(*idle_tasks)
            for key, owner in tuple(self._owners.items()):
                if key[:len(prefix)] == prefix:
                    try:
                        await self._retire(key, owner)
                    except Exception:
                        # _retire records every distinct failure for final close.
                        continue
        finally:
            self._releasing.discard(prefix)
            if len(prefix) <= 1:
                for session_id in tuple(self._activity_revisions):
                    if not prefix or session_id == prefix[0]:
                        self._active_sessions.discard(session_id)
                        self._activity_revisions.pop(session_id, None)
        failures = [error for scope, error in self._cleanup_failures if scope[:len(prefix)] == prefix]
        if failures:
            raise ExceptionGroup('MCP resource cleanup failed', failures)

    async def release_instance(self, session_id, agent_path):
        if self._closing:
            return await self.aclose()
        self._check_open()
        if self._requests.get(asyncio.current_task()) == (session_id, agent_path):
            raise MCPResourceError('mcp_cannot_close_own_borrow')
        await await_settled(self._release((session_id, agent_path)))

    async def release_session(self, session_id):
        if self._closing:
            return await self.aclose()
        self._check_open()
        if (self._requests.get(asyncio.current_task()) or (None,))[0] == session_id:
            raise MCPResourceError('mcp_cannot_close_own_borrow')
        await await_settled(self._release((session_id,)))

    async def aclose(self):
        if asyncio.get_running_loop() is not self._loop:
            raise MCPResourceError('mcp_wrong_event_loop')
        if asyncio.current_task() in self._requests:
            raise MCPResourceError('mcp_cannot_close_own_borrow')
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._release(()))
        await await_settled(self._close_task)
