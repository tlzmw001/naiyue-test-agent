"""SDK/producer lifetime inside the single synchronous cloud-root owner."""
from contextlib import asynccontextmanager
import logging

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .lifecycle import CloudLifecycle

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_cloud_backend(runtime, *, backend=None):
    if runtime.lifecycle is not None: raise CloudError('cloud_backend_already_owned')
    if backend is None:
        from .backend import OpenSandboxBackend
        if runtime.settings.backend is None: raise CloudError('cloud_backend_not_configured')
        backend = await OpenSandboxBackend.open(runtime.settings.backend)
    service = CloudLifecycle(runtime, backend)
    runtime.lifecycle = service
    from .deletions import CloudDeletions
    runtime.deletions = CloudDeletions(runtime, backend)
    primary = None
    try:
        yield service
    except BaseException as error:
        primary = error
        raise
    finally:
        for close, code in ((runtime.deletions.aclose,'cloud_deletion_close_failed'),
                (runtime.observations.aclose,'cloud_observation_close_failed'),
                (runtime.control.aclose, 'cloud_control_close_failed'),
                (runtime.models.aclose,'cloud_models_close_failed'),
                (runtime.artifacts.aclose,'cloud_artifacts_close_failed'),
                (runtime.inputs.aclose, 'cloud_inputs_close_failed'),
                (service.aclose, 'cloud_lifecycle_close_failed')):
            try: await close()
            except BaseException as error:
                logger.error('%s error_type=%s', code, type(error).__name__)
                if primary is None: primary = error
        runtime.lifecycle = None
        runtime.deletions = None
        try: await backend.close()
        except BaseException as error:
            logger.error('cloud_backend_close_failed error_type=%s', type(error).__name__)
            if primary is None: primary = error
        if primary is not None and __import__('sys').exc_info()[0] is None:
            raise primary
