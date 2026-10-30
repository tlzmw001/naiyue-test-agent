"""Owner-loop, bounded per-sandbox connection custody and single-flight opening."""
import asyncio
from dataclasses import dataclass
import json
import logging

from naiyue_test_agent.contracts.sandbox import SandboxError

logger=logging.getLogger(__name__)


@dataclass
class _Entry:
    identity: tuple
    task: asyncio.Task | None = None
    invalidated: bool = False


class SandboxConnections:
    def __init__(self,factory,*,max_bindings,max_connecting,cleanup_timeout=10):
        if min(max_bindings,max_connecting)<1:raise ValueError('Invalid connection budget')
        self._factory=factory;self._max_bindings=max_bindings
        self._opening=asyncio.Semaphore(max_connecting)
        self._entries={};self._closing=False;self._invalidating={}
        self._cleanup_timeout=cleanup_timeout

    @staticmethod
    def identity(row):
        return (row.binding_id,row.container_id,row.session_id,row.daemon_id,
                json.dumps(row.settings,sort_keys=True,separators=(',',':')))

    async def get(self,row,*,lane='main'):
        if self._closing:raise SandboxError('env_stopped')
        if row.binding_id in self._invalidating:raise SandboxError('env_stopped')
        if lane not in ('main','bulk'):raise ValueError('Invalid connection lane')
        key=(row.binding_id,lane);identity=self.identity(row)
        entry=self._entries.get(key)
        if entry is not None and entry.invalidated:raise SandboxError('env_stopped')
        if entry is not None and entry.identity!=identity:
            await self.invalidate(row.binding_id)
            return await self.get(row,lane=lane)
        if entry is not None and entry.task.done():
            if entry.task.cancelled() or entry.task.exception() is not None or entry.task.result().failed:
                await self._remove(key,entry)
                return await self.get(row,lane=lane)
        if entry is None:
            bindings={key[0] for key in self._entries}
            if row.binding_id not in bindings and len(bindings)>=self._max_bindings:
                raise SandboxError('env_connection_capacity')
            entry=_Entry(identity)
            # No await between checking the key and reserving it.
            self._entries[key]=entry
            entry.task=asyncio.create_task(self._open(row,lane,entry))
            entry.task.add_done_callback(self._observed)
        channel=await asyncio.shield(entry.task)
        if entry.invalidated or self._closing:raise SandboxError('env_stopped')
        return channel

    async def _open(self,row,lane,entry):
        async with self._opening:
            if entry.invalidated or self._closing:raise SandboxError('env_stopped')
            channel=await self._factory(row,lane)
        return channel

    @staticmethod
    def _observed(task):
        if not task.cancelled() and task.exception() is not None:
            logger.warning('sandbox_connect_failed error_type=%s',type(task.exception()).__name__)

    async def _remove(self,key,entry,*,deadline=None):
        entry.invalidated=True
        try:
            channel=await asyncio.shield(entry.task)
        except Exception as error:
            logger.debug('sandbox_connection_acquisition_released error_type=%s',type(error).__name__)
            if isinstance(error,SandboxError) and error.code=='env_connection_cleanup_unconfirmed':
                channel=getattr(error,'channel',None)
                if channel is None:raise
                await channel.close(deadline=deadline)
        else:
            await channel.close(deadline=deadline)
        if self._entries.get(key) is entry:self._entries.pop(key)

    async def invalidate(self,binding,*,wait=True):
        task=self._invalidating.get(binding)
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task=asyncio.create_task(self._retire(binding))
            self._invalidating[binding]=task
            task.add_done_callback(self._observed)
        if wait:await asyncio.shield(task)

    async def _retire(self,binding):
        entries=[(key,value) for key,value in self._entries.items() if key[0]==binding]
        for _,entry in entries:entry.invalidated=True
        deadline=asyncio.get_running_loop().time()+self._cleanup_timeout
        try:
            async with asyncio.timeout_at(deadline):
                results=await asyncio.gather(*(self._remove(key,value,deadline=deadline) for key,value in entries),return_exceptions=True)
        except TimeoutError as error:
            raise SandboxError('env_connection_cleanup_unconfirmed') from error
        for result in results:
            if isinstance(result,BaseException):raise result
        self._invalidating.pop(binding,None)

    async def close(self):
        self._closing=True
        results=await asyncio.gather(*(self.invalidate(binding) for binding in
            {key[0] for key in self._entries}),return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):raise result
