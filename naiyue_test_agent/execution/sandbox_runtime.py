"""One subprocess-capable event loop per environment service, including Windows."""
import asyncio
import os
from threading import Event,RLock,Thread,get_ident

from naiyue_test_agent.contracts.sandbox import SandboxError


class SandboxRuntime:
    def __init__(self):
        self._guard=RLock();self._closing=False;self.thread=None;self.loop=None

    def submit(self,coroutine):
        with self._guard:
            if self._closing:
                coroutine.close();raise SandboxError('env_stopped')
            if self.thread is None:
                ready=Event()
                def run():
                    self.loop=asyncio.ProactorEventLoop() if os.name=='nt' else asyncio.new_event_loop()
                    asyncio.set_event_loop(self.loop);ready.set()
                    try:self.loop.run_forever()
                    finally:self.loop.close()
                self.thread=Thread(target=run,name='naiyue-sandbox-transport',daemon=True)
                self.thread.start();ready.wait()
            if self.thread.ident==get_ident():
                coroutine.close();raise RuntimeError('Sandbox transport cannot synchronously call itself')
            return asyncio.run_coroutine_threadsafe(coroutine,self.loop)

    def call(self,coroutine):return self.submit(coroutine).result()

    async def await_call(self,coroutine):
        if asyncio.get_running_loop() is self.loop:return await coroutine
        return await asyncio.wrap_future(self.submit(coroutine))

    def close(self):
        with self._guard:
            if self._closing:return
            self._closing=True
            if self.thread is None:return
        async def shutdown():
            tasks=asyncio.all_tasks()-{asyncio.current_task()}
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await self.loop.shutdown_asyncgens()
            await self.loop.shutdown_default_executor()
        try:asyncio.run_coroutine_threadsafe(shutdown(),self.loop).result()
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
