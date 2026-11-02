import pytest
from pydantic import ValidationError
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_tasks import CreateProject, CloudTask
from naiyue_test_agent.contracts.cloud_events import EventBatch
from cloud_helpers import settings, task_record


def test_project_contract_strict_and_stable():
    command_id = str(uuid4())
    assert CreateProject(command_id=command_id, name='接口测试').command_id == command_id
    for extra in ({'path': '/private'}, {'name': 1}, {'command_id': '../escape'}, {'name': '  '}):
        with pytest.raises(ValidationError):
            CreateProject(**(dict(command_id=command_id, name='tests') | extra))


@pytest.mark.parametrize('field,value', [('task_id', '../x'), ('generation', 0),
    ('generation', True), ('revision', '1'), ('input_id', 'latest'), ('lifecycle', 'ready-ish')])
def test_task_contract_rejects_unknown_or_coerced_identity(field, value):
    record = task_record(str(uuid4())).model_dump()
    record[field] = value
    with pytest.raises(ValidationError):
        CloudTask(**record)


def test_event_has_distinct_delivery_and_source_generation():
    batch = dict(task_id=uuid4().hex, generation=2, epoch=str(uuid4()), events=[dict(
        seq=1, source_generation=1, run_id=uuid4().hex, kind='history_record', payload={'text': 'ok'})])
    assert EventBatch(**batch).events[0].source_generation == 1
    batch['events'][0]['kind'] = 'owner_approved'
    with pytest.raises(ValidationError):
        EventBatch(**batch)


@pytest.mark.parametrize('change', [{'owner_listen': '0.0.0.0:1'},
    {'task_listen': '0.0.0.0:2'}, {'task_listen': '127.0.0.1:2'},
    {'owner_token': ''}, {'task_token_ttl_seconds': 0}, {'unknown': 1}])
def test_settings_fail_closed(tmp_path, change):
    with pytest.raises(ValidationError):
        settings(tmp_path, **change)
