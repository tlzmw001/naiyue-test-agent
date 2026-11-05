"""B environment facts, distinct from Runner readiness and owner approval."""
from typing import Literal

from pydantic import Field

from .cloud_tasks import CloudRecord, ContentId, Label, TaskId, UUIDText, TemplateReceipt


class BackendFailure(Exception):
    def __init__(self, code, *, rejected=False):
        super().__init__(code)
        self.code, self.rejected = code, rejected


class EnvironmentCommand(CloudRecord):
    command_id: UUIDText
    kind: Literal['start', 'stop_environment', 'refresh']
    expected_revision: int = Field(ge=1)
    expected_generation: int = Field(ge=1)
    acknowledge_unknown:bool=False


class EnvironmentOperation(CloudRecord):
    command_id: UUIDText
    task_id: TaskId
    generation: int = Field(ge=1)
    kind: Literal['start', 'stop_environment', 'refresh']
    phase: Literal['reserved', 'dispatched', 'unknown', 'finished']
    backend_id: Label
    template: TemplateReceipt
    instance_id: UUIDText
    sandbox_id: Label | None = None
    idle_cutoff: float | None = None
    acknowledge_unknown:bool=False

    @property
    def metadata(self):
        return {'nyt-instance': self.instance_id, 'nyt-task': self.task_id,
            'nyt-generation': str(self.generation), 'nyt-command': self.command_id}


class SandboxFacts(CloudRecord):
    sandbox_id: Label
    image_id: str
    metadata: dict[str, str]
    state: str
    expires_at: float | None


class MountSpec(CloudRecord):
    source: str
    destination: Literal['/workspace', '/state', '/input', '/run/nyt']
    read_only: bool


class ContainerCheck(CloudRecord):
    file_transfer_version:int=Field(default=0,ge=0,le=1,exclude_if=lambda v:v==0)
    schema_version: int
    uid: int
    euid: int
    gid: int
    egid: int
    groups: list[int]
    no_new_privs: int
    caps: dict[str, int]
    runner_readonly: bool
    runner_version: Label
    kit_version: Label
    runner_protocol: int | None = None
    state_schema: int | None = None
    toolchain_sha256: ContentId | None = None
    checks: dict[str, str] = Field(default_factory=dict)

    def verify(self, *, uid, gid, kit_version, full=False):
        valid = (self.schema_version == 1 and self.uid == self.euid == uid > 0
            and self.gid == self.egid == gid > 0 and 0 not in self.groups
            and self.no_new_privs == 1 and self.runner_readonly is True
            and set(self.caps) == {'CapEff', 'CapPrm', 'CapInh', 'CapAmb', 'CapBnd'}
            and not any(self.caps.values()) and self.kit_version == kit_version)
        if full:
            valid = valid and self.toolchain_sha256 is not None and all(self.checks.get(k) == 'passed'
                for k in ('kit_cli', 'doctor', 'pytest', 'report', 'pip_check', 'native_tools'))
        if not valid:
            from .cloud_tasks import CloudError
            raise CloudError('template_preflight_failed' if full else 'container_preflight_failed')
        return self
