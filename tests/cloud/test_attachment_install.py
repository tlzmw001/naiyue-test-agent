"""Independent batch publication never merges into an existing workspace tree."""
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.cloud_files.capture import capture


def fixture(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'.env').write_text('selected')
    captured=capture(source,{'schema':3,'paths':['.env']},tmp_path/'package',InputLimits())
    workspace=tmp_path/'workspace';root=workspace/'project';root.mkdir(parents=True)
    state=tmp_path/'state';state.mkdir()
    return captured,root,state


def test_batch_install_is_independent_and_repeat_does_not_overwrite_agent_work(tmp_path):
    from naiyue_test_agent.cloud_runner.attachment_install import install
    package,root,state=fixture(tmp_path);aid=str(uuid4())
    relative=install(package.directory,root,state,aid,package.input_id,InputLimits())
    assert relative=='.nyt-input/'+aid
    target=root/relative/'.env';assert target.read_text()=='selected'
    target.write_text('agent changed')
    assert install(package.directory,root,state,aid,package.input_id,InputLimits())==relative
    assert target.read_text()=='agent changed'
    another=str(uuid4());install(package.directory,root,state,another,package.input_id,InputLimits())
    assert (root/'.nyt-input'/another/'.env').read_text()=='selected'
    assert target.read_text()=='agent changed'


@pytest.mark.parametrize('replace',['parent','batch'])
def test_batch_install_rejects_existing_or_redirected_destination(tmp_path,replace):
    from naiyue_test_agent.cloud_runner.attachment_install import install
    package,root,state=fixture(tmp_path);aid=str(uuid4());outside=tmp_path/'outside';outside.mkdir()
    if replace=='parent':(root/'.nyt-input').symlink_to(outside,target_is_directory=True)
    else:
        (root/'.nyt-input').mkdir();(root/'.nyt-input'/aid).mkdir()
    with pytest.raises(CloudError):install(package.directory,root,state,aid,package.input_id,InputLimits())
    assert list(outside.iterdir())==[]


def test_replaced_installed_directory_is_not_trusted_on_retry(tmp_path):
    from naiyue_test_agent.cloud_runner.attachment_install import install
    package,root,state=fixture(tmp_path);aid=str(uuid4())
    relative=install(package.directory,root,state,aid,package.input_id,InputLimits())
    (root/relative).rename(root/'moved');(root/relative).mkdir()
    with pytest.raises(CloudError,match='attachment_install_identity'):
        install(package.directory,root,state,aid,package.input_id,InputLimits())
    assert list((root/relative).iterdir())==[]


def test_retry_after_publication_does_not_merge_or_repeat_writes(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_runner.attachment_install as module
    package,root,state=fixture(tmp_path);aid=str(uuid4());original=module.write_json
    def crash(path,value):
        if value.get('phase')=='published':raise OSError('simulated crash after rename')
        return original(path,value)
    monkeypatch.setattr(module,'write_json',crash)
    with pytest.raises(CloudError,match='attachment_install_failed'):
        module.install(package.directory,root,state,aid,package.input_id,InputLimits())
    assert (root/'.nyt-input'/aid/'.env').read_text()=='selected'
    monkeypatch.setattr(module,'write_json',original)
    assert module.install(package.directory,root,state,aid,package.input_id,InputLimits())=='.nyt-input/'+aid


def test_parent_replacement_during_publication_cannot_redirect_files(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_runner.attachment_install as module
    package,root,state=fixture(tmp_path);aid=str(uuid4());outside=tmp_path/'outside';outside.mkdir()
    original=module.rename_new
    def replace_parent(*args,**kwargs):
        (root/'.nyt-input').rename(root/'moved-parent')
        (root/'.nyt-input').symlink_to(outside,target_is_directory=True)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'rename_new',replace_parent)
    with pytest.raises(CloudError):module.install(package.directory,root,state,aid,package.input_id,InputLimits())
    assert list(outside.iterdir())==[]


def test_unpack_checks_uncompressed_disk_need_before_writing(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from naiyue_test_agent.cloud_runner import attachment_install
    package,root,state=fixture(tmp_path);aid=str(uuid4())
    monkeypatch.setattr(attachment_install.shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    with pytest.raises(CloudError,match='attachment_disk_capacity'):
        attachment_install.install(package.directory,root,state,aid,package.input_id,InputLimits())
    assert not (root/'.nyt-input'/aid).exists()
    assert not list(root.parent.glob('.nyt-attachment-*'))
