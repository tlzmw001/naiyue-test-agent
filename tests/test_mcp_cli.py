"""Installed command routing consumes settings and reports only safe facts."""

import json
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize('mode', ['normal', 'empty', 'invalid'])
def test_cli_real_configuration_to_discovery(tmp_path, capsys, caplog, mode):
    from naiyue_test_agent.cli import main
    fixture = Path(__file__).with_name('mcp_discovery_fixture.py')
    audit = tmp_path / 'audit.jsonl'
    settings = tmp_path / 'settings.toml'
    arguments = [str(fixture), '--audit', str(audit), '--mode', mode]
    settings.write_text('[mcp.servers.local]\nenabled=true\ntransport="stdio"\ncommand=' +
                        json.dumps(sys.executable) + '\nargs=' + json.dumps(arguments) + '\n')
    assert main(['mcp', 'list', '--settings', str(settings)]) == (1 if mode == 'invalid' else 0)
    output = capsys.readouterr()
    rows = json.loads(output.out)['servers']
    assert rows[0]['status'] == ('error' if mode == 'invalid' else 'ok')
    if mode == 'normal':
        assert rows[0]['tools'][0] == {'original_name': 'echo', 'registered_name': 'mcp__local__echo',
                                      'selected': False, 'schema': 'valid'}
    elif mode == 'empty':
        assert rows[0]['tools'] == []
    else:
        assert rows[0]['error'] == 'invalid_schema'
    assert not any(json.loads(line)['event'] == 'call' for line in audit.read_text().splitlines())
    assert 'PRIVATE-STDERR' not in output.out + output.err + caplog.text
    assert str(fixture) not in output.out


def test_missing_explicit_settings_fails(tmp_path, capsys):
    from naiyue_test_agent.cli import main
    assert main(['mcp', 'list', '--settings', str(tmp_path / 'missing')]) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'mcp_settings_invalid'}


def test_all_enabled_servers_reported_after_one_failure(tmp_path, capsys):
    from naiyue_test_agent.cli import main
    path = tmp_path / 'settings.toml'
    path.write_text('[mcp.servers.broken]\nenabled=true\ntransport="stdio"\ncommand="MUST-NOT-EXIST"\n'
                    '[mcp.servers.off]\ntransport="stdio"\ncommand="MUST-NOT-RUN"\n'
                    '[mcp.servers.second]\nenabled=true\ntransport="stdio"\ncommand="ALSO-MUST-NOT-EXIST"\n')
    assert main(['mcp', 'list', '--settings', str(path)]) == 1
    rows = json.loads(capsys.readouterr().out)['servers']
    assert [row['server'] for row in rows] == ['broken', 'second']
    assert all(row['status'] == 'error' for row in rows)
