"""A browser observation borrows only the fixed binding's private owner connection."""
import asyncio
import json
import logging
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


async def observe(manager,domain,binding_id,session_id,*,thread_id=None,last_event_id=None,max_frame_bytes=1048576):
    try:
        with manager.operation(),domain.owner.operation():
            if len(manager.observers)>=manager.settings.max_observers:raise CloudError('cloud_observer_capacity',429)
            owner=asyncio.current_task();manager.observers.add(owner)
            try:
                binding=await run_blocking(domain.repo.binding,binding_id,session_id)
                if binding['task_id'] is None or not binding['retained']:raise CloudError('cloud_task_unavailable')
                async with manager.connection(binding) as client:
                    iterator=client.observe(binding['task_id'],thread_id=thread_id,
                        last_event_id=last_event_id,max_frame_bytes=max_frame_bytes)
                    primary=None
                    try:
                        async with asyncio.timeout(manager.settings.stream_seconds):
                            async for chunk in iterator:yield chunk
                    except BaseException as error:primary=error;raise
                    finally:
                        try:await await_settled(iterator.aclose())
                        except BaseException as error:
                            logger.warning('cloud_local_upstream_close_failed error_type=%s',type(error).__name__)
                            if primary is None:raise
            finally:manager.observers.discard(owner)
    except (CloudError,OSError,TimeoutError) as error:
        code=error.code if isinstance(error,CloudError) else 'cloud_connection_failed'
        logger.warning('cloud_local_observation_unavailable code=%s',code)
        yield ('event: unavailable\ndata: '+json.dumps({'code':code})+'\n\n').encode()
