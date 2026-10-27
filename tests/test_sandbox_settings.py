"""Execution network uncertainty must fail closed, never become external."""
import pytest
from pydantic import ValidationError
from naiyue_test_agent.settings import AppSettings, load_settings


def test_sandbox_defaults_are_lazy_and_network_is_explicit(tmp_path):
    settings = AppSettings()
    assert settings.sandbox.image is None
    assert settings.sandbox.network.mode == 'external'
    path = tmp_path / 'settings.toml'
    path.write_text('[sandbox]\ncontext="local-test"\nimage="app:test"\ncpus=2.0\n[sandbox.network]\nmode="none"\n')
    configured = load_settings(path)
    assert configured.sandbox.context == 'local-test'
    assert configured.sandbox.network.mode == 'none'
    with pytest.raises(ValidationError):
        configured.sandbox.network.mode = 'external'


@pytest.mark.parametrize('text', ['mode="allowlist"', 'mode=1', 'mode="none"\nunknown=true'])
def test_invalid_network_configuration_never_silently_enables_access(tmp_path, text):
    path = tmp_path / 'settings.toml'
    path.write_text('[sandbox.network]\n'+text+'\n')
    with pytest.raises(ValueError):
        load_settings(path)


def test_explicit_docker_path_can_contain_spaces():
    value='C:/Program Files/Docker/docker.exe'
    assert AppSettings.model_validate({'sandbox':{'docker_executable':value}}).sandbox.docker_executable==value


def test_operation_limits_load_from_config_and_remain_frozen(tmp_path):
    path=tmp_path/'settings.toml'
    path.write_text('[sandbox]\nprocess_output_bytes=65536\ntotal_output_bytes=131072\n'
        'max_operation_records=5\nmax_active_operations=2\npoll_milliseconds=250\n')
    policy=load_settings(path).sandbox
    assert (policy.process_output_bytes,policy.total_output_bytes,policy.max_operation_records,
        policy.max_active_operations,policy.poll_milliseconds)==(65536,131072,5,2,250)
    with pytest.raises(ValidationError):policy.max_active_operations=3


@pytest.mark.parametrize('key,value',[('process_output_bytes',65535),('total_output_bytes',65535),
    ('max_operation_records',0),('max_active_operations',0),('poll_milliseconds',19),('poll_milliseconds','100')])
def test_invalid_operation_limits_reject_before_docker_io(key,value):
    with pytest.raises(ValidationError):AppSettings.model_validate({'sandbox':{key:value}})
