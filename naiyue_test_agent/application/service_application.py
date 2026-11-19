"""Service profile: root resources exist before any project, model or chat is opened."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import logging

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, await_settled, run_blocking
from naiyue_test_agent.config_paths import get_data_dir
from .core import Application
from .projects.cache_cleanup_operation import CacheCleanup
from .lifetime import ApplicationLifetime, _register_cleanup
from .memory.personal_memory import PersonalMemory
from .projects.host_manager import HostManager
from .projects.service import ProjectService
from .sessions.run_admission import RunAdmission

logger = logging.getLogger(__name__)


class ModelRefreshBusy(RuntimeError):
    pass


class LiveModelCatalog:
    """Stable handle used by open hosts when resolving the next model request."""
    def __init__(self, loader):
        self._loader = loader

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return getattr(self._loader.require(), name)

    async def aclose(self):
        await self._loader.aclose()


class DeferredModels:
    def __init__(self, config_path, network=None):
        self.config_path, self.value = config_path, None
        self.network = network
        self.catalog = LiveModelCatalog(self)
        self._lock = asyncio.Lock()

    async def get(self):
        async with self._lock:
            if self.value is None:
                self.value = await self._prepare()
            return self.value

    async def for_host(self):
        await self.get()
        return self.catalog

    async def refresh(self, settings, admission, *, background_busy=lambda: False, memory_available=True):
        from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback

        def validate(candidate):
            auto = settings.permissions.auto
            required = [role.model_id for role in settings.subagents]
            if memory_available and settings.memory.enabled:
                required.append(settings.memory.model_id)
            if auto is not None:
                required.extend((auto.fast_model_id, auto.detailed_model_id))
            for identity in filter(None, required):
                candidate.get(identity)
            install_model_usage_callback(candidate, http_attempt_limit=settings.diagnostics.max_pending_events)

        previous = None

        def check_idle():
            if admission.active_count() or background_busy():
                raise ModelRefreshBusy

        def publish(candidate):
            nonlocal previous
            validate(candidate)
            with admission.lock:
                check_idle()
                previous, self.value = self.value, candidate

        async with self._lock:
            with admission.lock:
                check_idle()
            current = await self._prepare(publish)
            if previous is not None and previous is not current:
                try:
                    await await_settled(previous.aclose())
                except Exception as error:
                    logger.warning('retired_model_cleanup_failed error_type=%s', type(error).__name__)

    async def _prepare(self, on_prepared=None):
        value = await acquire_blocking(application.load_model_catalog, lambda item: item.aclose(), self.config_path)
        try:
            options = {} if self.network is None else {'network': self.network}
            await run_blocking(value.prepare_async_clients, **options)
            if on_prepared is not None:
                on_prepared(value)
        except BaseException:
            try:
                await await_settled(value.aclose())
            except BaseException as error:
                logger.error('model_cleanup_secondary_failed error_type=%s', type(error).__name__)
            raise
        return value

    def require(self):
        if self.value is None:
            raise RuntimeError('Models have not been opened')
        return self.value

    async def aclose(self):
        if self.value is not None:
            await self.value.aclose()



@asynccontextmanager
async def open_service_application(config_path, *, tools, system_prompt, response_format,
                                   settings, initial_active_tools=None):
    stack = AsyncExitStack()
    tools = tuple(tools)
    models = DeferredModels(config_path, settings.model_network)
    owner = ApplicationLifetime(models.catalog, None, stack)
    personal = PersonalMemory(get_data_dir(settings.storage), settings.memory)
    async def close_personal(*args):
        await run_blocking(personal.close)
    _register_cleanup(stack, 'personal_memory', close_personal)
    projects = ProjectService(get_data_dir(settings.storage), settings.projects, storage_settings=settings.storage)
    async def close_projects(*args):
        await run_blocking(projects.close)
    _register_cleanup(stack, 'projects', close_projects)
    admission = RunAdmission(settings.max_active_runs)
    hosts = HostManager(owner, catalog=None, personal=personal,
        admission=admission, projects=projects, settings=settings,
        tools=tools, system_prompt=system_prompt, response_format=response_format,
        initial_active_tools=initial_active_tools, catalog_loader=models.for_host)
    app = Application(owner, models.catalog, projects, hosts)
    def background_busy():
        return any(child._sessions.memory_jobs is not None and child._sessions.memory_jobs.has_pending_models()
                   for child in owner._children)

    async def refresh_models():
        with owner.operation():
            await models.refresh(settings, admission, background_busy=background_busy,
                                 memory_available=any(spec.tool.name == 'memory' for spec in tools))
    app.refresh_models = refresh_models
    app.cache_cleanup = CacheCleanup(owner, hosts, settings.cache_cleanup)
    primary = None
    try:
        yield app
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await app.aclose()
        except BaseException as error:
            if primary is None:
                raise
            logger.error('application_cleanup_secondary_failed error_type=%s', type(error).__name__)
