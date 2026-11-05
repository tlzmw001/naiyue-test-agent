"""Pinned OpenSandbox SDK adapter; explicit transport ownership and no create retries."""
from datetime import timedelta
import json
import logging
import shlex
from urllib.parse import urlsplit

import httpx

from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure, SandboxFacts, ContainerCheck
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger = logging.getLogger(__name__)


class _SafeSdkLog(logging.Handler):
    def emit(self, record):
        logger.log(record.levelno, 'cloud_sdk_event component=%s level=%s', record.name, record.levelname)


class OpenSandboxBackend:
    def __init__(self, settings, config, manager, transport):
        self.settings, self.config, self.manager = settings, config, manager
        self.backend_id, self.transport = settings.backend_id, transport

    @classmethod
    async def open(cls, settings, *, transport=None):
        try:
            from opensandbox import SandboxManager
            from opensandbox.config import ConnectionConfig
        except ImportError:
            raise BackendFailure('cloud_sdk_not_installed') from None
        # SDK warnings include upstream bodies. Preserve finite SDK events only;
        # adapter failures below retain operation/type/status without raw text.
        sdk_log = logging.getLogger('opensandbox')
        sdk_log.handlers = [_SafeSdkLog()]
        sdk_log.propagate = False
        url = urlsplit(settings.endpoint)
        transport = transport if transport is not None else httpx.AsyncHTTPTransport(retries=0)
        config = ConnectionConfig(domain=url.netloc, protocol=url.scheme,
            api_key=settings.api_key.get_secret_value(), transport=transport,
            request_timeout=timedelta(seconds=settings.request_timeout_seconds),
            follow_redirects=False, debug=False)
        config.retry_policy = config.retry_policy.disabled()
        try:
            manager = await SandboxManager.create(config)
        except BaseException:
            await transport.aclose()
            raise
        return cls(settings, config, manager, transport)

    @staticmethod
    def facts(info):
        return SandboxFacts(sandbox_id=info.id, image_id=info.image.image if info.image else '',
            metadata=info.metadata or {}, state=info.status.state.lower(),
            expires_at=info.expires_at.timestamp() if info.expires_at else None)

    async def _request(self, awaitable):
        from opensandbox.exceptions import SandboxException
        try:
            return await awaitable
        except (SandboxException, httpx.HTTPError) as error:
            logger.warning('cloud_backend_request_failed error_type=%s status=%s',
                type(error).__name__, getattr(error, 'status_code', None))
            raise BackendFailure('backend_unavailable') from None

    async def create(self, operation, mounts, resources):
        from opensandbox import Sandbox
        from opensandbox.models.sandboxes import Volume, Host
        from opensandbox.exceptions import SandboxException
        initial_status = None
        async def observe(response):
            nonlocal initial_status
            if response.request.method == 'POST' and response.request.url.path.rstrip('/').endswith('/sandboxes'):
                initial_status = response.status_code
        # Per-create SDK hook only records its initial POST status. A later GET
        # rejection cannot establish that the already-created container is absent.
        config = self.config.model_copy(update={'event_hooks': {'response': [observe]}})
        try:
            sandbox = await Sandbox.create(operation.template.image_id, connection_config=config,
                timeout=None, ready_timeout=timedelta(seconds=self.settings.ready_timeout_seconds),
                metadata=operation.metadata, resource=resources,
                entrypoint=['/opt/nyt/runner/bin/python','-I','/opt/nyt/runner-entry.py'],
                env={'PATH': '/opt/nyt/test/bin:/usr/local/bin:/usr/bin:/bin', 'PYTHONNOUSERSITE': '1'},
                volumes=[Volume(name=m.destination.strip('/').replace('/','-'), host=Host(path=m.source),
                    mount_path=m.destination, read_only=m.read_only) for m in mounts])
            return self.facts(await sandbox.get_info())
        except (SandboxException, httpx.HTTPError) as error:
            rejected = initial_status in (400, 401, 403, 422)
            logger.warning('cloud_backend_create_failed rejected=%s error_type=%s', rejected, type(error).__name__)
            raise BackendFailure('backend_create_rejected' if rejected else 'backend_unavailable', rejected=rejected) from None

    async def get(self, sandbox_id):
        from opensandbox.exceptions import SandboxApiException, SandboxException
        try:
            return self.facts(await self.manager.get_sandbox_info(sandbox_id))
        except SandboxApiException as error:
            if error.status_code == 404: return None
            logger.warning('cloud_backend_get_failed status=%s', error.status_code)
            raise BackendFailure('backend_unavailable') from None
        except (SandboxException, httpx.HTTPError) as error:
            logger.warning('cloud_backend_get_failed error_type=%s', type(error).__name__)
            raise BackendFailure('backend_unavailable') from None

    async def find(self, metadata):
        from opensandbox.models.sandboxes import SandboxFilter
        result = []
        for page in range(1, self.settings.max_pages + 1):
            response = await self._request(self.manager.list_sandbox_infos(SandboxFilter(
                metadata=metadata, page=page, page_size=self.settings.page_size)))
            result.extend(self.facts(info) for info in response.sandbox_infos)
            if not response.pagination.has_next_page: return result
        raise BackendFailure('backend_pagination_limit')

    async def kill(self, sandbox_id):
        from opensandbox.exceptions import SandboxApiException, SandboxException
        try:
            await self.manager.kill_sandbox(sandbox_id)
        except SandboxApiException as error:
            if error.status_code != 404:
                logger.warning('cloud_backend_kill_failed status=%s', error.status_code)
                raise BackendFailure('backend_unavailable') from None
        except (SandboxException, httpx.HTTPError) as error:
            logger.warning('cloud_backend_kill_failed error_type=%s', type(error).__name__)
            raise BackendFailure('backend_unavailable') from None

    async def preflight(self, sandbox_id, template):
        from opensandbox import Sandbox
        from opensandbox.models.execd import RunCommandOpts
        sandbox = await self._request(Sandbox.connect(sandbox_id, connection_config=self.config,
            connect_timeout=timedelta(seconds=self.settings.ready_timeout_seconds)))
        result = await self._request(sandbox.commands.run(shlex.join(['/opt/nyt/runner/bin/python', '-I',
            '-m', 'naiyue_test_agent.cloud_runner.preflight', '--quick']),
            opts=RunCommandOpts(timeout=timedelta(seconds=self.settings.request_timeout_seconds))))
        text = ''.join(line.text for line in result.logs.stdout)
        if result.error is not None or result.exit_code != 0 or len(text) > 65536:
            raise BackendFailure('container_preflight_failed')
        try:
            ContainerCheck.model_validate_json(text).verify(uid=template.uid, gid=template.gid, kit_version=template.kit_version)
        except (ValueError, KeyError, TypeError, CloudError):
            raise BackendFailure('container_preflight_failed') from None

    async def close(self):
        # SDK.close swallows transport failures; this externally-owned transport
        # is closed directly so a failed close remains observable to the owner.
        await self.transport.aclose()

    async def install(self, sandbox_id, input_id, limits):
        from opensandbox import Sandbox
        from opensandbox.models.execd import RunCommandOpts
        sandbox = await self._request(Sandbox.connect(sandbox_id, connection_config=self.config,
            connect_timeout=timedelta(seconds=self.settings.ready_timeout_seconds)))
        result = await self._request(sandbox.commands.run(shlex.join(['/opt/nyt/runner/bin/python', '-I',
            '-m', 'naiyue_test_agent.cloud_runner.input_install', *(['--empty'] if input_id is None else ['--input-id',input_id]),
            '--limits-json', limits.model_dump_json()]), opts=RunCommandOpts(
                timeout=timedelta(seconds=self.settings.request_timeout_seconds))))
        raw = ''.join(line.text for line in result.logs.stdout)
        try:
            if len(raw)>4096 or result.error is not None or result.exit_code!=0:
                raise ValueError('installation_failed')
            if json.loads(raw)!={'input_id':input_id,'status':'installed'}:
                raise ValueError('installation_receipt_mismatch')
        except (ValueError,TypeError):
            raise BackendFailure('input_install_failed') from None
