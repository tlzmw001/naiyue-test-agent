"""One service-owned supervisor; intent persisted before changing any SSH process."""
import asyncio
import logging
from uuid import uuid4
from copy import deepcopy
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_networks import NetworkEndpoints,NetworkChange,ServiceTarget
from naiyue_test_agent.cloud_runner.state import read_json,write_json
from naiyue_test_agent.cloud_files.service_tunnels import ReverseProcess,reverse_argv,probe_target

logger=logging.getLogger(__name__)


class ServiceNetworks:
    def __init__(self,manager):
        self.manager=manager;self.state={};self.processes={};self.status={};self.lock=asyncio.Lock()
        self.stopping=asyncio.Event();self.worker=None
        path=manager.settings.connections_file or manager.settings.profiles_file
        self.path=manager.settings.network_state_file or (path.with_name(path.stem+'.networks.json') if path else None)
        if self.path:
            for source in [path,*(profile.connection_file for profile in manager.book.profiles)]:
                if source is not None and str(source.resolve()).casefold()==str(self.path.resolve()).casefold():
                    raise CloudError('cloud_network_state_config_conflict',422)
            try:raw=read_json(self.path,1024*1024)
            except FileNotFoundError:raw={'schema':1,'profiles':{}}
            # Schema and identities are checked again against current profiles before use.
            if (not isinstance(raw,dict) or set(raw)!={'schema','profiles'} or type(raw['schema']) is not int
                    or raw['schema']!=1 or not isinstance(raw['profiles'],dict)):
                raise CloudError('cloud_network_state_invalid')
            self.state=raw['profiles']
            for local in self.state.values():
                local['endpoints']=NetworkEndpoints.model_validate(local['endpoints']).model_dump()

    def start(self):
        if self.worker is None:self.worker=asyncio.create_task(self._maintain())

    def profile(self,ident):
        profile=self.manager.book.require(ident)
        if not profile.network_id or not profile.ssh_host or self.path is None:
            raise CloudError('cloud_network_not_configured',422)
        return profile

    async def save(self):
        if self.path is None:raise CloudError('cloud_network_not_configured',422)
        try:await run_blocking(write_json,self.path,dict(schema=1,profiles=self.state))
        except OSError as error:
            logger.warning('cloud_network_save_failed error_type=%s',type(error).__name__)
            raise

    async def request(self,profile,method,suffix='',**kwargs):
        client=await self.manager.connections.get(profile.profile_id)
        try:return await run_blocking(client.request,method,'/networks/'+profile.network_id+suffix,**kwargs)
        finally:await self.manager.connections.release(client)

    async def read(self,ident):
        profile=self.profile(ident);self.start()
        remote=await self.request(profile,'GET')
        endpoints=NetworkEndpoints.model_validate(remote['endpoints'])
        remote['endpoints']=endpoints.model_dump()
        local=self.state.get(ident,{})
        return dict(**remote,local=local,status={slot:self.status.get((ident,slot),'disconnected') for slot in endpoints.ports})

    async def test(self,ident,target):
        profile=self.profile(ident)
        return await run_blocking(probe_target,target,profile.connect_timeout_seconds)

    async def change(self,ident,slot,target,revision):
        profile=self.profile(ident);self.start()
        async def apply():
            async with self.lock:
                remote=await self.request(profile,'GET');local=self.state.get(ident,{})
                endpoints=NetworkEndpoints.model_validate(remote['endpoints']);endpoints.port(slot)
                remote['endpoints']=endpoints.model_dump()
                if local and not endpoints.extends(NetworkEndpoints.model_validate(local['endpoints'])):
                    raise CloudError('cloud_network_identity_changed',409)
                if local and local.get('network_id')!=profile.network_id:raise CloudError('cloud_network_identity_changed',409)
                pending=local.get('pending')
                if pending is None:
                    if remote['state']['switching']:raise CloudError('cloud_network_switching',409)
                    pending=NetworkChange(command_id=str(uuid4()),expected_revision=revision,slot=slot,target=target).model_dump(mode='json')
                    local=dict(network_id=profile.network_id,endpoints=remote['endpoints'],slots=local.get('slots',{}),pending=pending)
                    previous=self.state.get(ident)
                    self.state[ident]=local
                    try:await self.save()
                    except BaseException:
                        if previous is None:self.state.pop(ident,None)
                        else:self.state[ident]=previous
                        raise
                elif pending['slot']!=slot or pending['target']!=target.model_dump(mode='json'):
                    raise CloudError('cloud_network_switching',409)
                await self._apply(profile,local)
                return await self.read(ident)
        # Accepted operations outlive browser disconnect; service shutdown drains the lock.
        return await await_settled(asyncio.create_task(apply()))

    async def cancel(self,ident):
        profile=self.profile(ident)
        async def work():
            async with self.lock:
                local=self.state.get(ident,{})
                if not local.get('pending'):return await self.read(ident)
                previous=deepcopy(local);local['cancelling']=True
                try:await self.save()
                except BaseException:
                    self.state[ident]=previous
                    raise
                await self._cancel(profile,local)
                return await self.read(ident)
        return await await_settled(asyncio.create_task(work()))

    async def _cancel(self,profile,local):
        pending=local['pending'];remote=await self.request(profile,'GET');state=remote['state']
        if state['last_command'] and state['last_command']['command_id']==pending['command_id']:
            local['slots']=state['slots']
        else:
            await self._stop((profile.profile_id,pending['slot']))
            if state['switching'] or state.get('cancelled_command')==pending['command_id']:
                state=await self.request(profile,'POST','/cancel',json={'command_id':pending['command_id']})
            elif state['revision']!=pending['expected_revision']:raise CloudError('cloud_network_changed',409)
            local['slots']=state['slots']
        local['pending']=None;local.pop('cancelling',None);await self.save()

    async def _stop(self,key):
        process=self.processes.get(key)
        if process is not None:await run_blocking(process.close)
        self.processes.pop(key,None)
        self.status[key]='disconnected'

    async def _connect(self,profile,slot,target,endpoints):
        key=(profile.profile_id,slot);process=self.processes.get(key)
        if process and process.ready and target.enabled and self.status.get(key) in ('connected','reconnecting'):
            if self.status[key]=='reconnecting':
                await self.request(profile,'POST','/probe/'+slot,json={})
                if not process.alive:raise CloudError('cloud_ssh_exited',503)
                self.status[key]='connected'
            return
        if process:await self._stop(key)
        if not target.enabled:self.status[key]='disconnected';return
        self.status[key]='connecting'
        await run_blocking(probe_target,target,profile.connect_timeout_seconds)
        process=await run_blocking(ReverseProcess,reverse_argv(profile,endpoints,slot,target),profile.shutdown_seconds)
        self.processes[key]=process
        deadline=asyncio.get_running_loop().time()+profile.connect_timeout_seconds
        while True:
            if not process.alive:
                logger.warning('cloud_reverse_exited returncode=%s',process.process.returncode)
                raise CloudError('cloud_ssh_exited',503)
            if not process.ready:
                if asyncio.get_running_loop().time()>=deadline:raise CloudError('cloud_ssh_forward_unconfirmed',503)
                await asyncio.sleep(profile.poll_seconds)
                continue
            try:
                await self.request(profile,'POST','/probe/'+slot,json={})
                if not process.alive:raise CloudError('cloud_ssh_exited',503)
                self.status[key]='connected';return
            except CloudError as error:
                if error.code!='cloud_network_unreachable' or asyncio.get_running_loop().time()>=deadline:raise
                await asyncio.sleep(profile.poll_seconds)

    async def _apply(self,profile,local):
        pending=local['pending'];key=(profile.profile_id,pending['slot'])
        try:
            remote=await self.request(profile,'GET')
            if not NetworkEndpoints.model_validate(remote['endpoints']).extends(NetworkEndpoints.model_validate(local['endpoints'])):raise CloudError('cloud_network_identity_changed',409)
            state=await self.request(profile,'POST','/prepare',json=pending)
            if state['switching'] is None:
                # Commit ACK lost: do not retarget or repeat the cloud mutation.
                local['slots'][pending['slot']]=pending['target'];local['pending']=None;await self.save();return
            await self._stop(key)
            target=ServiceTarget.model_validate(pending['target'])
            await self._connect(profile,pending['slot'],target,NetworkEndpoints.model_validate(local['endpoints']))
            await self.request(profile,'POST','/finish',json={'command_id':pending['command_id']})
            local['slots'][pending['slot']]=pending['target'];local['pending']=None;await self.save()
        except CloudError as error:
            self.status[key]=error.code
            # These are definite pre-admission refusals. Retain all uncertain operations.
            if error.code in ('cloud_network_busy','cloud_network_changed'):
                local['pending']=None;await self.save()
            logger.warning('cloud_network_apply_failed code=%s',error.code)
            raise

    async def _maintain(self):
        while not self.stopping.is_set():
            for profile in self.manager.book.profiles:
                if self.stopping.is_set():break
                try:
                    if profile.tunnel=='owned':
                        client=await self.manager.connections.get(profile.profile_id)
                        await self.manager.connections.release(client)
                    async with self.lock:
                        local=self.state.get(profile.profile_id)
                        if not local:continue
                        if local.get('network_id')!=profile.network_id:raise CloudError('cloud_network_identity_changed',409)
                        remote=await self.request(profile,'GET')
                        if not NetworkEndpoints.model_validate(remote['endpoints']).extends(NetworkEndpoints.model_validate(local['endpoints'])):raise CloudError('cloud_network_identity_changed',409)
                        if local.get('pending'):
                            pending_slot=local['pending']['slot']
                            try:
                                if local.get('cancelling'):await self._cancel(profile,local)
                                else:await self._apply(profile,local)
                            except Exception as error:self._failed(profile,pending_slot,error)
                        for slot,value in local['slots'].items():
                            if local.get('pending') and slot==local['pending']['slot']:continue
                            try:await self._connect(profile,slot,ServiceTarget.model_validate(value),NetworkEndpoints.model_validate(local['endpoints']))
                            except Exception as error:self._failed(profile,slot,error)
                except Exception as error:
                    code=error.code if isinstance(error,CloudError) else 'cloud_network_failed'
                    logger.warning('cloud_network_supervisor_failed code=%s error_type=%s',code,type(error).__name__)
                    for owner,slot in tuple(self.status):
                        if owner!=profile.profile_id:continue
                        if self.status.get((profile.profile_id,slot)) in ('connected','connecting'):
                            self.status[profile.profile_id,slot]='reconnecting'
            delay=min((p.reconnect_seconds for p in self.manager.book.profiles),default=5)
            try:await asyncio.wait_for(self.stopping.wait(),delay)
            except TimeoutError:pass

    def _failed(self,profile,slot,error):
        code=error.code if isinstance(error,CloudError) else 'cloud_network_failed'
        if self.status.get((profile.profile_id,slot))!='reconnecting':self.status[profile.profile_id,slot]=code
        logger.warning('cloud_network_slot_failed slot=%s code=%s error_type=%s',slot,code,type(error).__name__)

    async def close(self):
        self.stopping.set()
        if self.worker:await self.worker
        primary=None
        async with self.lock:
            for key in tuple(self.processes):
                try:await self._stop(key)
                except Exception as error:
                    logger.warning('cloud_network_shutdown_failed error_type=%s',type(error).__name__)
                    if primary is None:primary=error
        if primary is not None:raise primary
