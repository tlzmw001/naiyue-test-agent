"""Service profile: root resources exist before any project, model or chat is opened."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import logging

from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, await_settled, run_blocking
from naiyue_test_agent.config_paths import get_data_dir
from .core import Application
from .lifetime import ApplicationLifetime, _register_cleanup
from .memory.personal_memory import PersonalMemory
from .projects.host_manager import HostManager
from .projects.service import ProjectService
from .sessions.run_admission import RunAdmission

logger = logging.getLogger(__name__)


class DeferredModels:
    def __init__(self, config_path):
        self.config_path, self.value = config_path, None
        self._lock = asyncio.Lock()

    async def get(self):
        async with self._lock:
            if self.value is None:
                value = await acquire_blocking(application.load_model_catalog,
                    lambda item: item.aclose(), self.config_path)
                try:
                    await run_blocking(value.prepare_async_clients)
                except BaseException:
                    try:
                        await await_settled(value.aclose())
                    except BaseException as error:
                        logger.error('model_cleanup_secondary_failed error_type=%s', type(error).__name__)
                    raise
                self.value = value
            return self.value

    def _require(self):
        if self.value is None:
            raise RuntimeError('Models have not been opened')
        return self.value

    @property
    def default_model_id(self):
        return self._require().default_model_id

    @property
    def model_ids(self):
        return self._require().model_ids

    def image_capabilities(self):
        return self._require().image_capabilities()

    def thinking_options(self, identity):
        return self._require().thinking_options(identity)

    async def aclose(self):
        if self.value is not None:
            await self.value.aclose()


@asynccontextmanager
async def open_service_application(config_path, *, tools, system_prompt, response_format,
                                   settings, initial_active_tools=None):
    stack = AsyncExitStack()
    models = DeferredModels(config_path)
    owner = ApplicationLifetime(models, None, stack)
    personal = PersonalMemory(get_data_dir(settings.storage), settings.memory)
    async def close_personal(*args):
        await run_blocking(personal.close)
    _register_cleanup(stack, 'personal_memory', close_personal)
    projects = ProjectService(get_data_dir(settings.storage), settings.projects, storage_settings=settings.storage)
    async def close_projects(*args):
        await run_blocking(projects.close)
    _register_cleanup(stack, 'projects', close_projects)
    hosts = HostManager(owner, catalog=None, personal=personal,
        admission=RunAdmission(settings.max_active_runs), projects=projects, settings=settings,
        tools=tuple(tools), system_prompt=system_prompt, response_format=response_format,
        initial_active_tools=initial_active_tools, catalog_loader=models.get)
    app = Application(owner, models, projects, hosts)
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
