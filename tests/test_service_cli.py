"""User controls for an existing shared service and its private log."""

import asyncio
import json
import time

from naiyue_test_agent.cli import main
from naiyue_test_agent.service.launcher import ensure_service
from naiyue_test_agent.service.ownership import service_running
from test_service_startup import launch_options


def test_stop_without_service_does_not_launch_one(tmp_path, capsys):
    config = launch_options(tmp_path)
    config.settings_path.write_text('[storage]\ndata_dir=' + json.dumps(str(config.data_dir)) + '\n')
    assert main(['service', 'stop', '--settings', str(config.settings_path), '--config', str(config.config_path)]) == 1
    assert 'service_unavailable' in capsys.readouterr().out
    assert not config.data_dir.exists()


def test_stop_authenticates_original_service_and_waits_for_shutdown(tmp_path, capsys):
    config = launch_options(tmp_path)
    config.settings_path.write_text('[storage]\ndata_dir=' + json.dumps(str(config.data_dir)) + '\n')
    async def start():
        client = await ensure_service(config)
        await client.aclose()
    asyncio.run(start())
    try:
        assert service_running(config)
        wrong = tmp_path / 'other-models.json'
        assert main(['service', 'stop', '--settings', str(config.settings_path), '--config', str(wrong)]) == 1
        assert 'service_config_mismatch' in capsys.readouterr().out
        assert service_running(config)
        config.settings_path.write_text('[storage]\ndata_dir=' + json.dumps(str(config.data_dir)) + '\n[network]\nmode=42\n')
        assert main(['service', 'stop', '--settings', str(config.settings_path), '--config', str(config.config_path)]) == 0
        assert '已接受' in capsys.readouterr().out
        deadline = time.monotonic() + 15
        while service_running(config) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not service_running(config)
    finally:
        if service_running(config):
            async def stop():
                client = await ensure_service(config)
                try:
                    await client.request('POST', '/api/v1/shutdown')
                finally:
                    await client.aclose()
            asyncio.run(stop())


def test_logs_reads_last_lines_and_follow_interrupt_only_exits_viewer(tmp_path, capsys, monkeypatch):
    config = launch_options(tmp_path)
    config.settings_path.write_text('[storage]\ndata_dir=' + json.dumps(str(config.data_dir)) + '\n')
    log = config.data_dir / 'service/service.log'
    log.parent.mkdir(parents=True)
    log.write_text(''.join(f'line {index}\n' for index in range(130)))
    assert main(['service', 'logs', '--settings', str(config.settings_path)]) == 0
    output = capsys.readouterr().out
    assert 'line 29\n' not in output and 'line 30\n' in output and 'line 129\n' in output
    config.settings_path.write_text('[storage]\ndata_dir=' + json.dumps(str(config.data_dir)) + '\n[network]\nmode=42\n')
    from naiyue_test_agent.cli import service as command
    def interrupt(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(command.time, 'sleep', interrupt)
    assert main(['service', 'logs', '--settings', str(config.settings_path), '--follow']) == 130
    assert 'line 129\n' in capsys.readouterr().out
    assert not service_running(config)


def test_logs_does_not_create_missing_file_or_follow_symlink(tmp_path, capsys):
    config = launch_options(tmp_path)
    config.settings_path.write_text('[storage]\ndata_dir=' + json.dumps(str(config.data_dir)) + '\n')
    log = config.data_dir / 'service/service.log'
    assert main(['service', 'logs', '--settings', str(config.settings_path)]) == 1
    assert 'service_log_unavailable' in capsys.readouterr().out
    assert not log.exists()
    log.parent.mkdir(parents=True)
    log.symlink_to(tmp_path / 'private')
    (tmp_path / 'private').write_text('PRIVATE_CONTENT')
    assert main(['service', 'logs', '--settings', str(config.settings_path)]) == 1
    assert 'PRIVATE_CONTENT' not in capsys.readouterr().out
