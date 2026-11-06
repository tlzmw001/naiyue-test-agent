"""Persist task MCP intent and confirm its effective catalog through Runner control."""
import logging
import time
from uuid import uuid4

from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_mcp import CloudMCPSection, CloudMCPSettings, RefreshMCP, tunnel_target
from naiyue_test_agent.contracts.cloud_networks import service_context
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.networks import read_network
from naiyue_test_agent.storage.cloud.releases import meta, put
from .work import cloud_work

logger = logging.getLogger(__name__)
CAPABILITY = 'mcp:refresh'


class CloudMCP:
    def __init__(self, runtime):
        self.runtime = runtime
        with runtime.database.transaction(write=True) as db:
            put(db, 'mcp-settings', runtime.settings.mcp.model_dump(mode='json'))

    def _require(self, task_id):
        task = self.runtime.registry.get_task(task_id)
        if task.lifecycle in ('deleting', 'deleted'):
            raise CloudError('cloud_task_unavailable', 409)
        return task

    def _status(self, task, row, report):
        connected = task.runner_connection_id
        if CAPABILITY not in task.runner_capabilities and connected:
            status = 'unsupported'
        elif not connected:
            status = 'waiting_runner'
        elif row and row.get('confirmed_connection') == connected:
            status = 'error' if any(item['error'] for item in report) else 'available'
        elif task.active_run_id and row:
            status = 'waiting_run'
        elif task.runner_phase != 'ready':
            status = 'waiting_runner'
        elif row is None:
            status = 'available'
        elif row.get('sent_connection') == connected:
            status = 'refreshing' if row['expires_at'] > time.time() else 'unconfirmed'
        else:
            status = 'refreshing'
        return dict(status=status, refresh_id=row['refresh_id'] if row else None, services=report)

    def _routes(self, db, task, settings):
        slots = {server.network_slot for server in settings.servers.values()
                 if server.enabled and server.route == 'windows_tunnel'}
        endpoints = self.runtime.settings.networks.get(task.network_id)
        if not slots or endpoints is None:
            return None, False
        network = read_network(db, task.network_id)
        if network['switching']:
            return None, True
        selected = {name: target for name, target in network['slots'].items()
                    if name in slots and name in endpoints.ports}
        return {'slots': service_context(network | {'slots': selected}, endpoints)['slots']}, False

    def _report(self, db, task, row):
        if row is None or not task.runner_connection_id or row.get('report_connection') != task.runner_connection_id:
            return []
        report = row['report']
        if row.get('confirmed_connection') != task.runner_connection_id or not row.get('targets'):
            return report
        settings = CloudMCPSettings.model_validate(row['settings'])
        routes, _ = self._routes(db, task, settings)
        checked = []
        for item in report:
            name = item['server_id']
            previous = row['targets'].get(name)
            current = tunnel_target(settings.servers[name], routes) if previous else None
            if previous and tuple(previous) != current:
                item = item | dict(status='error', error='mcp_tunnel_target_unavailable', tools=[])
            checked.append(item)
        return checked

    def status(self, task_id):
        task = self._require(task_id)
        with self.runtime.database.transaction() as db:
            row = meta(db, 'mcp-task:' + task_id)
            return self._status(task, row, self._report(db, task, row))

    def request(self, task_id, request):
        r = self.runtime
        task = self._require(task_id)
        if task.runner_connection_id and CAPABILITY not in task.runner_capabilities:
            raise CloudError('cloud_mcp_unsupported', 409)
        with r.database.transaction() as db:
            row = meta(db, 'mcp-task:' + task_id)
            if row and row['refresh_id'] == request.refresh_id:
                return
        settings = (read_settings(r.settings_path, CloudMCPSection).mcp
                    if r.settings_path is not None else r.settings.mcp)
        with r.gates.hold(task_id):
            task = self._require(task_id)
            if task.runner_connection_id and CAPABILITY not in task.runner_capabilities:
                raise CloudError('cloud_mcp_unsupported', 409)
            key = 'mcp-task:' + task_id
            with r.database.transaction(write=True) as db:
                row = meta(db, key) or {}
                if request.refresh_id == row.get('refresh_id'):
                    return
                previous = row.get('previous', [])
                if request.refresh_id in previous:
                    raise CloudError('cloud_mcp_refresh_superseded', 409)
                if row.get('expires_at', 0) > time.time() and not row.get('confirmed_connection'):
                    raise CloudError('cloud_mcp_refresh_in_progress', 409)
                if row.get('refresh_id'):
                    previous = [*previous[-31:], row['refresh_id']]
                value = settings.model_dump(mode='json')
                put(db, 'mcp-settings', value)
                put(db, key, dict(refresh_id=request.refresh_id, settings=value, previous=previous,
                    report=row.get('report', []), report_connection=row.get('report_connection')))

    async def refresh(self, task_id, value):
        request = RefreshMCP.model_validate(value)
        await cloud_work(self.runtime, lambda: self.request(task_id, request))
        connection = self.runtime.control.connections.get(task_id)
        if connection is not None:
            await self.advance(connection)
        return await cloud_work(self.runtime, lambda: self.status(task_id))

    def prepare(self, connection):
        r = self.runtime
        task_id = connection.task_id
        key = 'mcp-task:' + task_id
        with r.gates.hold(task_id):
            task = r.control.verify(connection)
            if (CAPABILITY not in task.runner_capabilities or task.lifecycle != 'ready'
                    or task.runner_phase != 'ready' or task.active_run_id or task.control_operation_id
                    or task.environment_operation_id or task.artifact_operation_id):
                return None
            with r.database.transaction() as db:
                row = meta(db, key)
                if row is None:
                    settings = meta(db, 'mcp-settings')
                    if not settings['servers']:
                        return None
                    row = dict(refresh_id=str(uuid4()), settings=settings)
            if row.get('sent_connection') == connection.connection_id:
                return None
            if row.get('expires_at', 0) > time.time() and not row.get('confirmed_connection'):
                return None  # A replaced connection may still be closing its discovery.
            settings = CloudMCPSettings.model_validate(row['settings'])
            with r.database.transaction(write=True) as db:
                services, switching = self._routes(db, task, settings)
                if switching:
                    return None
                targets = {name: tunnel_target(server, services) for name, server in settings.servers.items()
                           if server.enabled and server.route == 'windows_tunnel'}
                longest = max((server.connect_timeout_seconds for server in settings.servers.values() if server.enabled), default=0)
                expires = time.time() + max(r.settings.control_receive_timeout_seconds,
                    longest + r.settings.control_io_timeout_seconds)
                profile = digest({'settings': row['settings'], 'services': services})
                put(db, key, row | dict(sent_connection=connection.connection_id, digest=profile, targets=targets,
                    expires_at=expires, confirmed_connection=None, network_id=task.network_id if any(targets.values()) else None))
                return dict(kind='mcp_refresh', refresh_id=row['refresh_id'], digest=profile,
                    settings=row['settings'], services=services, expires_at=expires)

    async def advance(self, connection):
        if not connection.mcp_supported:
            return
        try:
            frame = await cloud_work(self.runtime, lambda: self.prepare(connection))
            if frame is not None:
                await connection.send(frame)
        except (CloudError, OSError, RuntimeError, TimeoutError) as error:
            logger.warning('cloud_mcp_dispatch_unconfirmed error_type=%s', type(error).__name__)

    def confirm(self, connection, frame):
        r = self.runtime
        key = 'mcp-task:' + connection.task_id
        with r.gates.hold(connection.task_id):
            task = r.control.verify(connection, frame)
            if task.lifecycle in ('deleting', 'deleted') or task.active_run_id:
                return
            with r.database.transaction(write=True) as db:
                row = meta(db, key)
                if (row is None or row.get('sent_connection') != connection.connection_id
                        or row['refresh_id'] != frame.refresh_id or row['digest'] != frame.digest
                        or row.get('confirmed_connection') or row['expires_at'] <= time.time()):
                    return
                expected = {key: value['route'] for key, value in row['settings']['servers'].items()}
                if expected != {item.server_id: item.route for item in frame.services} or len(expected) != len(frame.services):
                    raise CloudError('cloud_mcp_report_invalid', 422)
                put(db, key, row | dict(confirmed_connection=connection.connection_id,
                    report_connection=connection.connection_id, report=[item.model_dump() for item in frame.services]))

    def run_revision(self, task):
        with self.runtime.database.transaction() as db:
            row = meta(db, 'mcp-task:' + task.task_id)
        if row is None:
            return None
        if task.runner_connection_id and row.get('confirmed_connection') == task.runner_connection_id:
            return row['refresh_id']
        if row.get('sent_connection') != task.runner_connection_id or row['expires_at'] > time.time():
            raise CloudError('cloud_mcp_refresh_in_progress', 409)
        return None
