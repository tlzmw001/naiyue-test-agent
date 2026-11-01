"""Application-owned persistent transport; frozen Docker records are the authority."""
import asyncio

from naiyue_test_agent.sandbox_settings import SandboxSettings
from .docker_cli import DockerCLI
from .sandbox_channel import SandboxChannel,RequestNotSent
from .sandbox_connections import SandboxConnections
from .sandbox_runtime import SandboxRuntime


class SandboxTransport:
    def __init__(self,cwd,configuration):
        self.cwd=cwd;self.runtime=SandboxRuntime()
        self._starting=set()
        self.connections=SandboxConnections(self._open,max_bindings=configuration.lifecycle.max_retained,
            max_connecting=configuration.transport.connecting,cleanup_timeout=configuration.transport.cleanup_timeout_seconds)

    async def _open(self,row,lane):
        from .docker_environment import DockerEnvironment
        settings=SandboxSettings.model_validate(row.settings);cli=DockerCLI(settings,self.cwd)
        policy=settings.transport
        args=['container','exec','-i',row.container_id,'python','-I','-m',
            'naiyue_test_agent.envd','attach','--max-bytes',str(settings.max_message_bytes),'--lane',lane]
        preamble_check=None;start=False
        if settings.ssh is not None:
            from .docker_management import attach_command,validate_snapshot
            identity=self.connections.identity(row)
            start=lane=='main' and identity in self._starting
            if start:self._starting.remove(identity)
            argv=attach_command(cli,row,args,start=start)
            preamble_check=lambda value:validate_snapshot(row,value,running=not start)
        else:
            await asyncio.to_thread(DockerEnvironment(cli).validate,row)
            argv=cli.command(args)
        channel=await SandboxChannel.open(argv,environment=row.environment.public(),session_id=row.session_id,
            env=cli.child_environment(),cwd=self.cwd,timeout=settings.rpc_timeout_seconds,
            frame_bytes=policy.frame_bytes,message_bytes=settings.max_message_bytes,
            io_slots=1 if lane=='bulk' else policy.io_requests,control_slots=policy.control_requests,
            max_waiting=policy.pending_requests,preamble_check=preamble_check,
            cleanup_timeout=policy.cleanup_timeout_seconds,cleanup_attempts=policy.cleanup_attempts,
            heartbeat_interval=policy.keepalive_interval_seconds,heartbeat_timeout=policy.heartbeat_timeout_seconds,
            start_expected=start)
        return channel

    def start_created(self,row):
        return self.runtime.call(self._start_created(row))

    async def _start_created(self,row):
        from naiyue_test_agent.contracts.sandbox import SandboxError
        if row.lifecycle!='creating' or row.container_id is None or row.settings.get('ssh') is None:
            raise SandboxError('env_operation_unsupported')
        identity=self.connections.identity(row)
        if identity in self._starting:raise SandboxError('env_busy')
        self._starting.add(identity)
        try:return await self.connections.get(row)
        finally:self._starting.discard(identity)

    async def _request(self,row,operation,params,mutates):
        lane='bulk' if operation=='result_chunk' else 'main'
        try:channel=await self.connections.get(row,lane=lane)
        except RequestNotSent:raise
        except Exception as error:
            from naiyue_test_agent.contracts.sandbox import SandboxError
            if isinstance(error,SandboxError):raise RequestNotSent(error.code) from error
            raise
        return await channel.request(operation,params,mutates=mutates)

    def request(self,row,operation,params,*,mutates=False):
        return self.runtime.call(self._request(row,operation,params,mutates))

    async def arequest(self,row,operation,params,*,mutates=False):
        return await self.runtime.await_call(self._request(row,operation,params,mutates))

    def validate(self,row):self.runtime.call(self.connections.get(row))

    def invalidate(self,binding,*,wait=True):
        if self.runtime.thread is not None:self.runtime.call(self.connections.invalidate(binding,wait=wait))

    async def ainvalidate(self,binding):
        if self.runtime.thread is not None:await self.runtime.await_call(self.connections.invalidate(binding))

    async def aclose(self):
        try:
            if self.runtime.thread is not None:await self.runtime.await_call(self.connections.close())
        finally:await asyncio.to_thread(self.runtime.close)
