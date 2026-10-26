"""Typed project domain methods share the application's owner loop and shutdown."""
from __future__ import annotations

from pathlib import Path

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.projects import ProjectError, ProjectOperation, RegisteredProject
from .service import ProjectService


class ProjectAPI:
    def __init__(self, owner, service: ProjectService | None, *, hosts=None):
        self._owner, self._service = owner, service
        self._hosts = hosts

    async def open(self, project_id: str):
        with self._owner.operation():
            self._require()
            if not isinstance(project_id, str):
                raise ProjectError('project_not_found')
            return await self._hosts.open(project_id)

    async def close(self, project_id: str):
        with self._owner.operation():
            self._require()
            await self._hosts.close(project_id)

    async def history_host(self, project_id: str):
        with self._owner.operation():
            self._require()
            return await self._hosts.open(project_id, history_only=True)

    def _require(self):
        if self._service is None:
            raise ProjectError('project_registry_unavailable')
        return self._service

    @property
    def default_parent_dir(self) -> Path | None:
        self._owner.check_open()
        return self._require().settings.default_parent_dir

    async def list(self) -> tuple[RegisteredProject, ...]:
        with self._owner.operation():
            return await run_blocking(self._require().list)

    async def list_sessions(self, project_id: str):
        with self._owner.operation():
            self._require()
            return await self._hosts.list_sessions(project_id)

    async def get(self, project_id: str) -> RegisteredProject:
        with self._owner.operation():
            return await run_blocking(self._require().get, project_id)

    async def directories(self, path=None, *, show_hidden=False, offset=0, limit=100):
        from .directories import browse_directories
        with self._owner.operation():
            return await run_blocking(browse_directories, path, default_parent=self._require().settings.default_parent_dir,
                                      show_hidden=show_hidden, offset=offset, limit=limit)

    async def add_local(self, root: str | Path, *, trusted: bool, operation_id: str,
                        name: str | None = None, expected_directory: str | None = None) -> RegisteredProject:
        with self._owner.operation():
            return await run_blocking(self._require().add_local, root, trusted=trusted,
                                      operation_id=operation_id, name=name, expected_directory=expected_directory)

    async def create_local(self, *, folder_name: str, trusted: bool, operation_id: str,
                           parent: str | Path | None = None, name: str | None = None,
                           git_init: bool = False, expected_directory: str | None = None) -> RegisteredProject:
        with self._owner.operation():
            return await run_blocking(self._require().create_local, folder_name=folder_name,
                trusted=trusted, operation_id=operation_id, parent=parent, name=name, git_init=git_init, expected_directory=expected_directory)

    async def add_git(self, url: str, *, ref: str, name: str, operation_id: str) -> RegisteredProject:
        with self._owner.operation():
            return await run_blocking(self._require().add_git, url, ref=ref, name=name, operation_id=operation_id)

    async def import_legacy(self, paths: list[Path] | None = None) -> tuple[dict, ...]:
        with self._owner.operation():
            return await run_blocking(self._require().import_legacy, paths)

    async def rename(self, project_id: str, name: str, *, operation_id: str) -> RegisteredProject:
        with self._owner.operation():
            async with self._hosts.project_change(project_id):
                return await run_blocking(self._require().rename, project_id, name, operation_id=operation_id)

    async def trust_git(self,project_id,*,expected_revision,operation_id):
        with self._owner.operation():
            async with self._hosts.project_change(project_id):
                return await run_blocking(self._require().trust_git,project_id,
                    expected_revision=expected_revision,operation_id=operation_id)

    async def relocate(self, project_id: str, *, expected_root: str | Path, root: str | Path,
                       trusted: bool, operation_id: str) -> RegisteredProject:
        with self._owner.operation():
            self._require()
            return await self._hosts.relocate(project_id, expected_root=expected_root,
                root=root, trusted=trusted, operation_id=operation_id)

    async def remove(self, project_id: str, *, operation_id: str) -> dict:
        with self._owner.operation():
            self._require()
            return await self._hosts.remove(project_id, operation_id=operation_id)

    async def operations(self) -> tuple[ProjectOperation, ...]:
        with self._owner.operation():
            return await run_blocking(self._require().operations)

    async def reconcile(self, operation_id: str | None = None) -> tuple[dict, ...]:
        with self._owner.operation():
            return await run_blocking(self._require().reconcile, operation_id)
