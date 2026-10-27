"""Refresh publishes one validated catalog without blocking other operations."""
import asyncio
from contextlib import ExitStack
from threading import Event

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.settings import AppSettings
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profiles import catalog
from test_sandbox_profile_sessions import application


def test_loader_reads_only_sandbox_and_never_contacts_docker(tmp_path, monkeypatch):
    from naiyue_test_agent.sandbox_profiles import load_sandbox_configuration
    monkeypatch.setattr('naiyue_test_agent.execution.docker_cli.DockerCLI.daemon',
        lambda *_: pytest.fail('refresh contacted Docker'))
    path = tmp_path/'settings.toml'
    path.write_text('[storage]\nunrelated_invalid_key=true\n[sandbox]\nimage="fixture:new"\n')
    assert load_sandbox_configuration(path).select().settings.image == 'fixture:new'
    path.write_text('[storage]\nunrelated_invalid_key=true\n')
    assert load_sandbox_configuration(path).profiles == ()


@pytest.mark.parametrize('content', [None, 'not toml', '[sandbox]\ndefault_profile="unknown"\n',
    '[sandbox]\ncpus=-1\n', '[sandbox.profiles.x]\nname="X"\nimage="fixture:x"\n'])
def test_failed_refresh_keeps_last_good_catalog(tmp_path, monkeypatch, content, caplog):
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                before = app.environments._service.configuration
                path = tmp_path/'settings.toml'
                if content is not None: path.write_text(content)
                with pytest.raises(SandboxError, match='env_configuration_invalid'):
                    await app.environments.refresh(path)
                assert app.environments._service.configuration is before
    asyncio.run(scenario())
    assert 'sandbox_refresh_settings_invalid' in caplog.text
    assert 'fixture:' not in caplog.text


def test_refresh_io_error_is_sanitized(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.sandbox_profiles import load_sandbox_configuration
    from pathlib import Path
    def denied(*args, **kwargs): raise PermissionError('secret config contents')
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(SandboxError, match='env_configuration_invalid'):
        load_sandbox_configuration(tmp_path/'settings.toml')
    assert 'PermissionError' in caplog.text and 'secret config contents' not in caplog.text


def test_refreshes_are_serial_but_event_loop_and_reads_remain_available(tmp_path, monkeypatch):
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                entered, release = Event(), Event()
                values = [AppSettings.model_validate({'sandbox': catalog('frontend-tests')}).sandbox,
                    AppSettings().sandbox]
                calls = []
                def load(path):
                    calls.append(path)
                    if len(calls) == 1:
                        entered.set()
                        assert release.wait(10), 'test failed to release refresh'
                    return values[len(calls)-1]
                monkeypatch.setattr('naiyue_test_agent.application.environments.api.load_sandbox_configuration', load)
                first = asyncio.create_task(app.environments.refresh(tmp_path/'settings.toml'))
                second = None
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    second = asyncio.create_task(app.environments.refresh(tmp_path/'settings.toml'))
                    await asyncio.sleep(0)  # Yield to the queued refresh, not a timing assertion.
                    assert len(calls) == 1
                    assert await app.projects.list() == ()
                    assert app.environments._service.configuration.default_profile == 'python-tests'
                finally:
                    release.set()
                    result = await first
                    if second is not None: await second
                assert result['default_profile'] == 'frontend-tests'
                assert app.environments._service.configuration.profiles == ()
                assert len(calls) == 2
    asyncio.run(scenario())


def test_cancelled_refresh_drains_before_next_refresh_can_publish(tmp_path, monkeypatch):
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                entered, release = Event(), Event()
                calls = []
                def load(path):
                    calls.append(path)
                    if len(calls) == 1:
                        entered.set()
                        assert release.wait(10), 'test failed to drain cancelled read'
                        return AppSettings().sandbox
                    return AppSettings.model_validate({'sandbox': catalog('frontend-tests')}).sandbox
                monkeypatch.setattr('naiyue_test_agent.application.environments.api.load_sandbox_configuration', load)
                first = asyncio.create_task(app.environments.refresh(tmp_path/'settings.toml'))
                second = None
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    first.cancel()
                    second = asyncio.create_task(app.environments.refresh(tmp_path/'settings.toml'))
                    await asyncio.sleep(0)
                    assert len(calls) == 1
                finally:
                    release.set()
                    with pytest.raises(asyncio.CancelledError): await first
                    if second is not None: result = await second
                assert result['default_profile'] == 'frontend-tests'
                assert app.environments._service.configuration.default_profile == 'frontend-tests'
    asyncio.run(scenario())
