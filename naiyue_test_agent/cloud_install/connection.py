"""Read an existing Coordinator identity; never initialize or discover an instance."""
import logging
from naiyue_test_agent.application.cloud.settings import CloudSettings
from naiyue_test_agent.application.cloud.ownership import CloudOwner,private_directory
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.cloud_files.client import CloudConnection
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


def update_connection(settings):
    explicit=read_settings(settings.connection_file,CloudConnection) if settings.connection_file else None
    if settings.cloud_settings_file is None:
        if explicit is None:raise CloudError('cloud_settings_required',422)
        return explicit
    cloud=read_settings(settings.cloud_settings_file,CloudSettings)
    if settings.task_data_dir is not None and cloud.data_dir!=settings.task_data_dir:raise CloudError('cloud_instance_mismatch')
    try:
        private_directory(cloud.data_dir)
        record=CloudOwner(cloud)._read_marker()
    except OSError as error:
        logger.error('cloud_identity_read_failed error_type=%s',type(error).__name__)
        raise CloudError('cloud_identity_unavailable',503) from None
    if record['status']!='ready':raise CloudError('cloud_identity_unavailable',503)
    derived=CloudConnection(endpoint=cloud.origin,owner_authority=cloud.owner_listen,
        owner_token=cloud.owner_token,instance_id=record['instance_id'])
    if explicit is not None:
        for field in ('endpoint','owner_authority','instance_id'):
            if getattr(explicit,field)!=getattr(derived,field):raise CloudError('cloud_instance_mismatch')
        return explicit
    return derived
