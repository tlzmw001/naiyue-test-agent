"""Startup, shutdown, disk faults and maintenance cannot lose process custody."""

import asyncio
import json
import os

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.application.sessions.background_tasks import BackgroundProcessManager
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessCleanupError
from naiyue_test_agent.tools.settings import BashSettings
from test_async_application import StreamingToolModel, call
from test_background_tasks import submit, wait_status, archive_root
from test_background_tools import background_app, command, tool_result


@pytest.mark.parametrize('action', ['stop', 'close'])
def test_startup_in_flight_is_owned_until_handle_and_cleanup_settle(monkeypatch, tmp_path, action):
    async def scenario():
        loop = asyncio.get_running_loop()
        original_spawn, original_settle = loop.subprocess_exec, HostProcessRunner._settle
        created, release, cleaning = (asyncio.Event() for _ in range(3))
        transports = []
        async def delayed(*args, **kwargs):
            result = await original_spawn(*args, **kwargs)
            transports.append(result[0])
            created.set()
            await release.wait()
            return result
        async def settle(self, *args, **kwargs):
            cleaning.set()
            return await original_settle(self, *args, **kwargs)
        monkeypatch.setattr(loop, 'subprocess_exec', delayed)
        monkeypatch.setattr(HostProcessRunner, '_settle', settle)
        manager = BackgroundProcessManager()
        operation = None
        try:
            row = await submit(manager, tmp_path, 'import time;time.sleep(60)')
            await asyncio.wait_for(created.wait(), 5)
            operation = asyncio.create_task(manager.stop(row['task_id']) if action == 'stop' else manager.aclose())
            await asyncio.wait_for(cleaning.wait(), 5)
            assert not operation.done()
            assert (await manager.output(row['task_id']))['status'] == 'stopping'
            release.set()
            await asyncio.wait_for(operation, 5)
            result = await manager.output(row['task_id'])
            assert result['status'] == 'finished' and result['cleanup_confirmed']
            assert result['termination'] == ('user_stop' if action == 'stop' else 'app_close')
            with pytest.raises(ProcessLookupError):
                os.kill(transports[0].get_pid(), 0)
        finally:
            release.set()
            if operation:
                await asyncio.gather(operation, return_exceptions=True)
            await manager.aclose()
    asyncio.run(scenario())


def test_archive_failure_stops_task_without_logging_private_error(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.tools import output
    def failed(*args):
        raise OSError('PRIVATE_DISK_MESSAGE')
    monkeypatch.setattr(output, '_write_owned_file', failed)
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            row = await submit(manager, tmp_path, "import time;print('early',flush=True);time.sleep(60)")
            result = await wait_status(manager, row['task_id'], lambda r: r['status'] == 'finished')
            assert result['termination'] == 'archive_error' and result['cleanup_confirmed']
            assert 'early' in result['output'] and not result['archive_complete']
            assert result['archive_reason'] == 'directory_unavailable'
        finally:
            await manager.aclose()
    asyncio.run(scenario())
    assert 'shell_archive_failed' in caplog.text and 'PRIVATE_DISK_MESSAGE' not in caplog.text


def test_post_exit_text_view_quota_does_not_rewrite_known_exit(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            row = await submit(manager, tmp_path, "import os;os.write(1,b'\\x1b[31m123456\\x1b[m')",
                settings=BashSettings(max_archive_bytes=17))
            result = await wait_status(manager, row['task_id'], lambda r: r['status'] == 'finished')
            assert result['termination'] == 'exited' and result['exit_code'] == 0
            assert result['cleanup_confirmed'] and not result['archive_complete']
            assert result['archive_reason'] == 'command_limit'
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_unconfirmed_cleanup_retains_writer_and_blocks_maintenance(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        release = asyncio.Event()
        failure = ProcessCleanupError(facts={'consumer_settled': False})
        class Runner:
            async def run(self, req, consume, control=None):
                async def writer():
                    await release.wait()
                    await consume('stdout', b'late')
                failure.consumer_task = asyncio.create_task(writer())
                raise failure
        row = await submit(manager, tmp_path, '', runner=Runner())
        result = await wait_status(manager, row['task_id'], lambda r: r['status'] == 'lost')
        assert not result['cleanup_confirmed'] and not manager._entries[row['task_id']].capture.closed
        with pytest.raises(ValueError, match='unconfirmed'):
            with manager.output_guard(archive_root(tmp_path)):
                pytest.fail('Unsettled writer must reserve its output')
        with pytest.raises(ToolException, match='unconfirmed'):
            with manager.deletion_guard('s'):
                pytest.fail('Unconfirmed process cannot be forgotten')
        with pytest.raises(ProcessCleanupError):
            await manager.aclose()
        release.set()
        await failure.consumer_task
        async def finished():
            while not manager._entries[row['task_id']].capture.closed:
                await asyncio.sleep(.01)
        await asyncio.wait_for(finished(), 5)
        assert (await manager.output(row['task_id']))['archive_saved_bytes'] == 4
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['clear', 'delete'])
def test_application_maintenance_refuses_active_tasks_then_preserves_truth(monkeypatch, tmp_path, action):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command("import time;print('saved',flush=True);time.sleep(60)"), 'background': True}), AIMessage(content='started')])
        async with background_app(monkeypatch, tmp_path, model, persistent=True) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            result = await app.host.runs.start(sid, [('user', 'start')])
            identity = json.loads(tool_result(result, 'bash').content)['task_id']
            ready = await wait_status(app.host.sessions._sessions.background_tasks, identity, lambda r: bool(r['output']))
            with pytest.raises(ValueError, match='active'):
                await app.host.tasks.preview_outputs(tmp_path)
            with pytest.raises(SessionPersistenceError, match='background'):
                await app.host.sessions.delete(sid)
            await app.host.tasks.stop(identity)
            if action == 'clear':
                plan = await app.host.tasks.preview_outputs(tmp_path)
                assert plan.file_count == 1 and plan.total_bytes > 0
                assert await app.host.tasks.clear_outputs(plan) == (plan.file_count, plan.total_bytes)
                missing = await app.host.tasks.output(identity)
                assert missing['output_unavailable'] and missing['termination'] == 'user_stop'
                assert missing['cleanup_confirmed'] and missing['output'] == ready['output']
            else:
                await app.host.sessions.delete(sid)
                assert (await app.host.tasks.list())['items'] == []
                assert all((tmp_path / path).exists() for path in ready['archive_paths'].values())
    asyncio.run(scenario())


def test_save_failure_does_not_cancel_or_replay_handed_off_process(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('bash', {'command': command('import time;time.sleep(60)'), 'background': True}), AIMessage(content='started')])
        async with background_app(monkeypatch, tmp_path, model, persistent=True) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            async def failed(*args, **kwargs):
                raise SessionPersistenceError('session_save_unconfirmed')
            monkeypatch.setattr(app.host.sessions._persistence, 'afinish', failed)
            with pytest.raises(SessionPersistenceError, match='save_unconfirmed'):
                await app.host.runs.start(sid, [('user', 'start')])
            rows = (await app.host.tasks.list())['items']
            assert len(rows) == 1 and rows[0]['status'] in ('starting', 'running')
            stopped = await app.host.tasks.stop(rows[0]['task_id'])
            assert stopped['cleanup_confirmed'] and len(model.seen) == 2
    asyncio.run(scenario())


def test_maintenance_reservation_closes_background_admission(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        for guard in (manager.output_guard(archive_root(tmp_path)), manager.deletion_guard('s')):
            with guard, pytest.raises(ToolException, match='admission'):
                await submit(manager, tmp_path, "print('must not run')")
        assert (await manager.list())['items'] == []
        await manager.aclose()
    asyncio.run(scenario())


def test_writer_close_failure_cannot_claim_cleanup_confirmed(monkeypatch, tmp_path):
    from naiyue_test_agent.tools.output import ShellOutputArchive
    original = ShellOutputArchive.finish
    class BrokenHandle:
        closed = False
        def close(self):
            raise OSError('PRIVATE_CLOSE_ERROR')
    def finish(self, **kwargs):
        self._handles['failed-close'] = BrokenHandle()
        return original(self, **kwargs)
    monkeypatch.setattr(ShellOutputArchive, 'finish', finish)
    async def scenario():
        manager = BackgroundProcessManager()
        row = await submit(manager, tmp_path, "print('data')")
        result = await wait_status(manager, row['task_id'], lambda r: r['status'] in ('finished', 'lost'))
        assert result['status'] == 'lost' and not result['cleanup_confirmed']
        assert result['exit_code'] == 0 and result['archive_reason'] == 'write_error'
        with pytest.raises(ProcessCleanupError):
            await manager.aclose()
    asyncio.run(scenario())


def test_cleanup_guard_releases_reservation_on_failure_and_blocks_new_runs(monkeypatch, tmp_path):
    from naiyue_test_agent.application.errors import SessionBusyError
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='next run works')])
        async with background_app(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            with pytest.raises(RuntimeError, match='maintenance failed'):
                with app.host.sessions._sessions.output_guard(app.host.sessions._sessions.workspace_archive_root(tmp_path)):
                    with pytest.raises(SessionBusyError):
                        await app.host.runs.start(sid, [('user', 'cannot race cleanup')])
                    assert model.seen == []
                    raise RuntimeError('maintenance failed')
            assert not app.host.sessions._sessions.background_tasks.is_cleaning(app.host.sessions._sessions.workspace_archive_root(tmp_path))
            result = await app.host.runs.start(sid, [('user', 'after cleanup')])
            assert result['messages'][-1].content == 'next run works'
    asyncio.run(scenario())
