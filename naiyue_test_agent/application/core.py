"""One application owns resources; callers explicitly select project/chat hosts."""
from .catalogs import ModelAPI
from .projects.api import ProjectAPI
from .environments.api import EnvironmentAPI


class Application:
    def __init__(self, owner, catalog, projects, hosts):
        self._lifetime = owner
        self.projects = ProjectAPI(owner, projects, hosts=hosts)
        self.chats = hosts.chats
        self.temporary = hosts.temporary
        self.models = ModelAPI(owner, catalog)
        self.environments = EnvironmentAPI(owner, hosts)
        hosts.environments=self.environments

    async def aclose(self):
        await self._lifetime.aclose()
