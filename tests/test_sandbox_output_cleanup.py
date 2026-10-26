"""Project archive cleanup reserves sandbox writers as well as host tasks."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_sessions import application
from naiyue_test_agent.contracts.sandbox import SandboxError


@pytest.mark.parametrize('state,stale,observed', [
    ('running', False, 0), ('unknown', True, 0), ('completed', False, 5),
])
def test_idle_host_cannot_clear_unsettled_sandbox_archive(tmp_path, monkeypatch, state, stale, observed):
    root = repository(tmp_path)

    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                await host.sessions.create('fake')
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                await host.runs.start(sid, [('user', 'done')])
                plan = await host.tasks.preview_outputs(root)
                operations = app.environments._operations
                binding = (await host.sessions.status(sid))['execution']['binding_id']
                ident = 'd' * 32
                row, _ = operations.records().reserve(binding, project.project_id, sid, ident, 'shell', {},
                    dict(offset=0, saved_bytes=0, max_archive_bytes=1024, tail_limit=128))
                archive = operations._archive(row)
                metadata = archive.prepare(row['metadata'])
                operations.records().save(binding, ident, dict(
                    state=state, stale=stale, observed_bytes=observed, metadata=metadata))
                try:
                    with pytest.raises(ValueError, match='sandbox'):
                        await host.tasks.preview_outputs(root)
                    with pytest.raises(ValueError, match='sandbox'):
                        await host.tasks.clear_outputs(plan)
                    assert Path(metadata['archive_path']).exists()
                    updated = archive.append(metadata, dict(offset=0, next_offset=5, observed_bytes=5, data=b'alive'))
                    assert updated['saved_bytes'] == 5
                    assert operations.records().get(binding, ident)['state'] == state
                finally:
                    operations.records().save(binding, ident, dict(state='completed', stale=False,
                        observed_bytes=0, metadata=metadata))
    asyncio.run(scenario())


def test_cleanup_reservation_rejects_new_sandbox_submission_until_released(tmp_path, monkeypatch):
    root = repository(tmp_path)

    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                await host.sessions.create('fake')
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                binding = (await host.sessions.status(sid))['execution']['binding_id']
                operations = app.environments._operations
                plan = await host.tasks.preview_outputs(root)
                entered, release = Event(), Event()
                from naiyue_test_agent.tools import output
                original = output.clear_shell_outputs

                def blocked_clear(value):
                    entered.set()
                    assert release.wait(10)
                    return original(value)

                monkeypatch.setattr(output, 'clear_shell_outputs', blocked_clear)
                clearing = asyncio.create_task(host.tasks.clear_outputs(plan))
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    with pytest.raises(SandboxError, match='env_output_busy'):
                        await asyncio.to_thread(operations.submit, binding, 'e' * 32, 'shell', {}, {})
                    assert not any(row['operation_id'] == 'e' * 32 for row in operations.records().list(binding))
                finally:
                    release.set()
                    await clearing
                with operations.output_guard(project.project_id):
                    pass
    asyncio.run(scenario())
