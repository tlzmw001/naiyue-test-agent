"""Optional local maintenance after necessary save, before run ownership release."""
import logging
from naiyue_test_agent.agent_runtime.async_utils import await_settled,run_blocking

logger=logging.getLogger(__name__)


async def after_finish(*,agent,storage,session_id,run_id,snapshot,state,checkpoint_settings,memory_access):
    if state=='ready' and checkpoint_settings is not None:
        try:
            from naiyue_test_agent.storage.checkpoint_retention import prune_after_finish
            await await_settled(prune_after_finish(agent,storage,session_id,run_id,checkpoint_settings,snapshot=snapshot))
        except Exception as error:logger.error('checkpoint_retention_failed error_type=%s',type(error).__name__)
    if memory_access is not None:
        try:await await_settled(run_blocking(memory_access.maintain_expired))
        except Exception as error:logger.error('memory_retention_failed error_type=%s',type(error).__name__)
