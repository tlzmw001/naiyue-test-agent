"""One Docker environment's immutable identity, policy and envd channel."""
import json
from uuid import uuid4
from naiyue_test_agent.contracts.sandbox import SandboxError,PROTOCOL_VERSION,SANDBOX_ROOT,STATE_ROOT
from naiyue_test_agent.envd.protocol import encode

TMPFS='rw,nosuid,nodev,size=67108864,uid=1000,gid=1000,mode=1777'


def labels(row):
    return {'naiyue.binding':row.binding_id,'naiyue.project':row.project_id,'naiyue.session':row.session_id}


def state_volume(row):return 'nyt-s-'+row.binding_id


def verify_volume(row,data,*,state=False):
    if data is None or data.get('Name')!=(state_volume(row) if state else row.volume_name) or data.get('Labels')!=labels(row):
        raise SandboxError('env_binding_mismatch')


def verify_container_identity(row,data):
    config=data.get('Config',{})
    if (data.get('Id')!=row.container_id or data.get('Image')!=row.image_id
            or data.get('Name')!='/'+row.container_name or config.get('Labels')!=labels(row)):
        raise SandboxError('env_binding_mismatch')


def verify_container(row,data):
    verify_container_identity(row,data)
    config=data.get('Config',{})
    host=data.get('HostConfig',{})
    settings=row.settings
    expected_mode='none' if settings['network']['mode']=='none' else 'bridge'
    mounts=data.get('Mounts',[])
    expected={SANDBOX_ROOT:row.volume_name,STATE_ROOT:state_volume(row)}
    if (config.get('User')!='1000:1000' or not host.get('ReadonlyRootfs') or host.get('Privileged')
            or host.get('NetworkMode')!=expected_mode or host.get('NanoCpus')!=int(settings['cpus']*1000000000)
            or host.get('Memory')!=settings['memory_bytes'] or host.get('PidsLimit')!=settings['pids_limit']
            or set(host.get('CapDrop') or ())!={'ALL'} or 'no-new-privileges' not in (host.get('SecurityOpt') or ())
            or host.get('Tmpfs')!={'/tmp':TMPFS}
            or len(mounts)!=2 or {mount.get('Destination') for mount in mounts}!=set(expected)
            or any(mount.get('Type')!='volume' or mount.get('Name')!=expected.get(mount.get('Destination')) or not mount.get('RW') for mount in mounts)
            or config.get('Entrypoint')!=['python']
            or config.get('Cmd')!=['-I','-m','naiyue_test_agent.envd','serve','--max-bytes',str(settings['max_message_bytes'])]):
        raise SandboxError('env_policy_mismatch')
    env=config.get('Env') or []
    policy={key:value for key,value in row.settings.items() if key!='ssh'}
    if ('NYT_ENVIRONMENT='+json.dumps(row.environment.public(),sort_keys=True) not in env
            or 'NYT_SESSION_ID='+row.session_id not in env
            or 'NYT_SANDBOX_POLICY='+json.dumps(policy,sort_keys=True) not in env):
        raise SandboxError('env_binding_mismatch')


class DockerEnvironment:
    def __init__(self,cli):self.cli=cli

    def action(self,row,action):
        self.cli.daemon(row.daemon_id)
        if action=='recover':
            data=self.cli.container(row.container_id) if row.container_id else self.cli.container(row.container_name,by_name=True)
            if data is None:raise SandboxError('env_expired')
            if row.container_id is None:row=row.changed(container_id=data['Id'])
            verify_container(row,data);verify_volume(row,self.cli.volume(row.volume_name))
            verify_volume(row,self.cli.volume(state_volume(row)),state=True)
            state='running' if data.get('State',{}).get('Running') else 'stopped'
            if state=='running':self.handshake(row)
        elif action=='destroy':
            data=self.cli.container(row.container_id) if row.container_id else self.cli.container(row.container_name,by_name=True)
            if data is not None:
                checked=row if row.container_id is not None else row.changed(container_id=data['Id'])
                verify_container_identity(checked,data)
                self.cli.run(['container','rm','--force',data['Id']],mutates=True)
                if self.cli.container(data['Id']) is not None:raise SandboxError('env_operation_unknown')
            for name,is_state in ((row.volume_name,False),(state_volume(row),True)):
                volume=self.cli.volume(name)
                if volume is not None:
                    verify_volume(row,volume,state=is_state)
                    self.cli.run(['volume','rm',name],mutates=True)
                    if self.cli.volume(name) is not None:raise SandboxError('env_operation_unknown')
            state='destroyed'
        elif action in ('start','stop'):
            data=self.validate(row,running=False)
            if action=='start':
                if not data['State']['Running']:self.cli.run(['container','start',row.container_id],mutates=True)
                self.handshake(row);state='running'
            else:
                if data['State']['Running']:
                    self.cli.run(['container','stop','--time',str(row.settings['stop_timeout_seconds']),row.container_id],mutates=True)
                if self.validate(row,running=False)['State']['Running']:raise SandboxError('env_operation_unknown')
                state='stopped'
        else:raise SandboxError('env_operation_unsupported')
        return row.changed(lifecycle=state,error_code=None)

    def validate(self,row,*,running=True):
        self.cli.daemon(row.daemon_id)
        data=self.cli.container(row.container_id) if row.container_id else None
        if data is None:raise SandboxError('env_expired')
        verify_container(row,data)
        verify_volume(row,self.cli.volume(row.volume_name))
        verify_volume(row,self.cli.volume(state_volume(row)),state=True)
        if running and not data.get('State',{}).get('Running'):raise SandboxError('env_stopped')
        return data

    def create(self,row,save):
        self.cli.daemon(row.daemon_id)
        policy={key:value for key,value in row.settings.items() if key!='ssh'}
        for name,is_state in ((row.volume_name,False),(state_volume(row),True)):
            args=['volume','create']
            for key,value in labels(row).items():args.extend(['--label',key+'='+value])
            self.cli.run([*args,name],mutates=True)
            verify_volume(row,self.cli.volume(name),state=is_state)
        args=['container','create','--name',row.container_name,'--user','1000:1000','--read-only',
              '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit',str(row.settings['pids_limit']),
              '--cpus',str(row.settings['cpus']),'--memory',str(row.settings['memory_bytes']),
              '--network','none' if row.settings['network']['mode']=='none' else 'bridge',
              '--mount','type=volume,src='+row.volume_name+',dst='+SANDBOX_ROOT,
              '--mount','type=volume,src='+state_volume(row)+',dst='+STATE_ROOT,'--tmpfs','/tmp:'+TMPFS,
              '--workdir',SANDBOX_ROOT,'--entrypoint','python',
              '--env','NYT_ENVIRONMENT='+json.dumps(row.environment.public(),sort_keys=True),
              '--env','NYT_SESSION_ID='+row.session_id,
              '--env','NYT_SANDBOX_POLICY='+json.dumps(policy,sort_keys=True)]
        for key,value in labels(row).items():args.extend(['--label',key+'='+value])
        identity=self.cli.run([*args,row.image_id,'-I','-m','naiyue_test_agent.envd','serve',
                              '--max-bytes',str(row.settings['max_message_bytes'])],mutates=True).decode().strip()
        row=row.changed(container_id=identity)
        save(row)
        self.validate(row,running=False)
        self.cli.run(['container','start',identity],mutates=True)
        return row

    def request(self,row,operation,params,*,mutates=False):
        self.validate(row)
        identity=uuid4().hex
        request={'protocol':PROTOCOL_VERSION,'request_id':identity,'environment':row.environment.public(),
                 'session_id':row.session_id,'operation':operation,'params':params}
        raw=self.cli.run(['container','exec','-i',row.container_id,'python','-I','-m',
            'naiyue_test_agent.envd','request','--max-bytes',str(row.settings['max_message_bytes'])],
            input_bytes=encode(request,row.settings['max_message_bytes']),mutates=mutates)
        try:response=json.loads(raw)
        except (ValueError,UnicodeError) as error:
            raise SandboxError('env_operation_unknown' if mutates else 'env_protocol_mismatch') from error
        if (not isinstance(response,dict) or response.get('protocol')!=PROTOCOL_VERSION
                or response.get('request_id')!=identity or response.get('environment')!=row.environment.public()
                or response.get('session_id')!=row.session_id):
            raise SandboxError('env_operation_unknown' if mutates else 'env_protocol_mismatch')
        bad='env_operation_unknown' if mutates else 'env_protocol_mismatch'
        if type(response.get('ok')) is not bool or response['ok'] and 'result' not in response:
            raise SandboxError(bad)
        if response.get('ok') is not True:
            error=response.get('error',{})
            from naiyue_test_agent.contracts.git_inputs import GIT_INPUT_CODES,GitInputError
            from naiyue_test_agent.contracts.git_results import GIT_RESULT_CODES
            if (isinstance(error,dict) and isinstance(error.get('code'),str)
                    and isinstance(error.get('message'),str) and error['code'] in GIT_INPUT_CODES|GIT_RESULT_CODES):
                raise GitInputError(error['code'])
            allowed={'tool_error','env_binding_mismatch','env_message_too_large','env_operation_unsupported',
                     'env_resource_outside','env_resource_unavailable','env_operation_unknown','env_protocol_mismatch',
                     'env_operation_not_found','env_operation_conflict','env_journal_full','env_operation_capacity',
                     'env_invalid_operation_id','env_stop_unconfirmed','env_stopped'}
            if (not isinstance(error,dict) or not isinstance(error.get('code'),str) or error.get('code') not in allowed
                    or not isinstance(error.get('message'),str)):
                raise SandboxError(bad)
            if error.get('code')=='tool_error':
                from langchain_core.tools import ToolException
                raise ToolException(str(error.get('message','Sandbox file operation failed')))
            raise SandboxError(str(error.get('code','env_protocol_mismatch')))
        return response['result']

    def handshake(self,row):
        from naiyue_test_agent import __version__
        result=self.request(row,'hello',{})
        if result!={'protocol':PROTOCOL_VERSION,'package_version':__version__,
                    'environment':row.environment.public(),'session_id':row.session_id}:
            raise SandboxError('env_protocol_mismatch')
