"""Bounded private JSON settings shared by operator CLI and local service."""
import logging
import os
from pathlib import Path
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.schema import private_file
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly

logger=logging.getLogger(__name__)


def read_settings(path,model,*,max_bytes=1024*1024):
    path=Path(path).expanduser().absolute()
    try:
        check_store_directory(path.parent);private_file(path)
        with os.fdopen(open_store_readonly(path),'rb') as stream:raw=stream.read(max_bytes+1)
        if len(raw)>max_bytes:raise CloudError('cloud_settings_too_large')
        return model.model_validate_json(raw)
    except (ValidationError,ValueError):raise CloudError('cloud_settings_invalid',422) from None
    except OSError as error:
        logger.error('cloud_settings_read_failed error_type=%s',type(error).__name__)
        raise CloudError('cloud_settings_unavailable',503) from None
