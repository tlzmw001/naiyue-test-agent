"""Coordinator-owned task-only bootstrap, mounted read-only separately from state."""
import time
from hashlib import sha256
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_runner import RunnerBootstrap
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask
from naiyue_test_agent.cloud_runner.state import read_json,write_json
from .ownership import private_directory


class TaskBootstrap:
    def __init__(self,runtime):self.runtime=runtime

    def directory(self,task):
        return self.runtime.settings.data_dir/'tasks'/task.task_id/'control'/str(task.generation)

    def prepare(self,task_id):
        r=self.runtime
        with r.gates.hold(task_id):
            task=r.available_task(task_id)
            directory=self.directory(task)
            for path in (directory.parent,directory):
                path.mkdir(mode=0o700,exist_ok=True);private_directory(path)
            try:existing=RunnerBootstrap.model_validate(read_json(directory/'bootstrap.json'))
            except FileNotFoundError:existing=None
            if existing is not None:
                if (existing.task_id!=task_id or existing.generation!=task.generation
                        or existing.boot_id!=task.runner_boot_id or existing.state_epoch!=task.state_epoch):
                    raise CloudError('runner_bootstrap_identity_mismatch')
                if existing.expires_at>time.time():return existing
            generation=task.generation
            profile=existing.models if existing else r.models.profile
            if (existing is None and task.runner_boot_id is not None
                    and task.model_profile_id!=(profile.profile_id if profile else None)):
                raise CloudError('model_profile_changed')
        issued=r.credentials.issue(task_id,scopes=('identity:read','events:append','control:connect','artifacts:write')
            + (('models:invoke',) if profile else ()))
        with r.gates.hold(task_id):
            task=r.available_task(task_id)
            if task.generation!=generation:raise CloudError('task_revision_conflict')
            boot=existing.boot_id if existing else task.runner_boot_id if task.runner_phase=='booting' else str(uuid4())
            boot=boot or str(uuid4())
            epoch=task.state_epoch or str(uuid4())
            if existing is not None:
                b=existing.model_copy(update={'token':issued.token,'expires_at':issued.expires_at})
            else:
                b=RunnerBootstrap(task_id=task_id,project_id=task.project_id,input_id=task.input_id,
                    instance_id=r.instance_id,generation=generation,boot_id=boot,state_epoch=epoch,
                    release_id=task.runner_release_id,endpoint='http://'+r.settings.task_listen,
                    token=issued.token,expires_at=issued.expires_at,max_frame_bytes=r.settings.max_frame_bytes,
                    batch_events=r.settings.history_batch_events,json_depth_limit=r.settings.json_depth_limit,
                    permissions=r.settings.runner_permissions,tool_environment=r.settings.tool_environment,subagents=r.settings.subagents,
                    artifact_limits=r.settings.artifact_limits,result_limits=r.settings.result_limits,input_limits=r.settings.input_limits,
                    models=r.models.profile,history_limits=r.settings.history_limits,
                    observation_limits=r.settings.observation_limits)
            updated=CloudTask.model_validate(task.model_dump()|dict(runner_boot_id=boot,state_epoch=epoch,
                runner_connection_id=None,runner_phase='booting',runner_model_ids=[],runner_capabilities=[],revision=task.revision+1,
                model_profile_id=b.models.profile_id if b.models else None))
            r.registry.save_task_cas(updated,expected_revision=task.revision,expected_generation=generation,revoked_at=time.time())
            # Reserve identity before publishing. An interrupted write can be
            # retried with the same boot/epoch, without deleting task state.
            write_json(directory/'bootstrap.json',b.model_dump(mode='json')|{'token':issued.token.get_secret_value()})
            return b

    def renew(self,connection):
        r=self.runtime;task_id=connection.task_id;old=connection.raw
        with r.gates.hold(task_id):
            task=r.credentials.authorize(old,task_id,'control:connect')
            frozen=RunnerBootstrap.model_validate(read_json(self.directory(task)/'bootstrap.json'))
        issued=r.credentials.issue(task_id,scopes=('identity:read','events:append','control:connect','artifacts:write')
            + (('models:invoke',) if frozen.models else ()))
        with r.gates.hold(task_id):
            task=r.credentials.authorize(old,task_id,'control:connect')
            if task.runner_connection_id!=connection.connection_id:raise CloudError('runner_connection_stale')
            path=self.directory(task)/'bootstrap.json';b=RunnerBootstrap.model_validate(read_json(path))
            b=b.model_copy(update={'token':issued.token,'expires_at':issued.expires_at})
            write_json(path,b.model_dump(mode='json')|{'token':issued.token.get_secret_value()})
            previous=r.credentials.repository.find(sha256(old.encode()).hexdigest())
            r.credentials.repository.revoke(task_id,previous.credential_id,time.time())
            connection.raw=issued.token.get_secret_value()
        return b
