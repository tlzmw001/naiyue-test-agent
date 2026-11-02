"""One bounded producer per export can be cancelled even while a browser stops reading."""
import asyncio,logging
from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
logger=logging.getLogger(__name__)


class DownloadLease:
    def __init__(self,manager,source):
        if manager.closing:raise CloudError('cloud_client_closing',503)
        if len(manager.downloads)+len(manager.observers)>=manager.settings.max_observers:raise CloudError('cloud_observer_capacity',429)
        self.manager,self.source=manager,source;self.queue=asyncio.Queue(maxsize=1)
        manager.downloads.add(self);self.job=asyncio.create_task(self.produce())

    async def produce(self):
        primary=None
        try:
            async for part in self.source:await self.queue.put((True,part))
            await self.queue.put((False,None))
        except asyncio.CancelledError as error:primary=error;raise
        except Exception as error:
            primary=error;await self.queue.put((False,error))
        finally:
            try:await await_settled(self.source.aclose())
            except BaseException as error:
                logger.error('cloud_download_source_close_failed error_type=%s',type(error).__name__)
                if primary is None:raise
            finally:self.manager.downloads.discard(self)

    async def iterate(self):
        try:
            while True:
                receive=asyncio.create_task(self.queue.get())
                try:
                    await asyncio.wait((receive,self.job),return_when=asyncio.FIRST_COMPLETED)
                    if not receive.done() and self.queue.empty():
                        self.job.result();break
                    available,value=await receive
                finally:
                    if not receive.done():receive.cancel()
                    await asyncio.gather(receive,return_exceptions=True)
                if not available:
                    if value is not None:raise value
                    break
                yield value
        finally:await self.aclose()

    async def aclose(self):
        if not self.job.done():self.job.cancel()
        result=(await await_settled(asyncio.gather(self.job,return_exceptions=True)))[0]
        primary=result if isinstance(result,BaseException) and not isinstance(result,asyncio.CancelledError) else None
        try:await await_settled(self.source.aclose())
        except BaseException as error:
            logger.error('cloud_download_lease_close_failed error_type=%s',type(error).__name__)
            if primary is None:raise
        finally:self.manager.downloads.discard(self)
        if primary is not None:raise primary
