import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CreateProject
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings, owner_headers
from test_history_ingress import setup_task, batch


@pytest.mark.parametrize('fence', ['revoke', 'generation'])
def test_validation_commit_race_rechecks_credential_under_task_gate(tmp_path, monkeypatch, fence):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, _ = setup_task(runtime)
        issued = runtime.credentials.issue(task.task_id, scopes=('events:append',))
        raw = issued.token.get_secret_value()
        checked, proceed = Event(), Event()
        original = runtime.credentials.authorize
        def pause(raw, task_id, scope):
            result = original(raw, task_id, scope)
            checked.set()
            assert proceed.wait(5)
            return result
        monkeypatch.setattr(runtime.credentials, 'authorize', pause)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(runtime.append_events, raw, batch(task))
            try:
                assert checked.wait(5)
                if fence == 'revoke': runtime.credentials.revoke(task.task_id, issued.credential_id)
                else:
                    runtime.replace_task(task.model_copy(update={'generation': 2, 'revision': 2}),
                        expected_revision=1, expected_generation=1)
            finally:
                proceed.set()
            with pytest.raises(CloudError, match='unauthorized'): pending.result(5)
        assert runtime.history_page(task.task_id)['last_seq'] == 0
        assert not runtime.gates._entries


def test_concurrent_conflicting_sequence_has_exactly_one_winner(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task, raw = setup_task(runtime)
        first = batch(task)
        other = first | {'events': [first['events'][0] | {'payload': {'text': 'other'}}]}
        start = Barrier(2)
        def send(data):
            start.wait(5)
            try: return runtime.append_events(raw, data)
            except CloudError as error: return error.code
        with ThreadPoolExecutor(2) as pool:
            values = list(pool.map(send, [first, other]))
        assert values.count('history_conflict') == 1
        assert len(runtime.history_page(task.task_id)['events']) == 1


def test_shutdown_drains_registered_worker_before_database_close(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        entered, release, closed = Event(), Event(), Event()
        command = CreateProject(command_id=str(uuid4()), name='drain')
        def work():
            with runtime.operation():
                entered.set()
                assert release.wait(5)
                return runtime.commands.create_project(command)
        def shutdown():
            runtime.close()
            closed.set()
        with ThreadPoolExecutor(2) as pool:
            pending = pool.submit(work)
            assert entered.wait(5)
            stop = pool.submit(shutdown)
            try:
                with runtime._requests:
                    assert runtime._requests.wait_for(lambda: runtime._closing, 5)
                assert not closed.is_set()
                with pytest.raises(CloudError, match='cloud_closing'):
                    with runtime.operation(): pytest.fail('admitted after shutdown')
            finally:
                release.set()
            receipt = pending.result(5)
            stop.result(5)
        assert runtime.registry.get_command(command.command_id) == receipt


def test_http_caller_cancel_does_not_undo_accepted_local_command(tmp_path, monkeypatch):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        entered, release, persisted = Event(), Event(), Event()
        original = runtime.commands.create_project
        def work(command):
            entered.set()
            assert release.wait(5)
            result = original(command)
            persisted.set()
            return result
        monkeypatch.setattr(runtime.commands, 'create_project', work)
        command = {'command_id': str(uuid4()), 'name': 'cancelled HTTP caller'}
        async def run():
            app, _ = create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=runtime.settings.origin,
                    headers=owner_headers(runtime)) as client:
                caller = asyncio.create_task(client.post('/api/cloud/v1/projects', json=command))
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    caller.cancel()
                    with pytest.raises(asyncio.CancelledError): await caller
                finally:
                    release.set()
                assert await asyncio.to_thread(persisted.wait, 5)
                result = await client.get('/api/cloud/v1/commands/' + command['command_id'])
                assert result.json()['status'] == 'succeeded'
        asyncio.run(run())
        assert len(runtime.registry.list_projects()) == 1
