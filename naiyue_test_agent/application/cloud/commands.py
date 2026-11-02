"""One local command: project and successful receipt commit together."""
import time
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_tasks import CloudProject, CommandReceipt, CreateProject
from naiyue_test_agent.contracts.cloud_events import digest


class CloudCommands:
    def __init__(self, registry, settings, instance_id):
        self.registry, self.settings, self.instance_id = registry, settings, instance_id

    def create_project(self, command):
        command = CreateProject.model_validate(command)
        fingerprint = digest({'operation': 'project:create', 'instance_id': self.instance_id,
            'payload': {'name': command.name}})
        now = time.time()
        project = CloudProject(project_id=str(uuid4()), name=command.name, revision=1, created_at=now)
        receipt = CommandReceipt(command_id=command.command_id, resource_id=project.project_id,
            operation='project:create', request_digest=fingerprint, status='succeeded', http_status=201,
            result={'project': project.model_dump()}, created_at=now, updated_at=now)
        return self.registry.commit_project(project, receipt, max_receipts=self.settings.max_receipts)
