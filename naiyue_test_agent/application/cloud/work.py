"""Runtime-counted local IO; streaming callers can require cancellation draining."""
import asyncio
import logging
from anyio.to_thread import run_sync

logger=logging.getLogger(__name__)


def _observed(job):
    if not job.cancelled() and job.exception() is not None:
        logger.error('cloud_detached_worker_failed error_type=%s',type(job.exception()).__name__)


async def cloud_work(runtime,action,*,drain_cancel=False):
    def execute():
        with runtime.operation():return action()
    job=asyncio.create_task(run_sync(execute))
    try:return await asyncio.shield(job)
    except asyncio.CancelledError:
        if not drain_cancel:
            job.add_done_callback(_observed)
            raise
        while not job.done():
            try:await asyncio.shield(job)
            except asyncio.CancelledError:continue
            except BaseException:break
        _observed(job)
        raise
