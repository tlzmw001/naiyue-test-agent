"""Scoped opaque credentials; raw secrets only cross the internal bootstrap call."""
from dataclasses import dataclass
from hashlib import sha256
import secrets
import time
from uuid import uuid4

from pydantic import SecretStr

from naiyue_test_agent.contracts.cloud_tasks import CloudError, StoredTaskCredential
from naiyue_test_agent.storage.cloud.credentials import CredentialRepository

SCOPES = frozenset({'identity:read', 'events:append', 'control:connect','artifacts:write','models:invoke'})


@dataclass(frozen=True)
class IssuedCredential:
    credential_id: str
    token: SecretStr
    expires_at: float


class TaskCredentials:
    def __init__(self, registry, gates, settings):
        self.registry, self.gates, self.settings = registry, gates, settings
        self.repository = CredentialRepository(registry.database)

    def issue(self, task_id, *, scopes):
        if (not scopes or len(scopes) != len(set(scopes)) or not set(scopes) <= SCOPES):
            raise CloudError('invalid_credential_scope', 422)
        with self.gates.hold(task_id):
            task = self.registry.get_task(task_id)
            if task.lifecycle in ('deleted', 'deleting'):
                raise CloudError('task_not_available')
            raw, ident = secrets.token_urlsafe(32), str(uuid4())
            expires_at = time.time() + self.settings.task_token_ttl_seconds
            self.repository.save(StoredTaskCredential(credential_id=ident, token_hash=sha256(raw.encode()).hexdigest(),
                task_id=task_id, project_id=task.project_id, generation=task.generation, scopes=sorted(scopes),
                expires_at=expires_at))
        return IssuedCredential(ident, SecretStr(raw), expires_at)

    def authorize(self, raw, task_id, scope):
        if not isinstance(raw, str) or not raw or len(raw) > 256:
            raise CloudError('unauthorized', 401)
        credential = self.repository.find(sha256(raw.encode()).hexdigest())
        if (credential is None or credential.task_id != task_id or credential.revoked_at is not None
                or credential.expires_at <= time.time() or scope not in credential.scopes):
            raise CloudError('unauthorized', 401)
        task = self.registry.get_task(task_id)
        if (task.project_id != credential.project_id or task.generation != credential.generation
                or task.lifecycle in ('deleted', 'deleting')):
            raise CloudError('unauthorized', 401)
        return task

    def revoke(self, task_id, credential_id):
        with self.gates.hold(task_id):
            self.repository.revoke(task_id, credential_id, time.time())
