"""Real module processes and TCP handshake, without any provider requests."""
import asyncio
import json
import subprocess
import sys
from threading import Event, current_thread

import pytest

from naiyue_test_agent.service.launcher import ensure_service
from naiyue_test_agent.service.ownership import read_discovery, service_running
from naiyue_test_agent.service.settings import ServiceError
from test_service_ownership import options


def launch_options(tmp_path, **changes):
    assets = tmp_path / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / 'index.html').write_text('<html>fixture</html>')
    models = {'schema_version': 1, 'default_model_id': 'A',
        'providers': {'local': {'kind': 'openai_compatible', 'base_url': 'https://example.invalid/v1', 'api_key': 'test-only'}},
        'models': {'A': {'provider_id': 'local', 'model': 'test-only', 'api': 'chat_completions'}}}
    (tmp_path / 'models.json').write_text(json.dumps(models))
    (tmp_path / 'settings.toml').write_text('[memory]\nauto_enabled = false\n')
    return options(tmp_path, port=0, **changes)


def test_concurrent_controlled_launch_clients_share_service_and_shutdown_releases_lock(monkeypatch, tmp_path):
    from naiyue_test_agent.service import launcher
    async def scenario():
        config = launch_options(tmp_path)
        children = []
        spawn = launcher._spawn
        def capture(options):
            child = spawn(options)
            children.append(child)
            return child
        monkeypatch.setattr(launcher, '_spawn', capture)
        a = b = None
        try:
            a, b = await asyncio.gather(ensure_service(config), ensure_service(config))
            assert a.instance_id == b.instance_id == read_discovery(config).instance_id
            assert service_running(config)
            assert (await a.request('GET', '/api/v1/scopes/chats/models'))['model_ids'] == ['A']
            await a.aclose()
            assert await b.request('GET', '/api/v1/scopes/chats/sessions') == []
            result = await b.request('POST', '/api/v1/shutdown')
            assert result == {'accepted': True}
            await b.aclose()
            for child in children:
                await asyncio.to_thread(child.wait, timeout=15)
            assert not service_running(config)
            assert read_discovery(config) is None
        finally:
            for client in (a, b):
                if client is not None:
                    await client.aclose()
            for child in children:
                if child.poll() is None:
                    child.kill()
                await asyncio.to_thread(child.wait, timeout=10)
    asyncio.run(scenario())


def test_startup_error_is_bounded_and_releases_owner(tmp_path):
    async def scenario():
        config = launch_options(tmp_path, startup_timeout=10)
        config.settings_path.write_text('bad = [')
        with pytest.raises(ServiceError, match='service_start_failed'):
            await ensure_service(config)
        assert not service_running(config)
        assert read_discovery(config) is None
        assert 'bad = [' not in (config.data_dir / 'service/service.log').read_text()
    asyncio.run(scenario())


def test_service_module_help_does_not_open_runtime(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'naiyue_test_agent.service', '--help'],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert '--data-dir' in result.stdout and '--settings' in result.stdout


def test_spawn_arranges_daemon_reaping_without_stopping_service(monkeypatch, tmp_path):
    from naiyue_test_agent.service import launcher
    started, release, finished = Event(), Event(), Event()
    daemon = []
    class Child:
        def wait(self):
            daemon.append(current_thread().daemon)
            started.set()
            release.wait(10)
            finished.set()
    child = Child()
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: child)
    try:
        assert launcher._spawn(launch_options(tmp_path)) is child
        assert started.wait(5), 'spawned services must be reaped even if their client exits'
        assert daemon == [True]
    finally:
        release.set()
        if started.is_set():
            assert finished.wait(5)


def test_independent_data_profiles_run_at_the_same_time(tmp_path):
    async def scenario():
        one = launch_options(tmp_path)
        two = one.model_copy(update={'data_dir': tmp_path / 'other-data'})
        a = b = None
        try:
            a, b = await asyncio.gather(ensure_service(one), ensure_service(two))
            assert a.instance_id != b.instance_id
            assert service_running(one) and service_running(two)
            base = '/api/v1/scopes/chats/sessions'
            await a.request('POST', base, json={'model_id': 'A'})
            assert len(await a.request('GET', base)) == 1
            assert await b.request('GET', base) == []
        finally:
            for client in (a, b):
                if client is not None:
                    try:
                        await client.request('POST', '/api/v1/shutdown')
                    finally:
                        await client.aclose()
            deadline = asyncio.get_running_loop().time() + 15
            while service_running(one) or service_running(two):
                if asyncio.get_running_loop().time() > deadline:
                    pytest.fail('independent services did not release their locks')
                await asyncio.sleep(0.05)
    asyncio.run(scenario())
