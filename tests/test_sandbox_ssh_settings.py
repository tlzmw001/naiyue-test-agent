"""An optional SSH target is frozen without changing legacy local fingerprints."""
import json

import pytest
from pydantic import ValidationError

from naiyue_test_agent.contracts.session_environment import SandboxProfileSelection
from naiyue_test_agent.sandbox_settings import SandboxSettings
from naiyue_test_agent.settings import AppSettings, load_settings


def test_ssh_configuration_loads_without_any_local_docker(tmp_path):
    path = tmp_path/'settings.toml'
    path.write_text('[sandbox]\nimage="fixture:python"\n[sandbox.ssh]\n'
                    'host="company-测试"\nexecutable="C:/Program Files/OpenSSH/ssh.exe"\n'
                    'docker_executable="/usr/bin/docker"\nconnect_timeout_seconds=20\n', encoding='utf-8')
    settings = load_settings(path).sandbox
    assert settings.ssh.host == 'company-测试'
    assert settings.ssh.executable == 'C:/Program Files/OpenSSH/ssh.exe'
    assert settings.ssh.docker_executable == '/usr/bin/docker'
    assert settings.ssh.socket_path == '/var/run/docker.sock'
    assert settings.ssh.connect_timeout_seconds == 20
    assert AppSettings.model_validate({'sandbox': settings.model_dump(mode='json')}).sandbox == settings
    selected = settings.select()
    assert SandboxProfileSelection.model_validate_json(selected.model_dump_json()) == selected
    with pytest.raises(ValidationError):
        selected.settings.ssh.host = 'another'


@pytest.mark.parametrize('ssh', [
    {}, {'host': ''}, {'host': '-oProxyCommand=bad'}, {'host': 'user@host'},
    {'host': 'ssh://host'}, {'host': 'host;bad'}, {'host': 'host name'},
    {'host': 'host\ncommand'}, {'host': 'host', 'password': 'not-supported'},
    {'host': 'host', 'socket_path': 'relative.sock'},
    {'host': 'host', 'socket_path': '/run/../other.sock'},
    {'host': 'host', 'socket_path': '/run/docker.sock\ncommand'},
    {'host': 'host', 'executable': '-ssh'},
    {'host': 'host', 'docker_executable': '-docker'},
    {'host': 'host', 'connect_timeout_seconds': 0},
    {'host': 'host', 'connect_timeout_seconds': '15'},
])
def test_invalid_ssh_is_rejected_before_io(ssh):
    with pytest.raises(ValidationError):
        SandboxSettings.model_validate({'ssh': ssh})


def test_ssh_and_a_nondefault_local_context_cannot_be_combined():
    with pytest.raises(ValidationError):
        SandboxSettings.model_validate({'context': 'local-daemon', 'ssh': {'host': 'company'}})


def test_old_local_selection_keeps_its_actual_pre_ssh_revision():
    selected = SandboxProfileSelection.create('default', 'Local', SandboxSettings(image='fixture:old'))
    assert selected.revision == '0e3e9d2435b55249bef4117bb4bb2fdba43448e6d3703c368a7f53feedf13098'
    assert 'ssh' not in selected.settings.model_dump(mode='json')
    legacy = json.loads(selected.model_dump_json())
    assert SandboxProfileSelection.model_validate(legacy) == selected


def test_ssh_target_participates_in_saved_selection_identity():
    first = SandboxSettings.model_validate({'image': 'fixture:python', 'ssh': {'host': 'first'}})
    second = SandboxSettings.model_validate({'image': 'fixture:python', 'ssh': {'host': 'second'}})
    assert SandboxProfileSelection.create('python', 'Python', first).revision != (
        SandboxProfileSelection.create('python', 'Python', second).revision)
