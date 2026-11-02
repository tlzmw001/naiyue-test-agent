"""Owned execution survives WS loss; only the durable outbox is retried."""
import asyncio
import logging
import time
from contextlib import AsyncExitStack

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_events import EventBatch,canonical_json
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_runner import Hello,CoordinatorFrame,RunnerBootstrap
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .state import read_json
from .http import task_request
from .artifact_transport import publish_artifacts

logger=logging.getLogger(__name__)


class RunnerClient:
    def __init__(self,kernel,*,http=None,bootstrap_path=None):
        self.kernel,self.http,self.bootstrap_path=kernel,http,bootstrap_path
        self.bootstrap=kernel.bootstrap
        self.fence=None;self.ws=None;self.send_lock=asyncio.Lock()
        self.commands=set();self.reported=set()
        self.artifact_reported=set();self.control_commands=set();self.control_reported=set()

    def hello(self):
        b=self.bootstrap
        return Hello(kind='hello',phase=self.kernel.phase,capabilities=['artifacts:capture','control:stop','control:recover','background:tasks','observations:send'],recovery=self.kernel.recovery_info,
            model_ids=list(self.kernel.catalog.model_ids) if self.kernel.catalog else [],
            **{k:getattr(b,k) for k in ('task_id','project_id','input_id','instance_id','generation','boot_id','state_epoch','release_id')})

    async def send(self,value):
        async with self.send_lock:
            await self.ws.send(canonical_json(value|self.fence))

    async def receive(self):
        raw=await self.ws.recv()
        frame=CoordinatorFrame.validate_python(parse_object(raw,byte_limit=self.bootstrap.max_frame_bytes,
            depth_limit=self.bootstrap.json_depth_limit))
        for k in ('generation','boot_id','state_epoch'):
            if getattr(frame,k)!=getattr(self.bootstrap,k):raise CloudError('runner_connection_stale')
        if self.fence is not None and frame.connection_id!=self.fence['connection_id']:raise CloudError('runner_connection_stale')
        return frame

    async def receiver(self):
        while True:
            frame=await self.receive()
            if frame.kind=='command':
                if self.bootstrap.expires_at<=time.time():raise CloudError('runner_credential_expired')
                result=await await_settled(self.kernel.accept(frame.command))
                self.commands.add(frame.command.command_id)
                await self.send({'kind':'accepted'}|result)
            elif frame.kind=='query':
                self.commands.add(frame.command_id);self.reported.discard(frame.command_id)
                result=await run_blocking(self.kernel.journal.lookup,frame.command_id)
                await self.send({'kind':'state'}|(result or dict(command_id=frame.command_id,status='not_found')))
            elif frame.kind=='control_command':
                if self.bootstrap.expires_at<=time.time():raise CloudError('runner_credential_expired')
                try:result=await await_settled(self.kernel.actions.accept(frame.command))
                except CloudError as error:result=await await_settled(self.kernel.actions.reject(frame.command,error))
                self.control_commands.add(frame.command.command_id)
                await self.send(dict(kind='control_state',command_id=frame.command.command_id,status=result['status']))
            elif frame.kind=='control_query':
                self.control_commands.add(frame.command_id);self.control_reported.discard(frame.command_id)
                result=await run_blocking(self.kernel.journal.control_lookup,frame.command_id)
                await self.send(dict(kind='control_state',command_id=frame.command_id,status=result['status'] if result else 'not_found'))
            elif frame.kind=='renewed':
                self.bootstrap=self.bootstrap.model_copy(update={'token':frame.token,'expires_at':frame.expires_at})
                if self.kernel.remote_models:self.kernel.remote_models.renew(frame.token,frame.expires_at)
            elif frame.kind=='artifact_capture':
                if self.bootstrap.expires_at<=time.time():raise CloudError('runner_credential_expired')
                value=await await_settled(self.kernel.artifacts.accept(frame.command))
                await self.send(dict(kind='artifact_state',command_id=value.command.command_id,
                    artifact_id=value.command.artifact_id,status=value.status))
            elif frame.kind=='artifact_query':
                self.artifact_reported.discard(frame.command_id)
                value=await run_blocking(self.kernel.artifacts.journal.get,frame.command_id)
                await self.send(dict(kind='artifact_state',command_id=frame.command_id,
                    artifact_id=value.command.artifact_id if value else None,status=value.status if value else 'not_found'))

    async def publish(self):
        b=self.bootstrap
        pending=await run_blocking(self.kernel.journal.pending)
        if pending:
            batch=EventBatch(task_id=b.task_id,generation=b.generation,epoch=b.state_epoch,events=pending)
            ack=await task_request(self.http,self.bootstrap,'POST','/internal/cloud/v1/tasks/'+b.task_id+'/events',value=batch.model_dump())
            if (set(ack)!={'epoch','last_seq'} or ack['epoch']!=b.state_epoch
                    or type(ack['last_seq']) is not int or ack['last_seq']<pending[-1].seq):raise CloudError('history_ack_invalid')
            await run_blocking(self.kernel.journal.ack,pending[-1].seq)
        await publish_artifacts(self)
        if await run_blocking(self.kernel.journal.pending):return
        if self.kernel.active_task is not None and not self.kernel.active_task.done():return
        for command_id in self.commands-self.reported:
            result=await run_blocking(self.kernel.journal.lookup,command_id)
            if (result and result['terminal_seq'] is not None and result['status']!='recovered'
                    and not (result['status'] in ('completed','stopped') and self.kernel.phase=='blocked')):
                quiet=self.kernel.session_quiet()
                await self.send(dict(kind='finished',quiescent=quiet)|result)
                self.reported.add(command_id)
        if self.kernel.actions.busy:return
        for command_id in self.control_commands-self.control_reported:
            result=await run_blocking(self.kernel.journal.control_lookup,command_id)
            if result and result['terminal_seq'] is not None and result['phase']==self.kernel.phase:
                value={key:result[key] for key in ('command_id','status','terminal_seq','phase','quiescent','error_code','recovery')}
                await self.send(dict(kind='control_finished',**value))
                self.control_reported.add(command_id)
        await self.kernel.results.publish(self)

    async def publisher(self):
        while True:
            try:await self.publish()
            except (httpx.HTTPError,CloudError,TimeoutError) as error:
                logger.warning('runner_history_delivery_unconfirmed error_type=%s',type(error).__name__)
            await asyncio.sleep(self.bootstrap.retry_seconds)

    async def heartbeat(self):
        while True:
            remaining=self.bootstrap.expires_at-time.time()
            await asyncio.sleep(min(self.bootstrap.heartbeat_seconds,max(0.1,remaining/3)))
            renew=self.bootstrap.expires_at-time.time()<self.bootstrap.heartbeat_seconds*3
            quiet=self.kernel.session_quiet() and not await run_blocking(self.kernel.journal.pending)
            if quiet and self.kernel.phase=='ready' and self.kernel.previous_activity is None:
                await run_blocking(self.kernel.journal.mark_activity,False)
            await self.send(dict(kind='renew' if renew else 'heartbeat',quiescent=quiet,phase=self.kernel.phase,
                run_id=self.kernel.last_run_id,recovery=self.kernel.recovery_info.model_dump() if self.kernel.recovery_info else None))

    async def observer(self):
        while True:
            await asyncio.sleep(self.bootstrap.observation_limits.flush_seconds)
            batch=self.kernel.observations.drain()
            if batch is not None:
                async with asyncio.timeout(self.bootstrap.observation_limits.send_timeout_seconds):
                    await self.send(dict(kind='observations',batch=batch))

    async def connected(self):
        self.fence=None;self.reported=set();self.artifact_reported=set();self.control_reported=set()
        await self.ws.send(self.hello().model_dump_json())
        welcome=await self.receive()
        if welcome.kind!='welcome':raise CloudError('runner_handshake_invalid')
        self.fence={k:getattr(welcome,k) for k in ('connection_id','generation','boot_id','state_epoch')}
        jobs=[asyncio.create_task(fn()) for fn in (self.receiver,self.publisher,self.heartbeat,self.observer)]
        try:
            done,_=await asyncio.wait(jobs,return_when=asyncio.FIRST_COMPLETED)
            for job in done:job.result()
        finally:
            for job in jobs:job.cancel()
            await asyncio.gather(*jobs,return_exceptions=True)

    async def loop(self,stop):
        while not stop.is_set():
            if self.bootstrap_path:
                refreshed=await run_blocking(read_json,self.bootstrap_path)
                candidate=RunnerBootstrap.model_validate(refreshed)
                if candidate.model_dump(exclude={'token','expires_at'})!=self.bootstrap.model_dump(exclude={'token','expires_at'}):
                    raise CloudError('runner_bootstrap_identity_mismatch')
                self.bootstrap=candidate
                if self.kernel.remote_models:self.kernel.remote_models.renew(candidate.token,candidate.expires_at)
            b=self.bootstrap
            scheme='wss' if b.endpoint.startswith('https:') else 'ws'
            uri=scheme+b.endpoint[b.endpoint.index(':'):].rstrip('/')+'/internal/cloud/v1/tasks/'+b.task_id+'/control'
            try:
                async with connect(uri,additional_headers={'Authorization':'Bearer '+b.token.get_secret_value()},
                        proxy=None,max_size=b.max_frame_bytes,max_queue=16,open_timeout=15,close_timeout=5) as ws:
                    self.ws=ws
                    await self.connected()
            except (OSError,TimeoutError,WebSocketException,CloudError) as error:
                logger.warning('runner_control_unavailable error_type=%s',type(error).__name__)
            try:await asyncio.wait_for(stop.wait(),timeout=b.retry_seconds)
            except TimeoutError:pass

    async def run(self,stop):
        async with AsyncExitStack() as stack:
            if self.http is None:
                self.http=await stack.enter_async_context(httpx.AsyncClient(base_url=self.bootstrap.endpoint,
                    trust_env=False,follow_redirects=False,timeout=15.0))
            job=asyncio.create_task(self.loop(stop));waiting=asyncio.create_task(stop.wait())
            try:
                done,_=await asyncio.wait((job,waiting),return_when=asyncio.FIRST_COMPLETED)
                if job in done:job.result()
            finally:
                job.cancel();waiting.cancel()
                await asyncio.gather(job,waiting,return_exceptions=True)
                if self.kernel.active_task and not self.kernel.active_task.done():
                    self.kernel.sessions.stop_loaded_sessions()
                    await await_settled(self.kernel.active_task)
