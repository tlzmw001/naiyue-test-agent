"""Fixed SSH operator invocation; credentials and build paths stay on Linux."""
import json,logging,os,shlex
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator

from naiyue_test_agent.contracts.cloud_tasks import CloudRecord,CloudError,UUIDText,TaskId,ContentId
from naiyue_test_agent.tools.processes import HostProcessRunner,ProcessRequest,run_async_sync

logger=logging.getLogger(__name__)


class RemoteRequest(CloudRecord):
    action: Literal['check','update','status','resume','activate','maintenance','upgrade','cancel','discover','backup','backups','restore','restore-activate','gc']
    release_id: str|None = Field(default=None,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    command_id: UUIDText|None=None
    task_id: TaskId|None=None
    expected_revision: int|None = Field(default=None,ge=0)
    template_id: str|None = Field(default=None,pattern=r'^[a-z0-9][a-z0-9_.-]{0,127}$')
    maintenance: Literal['enter','leave','status']|None=None
    backup_id: UUIDText|None=None
    confirm: UUIDText|ContentId|None=None

    @model_validator(mode='after')
    def complete(self):
        if self.action in ('check','update','activate','upgrade') and self.release_id is None:raise ValueError('release_required')
        if self.action not in ('check','discover','backups') and not (self.action=='maintenance' and self.maintenance=='status') and self.command_id is None:raise ValueError('command_id_required')
        if self.action=='upgrade' and self.task_id is None:raise ValueError('task_required')
        if self.action=='activate' and (self.expected_revision is None or self.template_id is None):raise ValueError('activation_target_required')
        if self.action=='maintenance' and self.maintenance is None:raise ValueError('maintenance_action_required')
        if self.action=='restore' and self.backup_id is None:raise ValueError('backup_required')
        return self

    def arguments(self):
        args=[self.action]
        if self.action in ('status','resume','cancel','restore-activate'):args.append(self.command_id)
        elif self.action=='maintenance':args.append(self.maintenance)
        elif self.action=='upgrade':args.extend([self.task_id,self.release_id])
        elif self.action=='restore':args.append(self.backup_id)
        elif self.action not in ('discover','backup','backups','gc'):args.append(self.release_id)
        if self.command_id is not None and self.action not in ('status','resume','cancel','restore-activate'):args.extend(['--command-id',self.command_id])
        if self.action=='activate':args.extend(['--expected-revision',str(self.expected_revision),'--template-id',self.template_id])
        if self.confirm is not None:args.extend(['--confirm',self.confirm])
        return args


def execute_remote(config,request):
    request=RemoteRequest.model_validate(request)
    words=[config.remote_python,'-I','-m','naiyue_test_agent.cli','cloud','release',*request.arguments(),'--settings',config.remote_settings]
    argv=[config.ssh_executable,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ControlMaster=no','-o','ControlPath=none','-o','ConnectTimeout='+str(max(1,int(config.timeout_seconds))),
        config.ssh_host,shlex.join(words)]
    output=bytearray();counts={'stdout':0,'stderr':0}
    async def consume(stream,chunk):
        counts[stream]+=len(chunk)
        if counts[stream]>1024*1024:raise CloudError('update_ssh_output_limit')
        if stream=='stdout':output.extend(chunk)
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','SYSTEMROOT','SSH_AUTH_SOCK','TMPDIR','TEMP','TMP')}
    try:
        result=run_async_sync(lambda:HostProcessRunner().run(ProcessRequest(argv,Path.cwd(),env,config.timeout_seconds),consume,None))
        if result.termination!='exited' or result.returncode!=0 or not result.output_complete:raise CloudError('update_ssh_unconfirmed',503)
        value=json.loads(output)
        if not isinstance(value,dict):raise ValueError()
        return value
    except (OSError,ValueError) as error:
        logger.error('cloud_update_ssh_failed error_type=%s',type(error).__name__)
        raise CloudError('update_ssh_unconfirmed',503) from None
