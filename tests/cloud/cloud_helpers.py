"""Explicit temporary B identities; never discover developer settings or data."""
from uuid import uuid4

from naiyue_test_agent.application.cloud.settings import CloudSettings
from naiyue_test_agent.contracts.cloud_tasks import CloudTask


def settings(tmp_path, **changes):
    fields = dict(data_dir=tmp_path / 'cloud',
        owner_listen='127.0.0.1:28101', task_listen='172.18.0.1:28102',
        owner_token='owner-test-only-secret', task_token_ttl_seconds=300)
    fields.update(changes)
    return CloudSettings(**fields)


def task_record(project_id, **changes):
    fields = dict(task_id=uuid4().hex, project_id=project_id, title='test task',
        input_id='a' * 64, template_id='python-tests', runner_release_id='test-release',
        state_schema=1, generation=1, revision=1, lifecycle='creating',
        created_at=1.0, updated_at=1.0)
    fields.update(changes)
    return CloudTask(**fields)


def owner_headers(runtime):
    return {'Authorization': 'Bearer ' + runtime.settings.owner_token.get_secret_value(),
        'Origin': runtime.settings.origin, 'X-Cloud-Protocol': '1',
        'X-Service-Instance': runtime.instance_id}
