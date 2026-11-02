import json

import pytest

from naiyue_test_agent.cli import main
from naiyue_test_agent.cloud_files.configuration import read_settings
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.settings import CloudSettings
from cloud_helpers import settings


def test_cloud_help_is_installed_without_starting_any_service(capsys):
    with pytest.raises(SystemExit) as caught:
        main(['cloud', '--help'])
    assert caught.value.code == 0
    text = capsys.readouterr().out
    assert 'serve' in text and 'release' in text


def test_cloud_settings_are_explicit_private_and_never_echo_secrets(tmp_path, capsys):
    path = tmp_path / 'settings.json'
    path.write_text(settings(tmp_path).model_dump_json())
    path.chmod(0o644)
    with pytest.raises(CloudError): read_settings(path, CloudSettings)
    data = json.loads(path.read_text())
    data['owner_token'] = 'PRIVATE_OWNER_KEY'
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    assert read_settings(path, CloudSettings).owner_token.get_secret_value() == 'PRIVATE_OWNER_KEY'
    data['unexpected'] = 'PRIVATE_EXTRA'
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit) as caught:
        main(['cloud','serve','--settings',str(path)])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert 'PRIVATE_OWNER_KEY' not in output.err and 'PRIVATE_EXTRA' not in output.err
