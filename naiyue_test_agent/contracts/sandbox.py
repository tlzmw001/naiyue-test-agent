"""Durable execution facts, never a Docker connection or tool authorization."""
from dataclasses import dataclass, replace
import hashlib
import json
import re
from .environment import EnvironmentRef

PROTOCOL_VERSION = 2
PROCESS_CLEANUP_ERROR_CODE = 'env_process_cleanup_unconfirmed'
SANDBOX_ROOT = '/workspace'
STATE_ROOT = '/state'
SOCKET_PATH = '/tmp/naiyue-envd.sock'


class SandboxError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require_operation_id(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{32}',value) is None:
        raise SandboxError('env_invalid_operation_id')
    return value


def operation_id(runtime):
    scope=runtime.context.scope
    if not scope.identity.run_id or not runtime.tool_call_id:
        raise SandboxError('env_invalid_operation_id')
    value=[scope.environment.public(),scope.identity.session_id,scope.identity.run_id,
           scope.identity.agent_path,runtime.tool_call_id]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:32]


def operation_fingerprint(kind,payload):
    value=dict(payload)
    if kind in ('write','edit'):
        value['subject']={key:item for key,item in payload['subject'].items() if key in
            ('workspace','requested','resolved','args','tool_name','execution_environment')}
    return hashlib.sha256(json.dumps([kind,value],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class SandboxRecord:
    binding_id: str
    project_id: str
    session_id: str
    lifecycle: str
    daemon_id: str
    container_name: str
    volume_name: str
    image_id: str
    settings: dict
    container_id: str | None = None
    error_code: str | None = None
    input_id: str | None = None
    input_snapshot: str | None = None
    input_operation_id: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    profile_revision: str | None = None
    created_at: float | None = None
    last_used_at: float | None = None

    @property
    def environment(self):
        return EnvironmentRef(self.binding_id, 'sandbox', self.project_id, SANDBOX_ROOT, 'linux')

    def changed(self, **values):
        return replace(self, **values)
