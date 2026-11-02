import asyncio
import os
import json
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CreateProject, CloudError
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings, task_record, owner_headers


def setup_task(runtime):
    receipt = runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='history'))
    task = task_record(receipt.resource_id)
    runtime.registry.add_task(task)
    issued = runtime.credentials.issue(task.task_id, scopes=('identity:read', 'events:append'))
    return task, issued.token.get_secret_value()


def batch(task, *, epoch=None, seq=1, source_generation=None):
    return dict(task_id=task.task_id, generation=task.generation, epoch=epoch or str(uuid4()), events=[dict(
        seq=seq, source_generation=source_generation or task.generation, run_id=uuid4().hex,
        kind='history_record', payload={'text': '已完成', 'approved': True})])


def test_events_commit_ack_reopen_dedupe_and_owner_page(tmp_path):
    config = settings(tmp_path)
    async def send(runtime, record, raw, data):
        owner, task = create_cloud_apps(runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task), base_url='http://' + config.task_listen) as client:
            response = await client.post('/internal/cloud/v1/tasks/' + record.task_id + '/events',
                headers={'Authorization': 'Bearer ' + raw}, json=data)
            assert response.status_code == 200, response.text
            assert response.json() == {'epoch': data['epoch'], 'last_seq': 1}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=config.origin,
                headers=owner_headers(runtime)) as client:
            response = await client.get('/api/cloud/v1/tasks/' + record.task_id + '/history/events')
            assert response.status_code == 200
            value = response.json()
            assert value['last_seq'] == 1 and len(value['events']) == 1
            assert value['events'][0]['payload'] == data['events'][0]['payload']
        assert runtime.registry.get_task(record.task_id).lifecycle == 'creating'
    with open_cloud_runtime(config) as runtime:
        record, raw = setup_task(runtime)
        data = batch(record)
        asyncio.run(send(runtime, record, raw, data))
    with open_cloud_runtime(config) as runtime:
        asyncio.run(send(runtime, record, raw, data))


@pytest.mark.parametrize('fault,code', [('gap', 'history_gap'), ('conflict', 'history_conflict'),
    ('epoch', 'history_epoch_mismatch'), ('future_source', 'event_generation_mismatch')])
def test_event_conflicts_are_atomic(tmp_path, fault, code):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, raw = setup_task(runtime)
        original = batch(task)
        runtime.append_events(raw, original)
        changed = batch(task, epoch=original['epoch'], seq=2)
        if fault == 'gap': changed['events'][0]['seq'] = 3
        elif fault == 'conflict': changed['events'] += [original['events'][0] | {'payload': {'text': 'different'}}]
        elif fault == 'epoch': changed['epoch'] = str(uuid4())
        else: changed['events'][0]['source_generation'] = 2
        with pytest.raises(CloudError, match=code): runtime.append_events(raw, changed)
        assert runtime.history_page(task.task_id)['last_seq'] == 1


def test_batch_error_reports_confirmed_cursor_not_rolled_back_provisional_cursor(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, raw = setup_task(runtime)
        first = batch(task)
        runtime.append_events(raw, first)
        second = batch(task, epoch=first['epoch'], seq=2)
        second['events'] += [first['events'][0] | {'payload': {'text': 'conflicting'}}]
        with pytest.raises(CloudError) as caught: runtime.append_events(raw, second)
        assert caught.value.last_seq == 1
        assert runtime.history_page(task.task_id)['last_seq'] == 1


def test_history_page_byte_budget_and_cursor_ahead(tmp_path):
    with open_cloud_runtime(settings(tmp_path, max_frame_bytes=1024)) as runtime:
        task, raw = setup_task(runtime)
        first = batch(task)
        first['events'][0]['payload'] = {'text': 'x' * 600}
        runtime.append_events(raw, first)
        second = batch(task, epoch=first['epoch'], seq=2)
        second['events'][0]['payload'] = {'text': 'y' * 600}
        runtime.append_events(raw, second)
        page = runtime.history_page(task.task_id)
        assert len(page['events']) == 1 and page['last_seq'] == 2 and page['next_seq'] == 1
        assert runtime.history_page(task.task_id, after_seq=1)['next_seq'] == 2
        with pytest.raises(CloudError, match='history_cursor_ahead'):
            runtime.history_page(task.task_id, after_seq=3)


def test_history_read_budget_interrupts_and_releases_transaction(tmp_path, monkeypatch, caplog):
    import naiyue_test_agent.storage.cloud.history as history_module
    with open_cloud_runtime(settings(tmp_path, history_read_timeout_seconds=1.0)) as runtime:
        task, raw = setup_task(runtime)
        data = batch(task)
        data['events'] = [data['events'][0] | {'seq': i} for i in range(1, 33)]
        runtime.append_events(raw, data)
        ticks = iter([0.0] + [10.0] * 100)
        with monkeypatch.context() as patch:
            patch.setattr(history_module, 'monotonic', lambda: next(ticks))
            with pytest.raises(CloudError, match='history_read_timeout'):
                runtime.history_page(task.task_id)
        assert 'cloud_history_read_timeout' in caplog.text
        assert not runtime._histories[task.task_id].database.connection.in_transaction
        assert runtime.history_page(task.task_id)['last_seq'] == 32


@pytest.mark.parametrize('case', ['oversize', 'depth', 'unknown_query', 'duplicate_header'])
def test_http_budgets_and_unknown_parameters_fail_without_side_effects(tmp_path, case):
    with open_cloud_runtime(settings(tmp_path, max_frame_bytes=512, json_depth_limit=4)) as runtime:
        async def run():
            owner, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as client:
                headers = owner_headers(runtime)
                url = '/api/cloud/v1/projects'
                content = b'{"x":"' + b'x' * 600 + b'"}' if case == 'oversize' else b'{"x":[[[[]]]]}'
                if case == 'unknown_query':
                    content = b'{"command_id":"' + str(uuid4()).encode() + b'","name":"x"}'
                    url += '?unknown=1'
                if case == 'duplicate_header':
                    headers = [*headers.items(), ('Authorization', 'Bearer extra')]
                response = await client.post(url, content=content, headers=headers)
                assert response.status_code == (413 if case == 'oversize' else 400 if case == 'duplicate_header' else 422)
                assert 'x' * 100 not in response.text
        asyncio.run(run())
        assert runtime.registry.list_projects() == []


def test_current_credential_can_replay_old_source_but_old_token_is_rejected(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, old = setup_task(runtime)
        first = batch(task)
        runtime.append_events(old, first)
        new = task.model_copy(update={'generation': 2, 'revision': 2})
        runtime.replace_task(new, expected_revision=1, expected_generation=1)
        with pytest.raises(CloudError): runtime.append_events(old, first)
        raw = runtime.credentials.issue(task.task_id, scopes=('events:append',)).token.get_secret_value()
        replay = first | {'generation': 2}
        assert runtime.append_events(raw, replay)['last_seq'] == 1
        next_batch = batch(new, epoch=first['epoch'], seq=2, source_generation=1)
        assert runtime.append_events(raw, next_batch)['last_seq'] == 2


@pytest.mark.parametrize('delivery_generation', [1, 3])
def test_http_delivery_generation_must_match_current_credential(tmp_path, delivery_generation):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, _ = setup_task(runtime)
        current = task.model_copy(update={'generation': 2, 'revision': 2})
        runtime.replace_task(current, expected_generation=1, expected_revision=1)
        raw = runtime.credentials.issue(task.task_id, scopes=('events:append',)).token.get_secret_value()
        payload = batch(current, source_generation=1) | {'generation': delivery_generation}
        async def run():
            _, app = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://' + runtime.settings.task_listen) as client:
                response = await client.post('/internal/cloud/v1/tasks/' + task.task_id + '/events', json=payload,
                    headers={'Authorization': 'Bearer ' + raw})
                assert response.status_code == 409
                assert response.json()['error']['code'] == 'event_generation_mismatch'
        asyncio.run(run())
        assert not list((runtime.settings.data_dir / 'history').glob('*.sqlite'))


@pytest.mark.parametrize('damage', ['missing', 'empty'])
def test_established_history_loss_does_not_reset_epoch_or_cursor(tmp_path, damage):
    config = settings(tmp_path)
    with open_cloud_runtime(config) as runtime:
        task, raw = setup_task(runtime)
        runtime.append_events(raw, batch(task))
    path = config.data_dir / 'history' / (task.task_id + '.sqlite')
    if damage == 'missing': path.unlink()
    else: path.write_bytes(b'')
    with open_cloud_runtime(config) as runtime:
        with pytest.raises(CloudError): runtime.identity(raw, task.task_id)
        with pytest.raises(CloudError): runtime.append_events(raw, batch(task))
    assert not path.exists() if damage == 'missing' else path.stat().st_size == 0


def test_surrogate_json_is_bounded_invalid_request_not_unhandled_failure(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, raw = setup_task(runtime)
        payload = batch(task)
        payload['events'][0]['payload'] = {'text': '\ud800'}
        async def run():
            _, app = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://' + runtime.settings.task_listen) as client:
                response = await client.post('/internal/cloud/v1/tasks/' + task.task_id + '/events',
                    content=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + raw})
                assert response.status_code == 422
                assert response.json()['error']['code'] == 'invalid_request'
        asyncio.run(run())
        assert runtime.history_page(task.task_id)['last_seq'] == 0


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'malicious_sqlite'])
def test_task_owned_sqlite_is_never_opened_by_history(tmp_path, kind):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, raw = setup_task(runtime)
        directory = runtime.settings.data_dir / 'tasks' / task.task_id / 'state'
        directory.mkdir(parents=True)
        path = directory / 'sessions.sqlite'
        if kind == 'symlink': path.symlink_to(runtime.settings.data_dir / 'cloud.sqlite')
        elif kind == 'fifo': os.mkfifo(path)
        else: path.write_bytes(b'not SQLite; task-controlled content')
        assert runtime.append_events(raw, batch(task))['last_seq'] == 1
        assert runtime.history_page(task.task_id)['last_seq'] == 1
        other, token = setup_task(runtime)
        assert runtime.append_events(token, batch(other))['last_seq'] == 1


@pytest.mark.parametrize('content', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{', b'[]'])
def test_invalid_json_never_leaks_input_or_creates_project(tmp_path, content):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        async def run():
            owner, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as client:
                response = await client.post('/api/cloud/v1/projects', content=content, headers=owner_headers(runtime))
                assert response.status_code == 422
                assert set(response.json()) == {'error'}
        asyncio.run(run())
        assert runtime.registry.list_projects() == []
