"""Bounded terminal snapshots and exact receipts do not own logs or model runs."""

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from naiyue_test_agent.application.background_tasks import BackgroundProcessManager
from naiyue_test_agent.shell_task_settings import ShellTaskSettings
from naiyue_test_agent.tools.processes import ProcessOutcome
from test_background_tasks import submit


async def completed(manager, root, *, count=1, sid='s', prefix='c'):
    class Runner:
        async def run(self, request, consume, control):
            request.on_started()
            await consume('stdout', b'PRIVATE_STDOUT')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    identities = []
    for i in range(count):
        row = await submit(manager, root, 'PRIVATE_COMMAND', sid=sid, call=prefix+str(i), runner=Runner())
        await manager._entries[row['task_id']].worker
        identities.append(row['task_id'])
    return identities


def test_default_snapshot_is_immutable_scoped_and_has_no_command_or_output(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        try:
            ids = await completed(manager, tmp_path, count=2)
            other = await completed(manager, tmp_path, sid='other', prefix='other')
            snap = manager.notice_snapshot(session_id='s', workspace_root=tmp_path, run_id='run')
            assert len(snap.versions) == 2 and {key for key, _ in snap.versions} == set(ids)
            assert all(identity in snap.text for identity in ids)
            assert other[0] not in snap.text and 'PRIVATE' not in snap.text
            assert 'exit_code' in snap.text and 'task_output' in snap.text
            with pytest.raises(FrozenInstanceError):
                snap.text = 'changed'
            assert manager.notice_snapshot(session_id='s', workspace_root=tmp_path, run_id='run') == snap
            assert manager.notice_snapshot(session_id='missing', workspace_root=tmp_path).text == ''
            assert manager.notice_snapshot(session_id='s', workspace_root=tmp_path / 'other').text == ''
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_configured_item_limit_and_exact_ack_keep_later_terminal_versions(tmp_path):
    async def scenario():
        manager = BackgroundProcessManager(ShellTaskSettings(max_notice_items=1))
        try:
            first, second = await completed(manager, tmp_path, count=2)
            snap = manager.notice_snapshot(session_id='s', workspace_root=tmp_path, run_id='r')
            assert [key for key, _ in snap.versions] == [first]
            third, = await completed(manager, tmp_path, prefix='new')
            with pytest.raises(ValueError, match='scope'):
                manager.acknowledge_notices(snap, session_id='other', workspace_root=tmp_path, run_id='r')
            wrong = replace(snap, versions=((first, snap.versions[0][1] + 1),))
            manager.acknowledge_notices(wrong, session_id='s', workspace_root=tmp_path, run_id='r')
            assert manager.notice_snapshot(session_id='s', workspace_root=tmp_path).versions == snap.versions
            manager.acknowledge_notices(snap, session_id='s', workspace_root=tmp_path, run_id='r')
            manager.acknowledge_notices(snap, session_id='s', workspace_root=tmp_path, run_id='r')
            next_one = manager.notice_snapshot(session_id='s', workspace_root=tmp_path, run_id='next')
            assert [key for key, _ in next_one.versions] == [second]
            manager.acknowledge_notices(next_one, session_id='s', workspace_root=tmp_path, run_id='next')
            assert manager.notice_snapshot(session_id='s', workspace_root=tmp_path).versions[0][0] == third
            assert len((await manager.list())['items']) == 3  # Receipts never delete task records.
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_complete_utf8_segment_obeys_configured_byte_limit_and_keeps_remainder(tmp_path, caplog):
    async def scenario():
        first = BackgroundProcessManager(ShellTaskSettings(max_notice_items=1))
        await completed(first, tmp_path)
        size = len(first.notice_snapshot(session_id='s', workspace_root=tmp_path).text.encode('utf-8'))
        await first.aclose()
        manager = BackgroundProcessManager(ShellTaskSettings(max_notice_items=20, max_notice_bytes=size))
        tiny = BackgroundProcessManager(ShellTaskSettings(max_notice_bytes=1))
        try:
            ids = await completed(manager, tmp_path, count=2)
            snap = manager.notice_snapshot(session_id='s', workspace_root=tmp_path, run_id='r')
            assert len(snap.versions) == 1 and len(snap.text.encode('utf-8')) == size
            assert ids[0] in snap.text and ids[1] not in snap.text
            await completed(tiny, tmp_path, prefix='tiny')
            empty = tiny.notice_snapshot(session_id='s', workspace_root=tmp_path)
            assert empty.text == '' and empty.versions == ()
            assert 'background_notice_budget_too_small' in caplog.text
            assert tiny._pending_notices  # An unsent item is not consumed.
        finally:
            await manager.aclose()
            await tiny.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['max_notice_items', 'max_notice_bytes'])
@pytest.mark.parametrize('entry', ['0', '-1', '1.5', 'true', '"20"'])
def test_notice_configuration_invalid_values_prevent_startup(tmp_path, field, entry):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text(f'[shell_tasks]\n{field}={entry}')
    with pytest.raises(ValueError, match='Shell task settings are invalid'):
        load_settings(path)


def test_notice_configuration_defaults_and_real_loader(tmp_path):
    from naiyue_test_agent.settings import AppSettings, load_settings
    assert AppSettings().shell_tasks.max_notice_items == 20
    assert AppSettings().shell_tasks.max_notice_bytes == 8192
    path = tmp_path / 'settings.toml'
    path.write_text('[shell_tasks]\nmax_notice_items=3\nmax_notice_bytes=2048')
    settings = load_settings(path)
    assert settings.shell_tasks.max_notice_items == 3 and settings.shell_tasks.max_notice_bytes == 2048


def test_notices_do_not_read_logs_and_forget_session_removes_pending(monkeypatch, tmp_path):
    async def scenario():
        manager = BackgroundProcessManager()
        identities = await completed(manager, tmp_path)
        def forbidden():
            raise AssertionError('Notice preparation must not read logs')
        monkeypatch.setattr(manager._entries[identities[0]].capture, 'snapshot', forbidden)
        snapshot = manager.notice_snapshot(session_id='s', workspace_root=tmp_path, run_id='r')
        assert snapshot.versions
        manager.forget_session('s')
        assert manager.notice_snapshot(session_id='s', workspace_root=tmp_path).versions == ()
        assert not manager._pending_notices
        manager.acknowledge_notices(snapshot, session_id='s', workspace_root=tmp_path, run_id='r')
        await manager.aclose()
    asyncio.run(scenario())
