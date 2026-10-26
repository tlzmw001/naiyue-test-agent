"""Safety gates for the disposable real-Docker profile acceptance command."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from scripts.verify_sandbox_profiles import checked_images, destroy_owned


def test_two_tags_for_the_same_image_are_not_two_development_environments():
    cli=SimpleNamespace(json=lambda _: [dict(Id='same',Os='linux')])
    with pytest.raises(ValueError,match='different image IDs'):checked_images(cli,['python','node'])


@pytest.mark.parametrize('image',[dict(Id='',Os='linux'),dict(Id='node',Os='windows')])
def test_acceptance_rejects_missing_or_unsupported_image_identity(image):
    cli=SimpleNamespace(json=lambda _:[image])
    with pytest.raises(ValueError):checked_images(cli,['python','node'])


def test_cleanup_checks_all_project_ownership_before_any_mutation():
    api=SimpleNamespace(management=AsyncMock(return_value=dict(items=[
        dict(binding_id='mine',project_id='P'),dict(binding_id='other',project_id='Q')])),manage=AsyncMock())
    with pytest.raises(ValueError,match='foreign project'):asyncio.run(destroy_owned(api,{'P'}))
    api.manage.assert_not_called()


def test_cleanup_attempts_all_owned_bindings_and_reports_the_first_failure(caplog):
    original=OSError('private transport details')
    api=SimpleNamespace(management=AsyncMock(return_value=dict(items=[
        dict(binding_id='one',project_id='P'),dict(binding_id='two',project_id='P')])),
        manage=AsyncMock(side_effect=[original,dict(lifecycle='destroyed')]))
    with pytest.raises(OSError) as caught:asyncio.run(destroy_owned(api,{'P'}))
    assert caught.value is original
    assert [call.args for call in api.manage.call_args_list]==[('one','destroy'),('two','destroy')]
    assert all(call.kwargs==dict(confirm_destroy=True) for call in api.manage.call_args_list)
    assert 'sandbox_profiles_cleanup_failed' in caplog.text and 'private transport details' not in caplog.text


def test_cleanup_application_uses_a_saved_configuration_when_the_file_is_invalid(tmp_path,monkeypatch):
    from scripts import verify_sandbox_profiles as gate
    from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
    saved=SandboxConfiguration.model_validate(dict(context='isolated',image='fixture:python'))
    (tmp_path/'settings.toml').write_text('invalid TOML')
    monkeypatch.setattr('naiyue_test_agent.application.service_application.open_service_application',lambda *a,**k:k['settings'])
    settings=gate.application(tmp_path,saved)
    assert settings.sandbox==saved and settings.storage.data_dir==tmp_path/'data'


def test_failed_acceptance_keeps_its_registration_directory_for_recovery(tmp_path,monkeypatch,caplog):
    from scripts import verify_sandbox_profiles as gate
    recovery=tmp_path/'recovery';recovery.mkdir()
    failure=AssertionError('injected gate failure')
    async def fail(*args):raise failure
    monkeypatch.setattr(gate,'scenario',fail)
    monkeypatch.setattr(gate.tempfile,'mkdtemp',lambda *args,**kwargs:str(recovery))
    monkeypatch.setattr('sys.argv',['verify_sandbox_profiles','--context','isolated','--python-image','P',
        '--node-image','N','--output',str(tmp_path/'report.json')])
    with pytest.raises(AssertionError) as caught:gate.main()
    assert caught.value is failure and recovery.is_dir()
    assert 'sandbox_profiles_recovery_directory' in caplog.text


def test_runtime_checks_reject_missing_commands_even_if_the_final_command_succeeds():
    from scripts import verify_sandbox_profiles as gate
    with pytest.raises(ValueError):
        gate.checked_runtime('exit_code=0; observed_lines=2\noutput:\nnode: not found\nnpm: not found',('NODE','NPM'))
    assert gate.checked_runtime('exit_code=0; observed_lines=2\noutput:\nNYT_NODE=v22.1.0\nNYT_NPM=10.1.0\n',('NODE','NPM'))==dict(NODE='v22.1.0',NPM='10.1.0')
