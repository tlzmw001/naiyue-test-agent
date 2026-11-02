"""Three real command owners share only keyed task lifetime, never business rules."""
import asyncio
import logging
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


class CommandProducers:
    def __init__(self,diagnostic):
        self.flights={};self.closing=False;self.diagnostic=diagnostic

    def submit(self,key,fingerprint,producer):
        if self.closing:raise CloudError('cloud_closing',503)
        previous=self.flights.get(key)
        if previous:
            if previous[0]!=fingerprint:raise CloudError('command_id_conflict')
            return previous[1]
        task=asyncio.create_task(producer());self.flights[key]=(fingerprint,task)
        def finished(done):
            if self.flights.get(key,(None,None))[1] is done:self.flights.pop(key)
            if not done.cancelled() and done.exception() is not None:
                logger.warning('%s error_type=%s',self.diagnostic,type(done.exception()).__name__)
        task.add_done_callback(finished)
        return task

    async def aclose(self):
        self.closing=True
        results=await asyncio.gather(*(asyncio.shield(v[1]) for v in list(self.flights.values())),return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException) and not isinstance(result,CloudError):raise result
