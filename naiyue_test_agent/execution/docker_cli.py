"""The controlled native Docker client; never accepts a model's argv or cwd."""
import json
import logging
import os
import shlex
import shutil
from pathlib import Path
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.tools.processes import HostProcessRunner,ProcessRequest,run_async_sync

logger=logging.getLogger(__name__)


class DockerCLI:
    def __init__(self,settings,cwd):
        self.settings,self.cwd=settings,Path(cwd)

    def run(self,args,*,input_bytes=None,mutates=False):
        from contextlib import ExitStack
        import tempfile
        ssh=self.settings.ssh
        executable=shutil.which(ssh.executable if ssh is not None else self.settings.docker_executable)
        if executable is None:raise SandboxError('env_ssh_unavailable' if ssh is not None else 'env_docker_unavailable')
        output=bytearray()
        error_size=0
        async def consume(stream,chunk):
            nonlocal error_size
            if stream=='stderr':
                error_size+=len(chunk)
                if error_size>65536:raise SandboxError('env_message_too_large')
            else:
                if len(output)+len(chunk)>self.settings.max_message_bytes:
                    raise SandboxError('env_message_too_large')
                output.extend(chunk)
        environment={key:value for key,value in os.environ.items()
            if key in ('PATH','HOME','USERPROFILE','SYSTEMROOT','TMPDIR','TEMP','TMP','SSH_AUTH_SOCK','DOCKER_CONFIG')}
        if ssh is None:
            argv=[executable,'--context',self.settings.context,*args]
        else:
            command='exec '+shlex.join([ssh.docker_executable,'--host','unix://'+ssh.socket_path,*args])
            argv=[executable,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                  '-o','ForwardAgent=no','-o','ClearAllForwardings=yes',
                  '-o','ConnectTimeout='+str(ssh.connect_timeout_seconds),'--',ssh.host,command]
        with ExitStack() as stack:
            source=None
            if input_bytes is not None:
                if len(input_bytes)>self.settings.max_message_bytes:raise SandboxError('env_message_too_large')
                source=stack.enter_context(tempfile.TemporaryFile())
                source.write(input_bytes);source.seek(0)
            request=ProcessRequest(argv,self.cwd,environment,self.settings.rpc_timeout_seconds,
                stdin_fd=source.fileno() if source is not None else None)
            try:
                result=run_async_sync(lambda:HostProcessRunner().run(request,consume,None))
            except Exception as error:
                logger.warning('docker_request_failed error_type=%s',type(error).__name__)
                if isinstance(error,SandboxError) and not mutates:raise
                raise SandboxError('env_operation_unknown' if mutates else 'env_unreachable') from error
        if result.termination!='exited' or result.returncode!=0:
            logger.warning('docker_request_failed termination=%s exit_code=%s',result.termination,result.returncode)
            raise SandboxError('env_operation_unknown' if mutates else 'env_unreachable')
        return bytes(output)

    def json(self,args):
        try:return json.loads(self.run(args))
        except (ValueError,UnicodeError) as error:raise SandboxError('env_protocol_mismatch') from error

    def daemon(self,expected=None):
        if self.settings.ssh is None:
            contexts=self.json(['context','inspect',self.settings.context])
            endpoint=contexts[0]['Endpoints']['docker']['Host']
            if not endpoint.startswith(('unix://','npipe://')):raise SandboxError('env_endpoint_unsupported')
        info=self.json(['info','--format','{{json .}}'])
        if info.get('OSType')!='linux':raise SandboxError('env_platform_unsupported')
        identity=info.get('ID')
        if not isinstance(identity,str) or not identity:raise SandboxError('env_protocol_mismatch')
        if expected is not None and expected!=identity:raise SandboxError('env_binding_mismatch')
        return identity

    def container(self,identity,*,by_name=False):
        rows=self.run(['container','ls','--all','--no-trunc','--filter',
            ('name=^/'+identity+'$') if by_name else 'id='+identity,'--format','{{.ID}}']).decode().splitlines()
        if not rows:return None
        if len(rows)!=1:raise SandboxError('env_binding_mismatch')
        return self.json(['container','inspect',rows[0]])[0]

    def volume(self,name):
        names=self.run(['volume','ls','--filter','name='+name,'--format','{{.Name}}']).decode().splitlines()
        if name not in names:return None
        return self.json(['volume','inspect',name])[0]
