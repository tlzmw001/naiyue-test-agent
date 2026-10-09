"""Drain already-started IO before releasing its owner or closing resources."""

import asyncio
import logging


logger = logging.getLogger(__name__)


async def await_settled(awaitable):
    task = asyncio.ensure_future(awaitable)
    cancellation = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as error:
            if task.cancelled():
                raise
            cancellation = cancellation or error
        except BaseException as error:
            if cancellation is not None:
                logger.error('drained_operation_failed error_type=%s', type(error).__name__)
                raise cancellation
            raise
    if cancellation is not None:
        raise cancellation
    return result


async def run_blocking(call, *args, **kwargs):
    return await await_settled(asyncio.to_thread(call, *args, **kwargs))


async def acquire_blocking(call, dispose, *args, **kwargs):
    """A cancelled factory must dispose a resource acquired by its worker."""
    task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await await_settled(task)
    except BaseException:
        if task.done() and not task.cancelled() and task.exception() is None:
            try:
                await await_settled(dispose(task.result()))
            except BaseException as error:
                logger.error('acquisition_cleanup_failed error_type=%s', type(error).__name__)
        raise
