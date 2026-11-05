"""Unified configuration drives the existing consumers without duplicate tunnels."""
import asyncio
import json
from uuid import uuid4
import pytest
from pydantic import ValidationError


def config():
    return {'connections': [{'connection_id': 'dev', 'connection': {
        'endpoint': 'http://127.0.0.1:43121', 'owner_authority': '127.0.0.1:43122',
        'owner_token': 'private-test-token', 'instance_id': str(uuid4())},
        'ssh': {'tunnel': 'owned', 'ssh_host': 'dev-host'},
        'profiles': [{'profile_id': key, 'network_id': key, 'template_ids': ['test'], 'default_template': 'test'} for key in ('alice', 'alice-extra')],
        'remote_update': {'remote_python': '/opt/nyt/python', 'remote_settings': '/opt/nyt/update.json'}}]}


def test_single_book_preserves_profile_ids_and_hides_secrets(tmp_path):
    from naiyue_test_agent.cloud_files.connections import ConnectionBook
    from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    path=tmp_path/'connections.json';path.write_text(json.dumps(config()));path.chmod(0o600)
    manager=ManualCloudTasks(CloudTaskSettings(connections_file=path))
    assert [p['profile_id'] for p in manager.book.public()]==['alice','alice-extra']
    assert manager.book.require('alice-extra').connection_id=='dev'
    assert manager.connection_settings(manager.book.require('alice')).owner_authority=='127.0.0.1:43122'
    assert 'private-test-token' not in json.dumps(manager.book.public())
    book=ConnectionBook.read(path)
    assert book.remote('alice').ssh_host=='dev-host'
    assert book.remote('alice').remote_python=='/opt/nyt/python'


def test_shared_connection_opens_once_for_two_networks(monkeypatch):
    from naiyue_test_agent.cloud_files.connections import ConnectionBook
    from naiyue_test_agent.cloud_files.profiles import ProfileConnections,ConnectedProfile
    opened=[];closed=[]
    # The production lease owner is hashable; use an ordinary object in this double.
    class Opened:
        process=None
        def __init__(self):self.client=object()
        def check(self,expected):pass
        def close(self):closed.append(True)
    def create(profile):opened.append(profile);return Opened()
    monkeypatch.setattr(ConnectedProfile,'open',create)
    async def scenario():
        pool=ProfileConnections(ConnectionBook.model_validate(config()).profile_book())
        first,second=await asyncio.gather(pool.get('alice'),pool.get('alice-extra'))
        assert first is second
        await pool.release(first);await pool.release(second);await pool.close()
    asyncio.run(scenario())
    assert len(opened)==len(closed)==1


def test_ambiguous_settings_and_profiles_are_rejected(tmp_path):
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    from naiyue_test_agent.cloud_files.connections import ConnectionBook
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    with pytest.raises(ValidationError):CloudTaskSettings(connections_file=tmp_path/'c.json',profiles_file=tmp_path/'p.json')
    book=ConnectionBook.model_validate(config())
    with pytest.raises(CloudError,match='cloud_profile_required'):book.select(None)
    raw=config();raw['connections'].append(raw['connections'][0])
    with pytest.raises(ValidationError):ConnectionBook.model_validate(raw)


def test_network_state_path_can_preserve_old_location(tmp_path):
    from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    path=tmp_path/'connections.json';path.write_text(json.dumps(config()));path.chmod(0o600)
    old=tmp_path/'profiles.networks.json'
    manager=ManualCloudTasks(CloudTaskSettings(connections_file=path,network_state_file=old))
    assert manager.networks.path==old


def test_inline_connection_handshake_uses_instance_identity():
    import httpx
    from naiyue_test_agent.cloud_files.connections import ConnectionBook
    from naiyue_test_agent.cloud_files.profiles import ConnectedProfile,REQUIRED
    raw=config();raw['connections'][0]['ssh']['tunnel']='external'
    profile=ConnectionBook.model_validate(raw).profile_book().require('alice')
    def reply(request):
        assert request.headers['authorization']=='Bearer private-test-token'
        return httpx.Response(200,json={'instance_id':profile.connection.instance_id,'protocol':1,'capabilities':list(REQUIRED)})
    with_connection=ConnectedProfile.open(profile,transport=httpx.MockTransport(reply))
    with_connection.close()


def test_cli_connection_and_remote_update_use_the_same_file(tmp_path):
    from naiyue_test_agent.cloud_files.connections import read_connection,read_remote
    path=tmp_path/'connections.json';path.write_text(json.dumps(config()));path.chmod(0o600)
    assert read_connection(path,'alice').owner_authority=='127.0.0.1:43122'
    assert read_remote(path,'alice-extra').ssh_host=='dev-host'


def test_updater_derives_identity_without_creating_a_root(tmp_path):
    from lifecycle_helpers import config as cloud_config
    from test_update_installer import inputs
    from naiyue_test_agent.cloud_install.connection import update_connection
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    settings,_=inputs(tmp_path);cloud=cloud_config(tmp_path)
    path=tmp_path/'cloud.json'
    path.write_text(json.dumps(cloud.model_dump(mode='json')|{'owner_token':cloud.owner_token.get_secret_value()}));path.chmod(0o600)
    settings=settings.model_copy(update={'connection_file':None,'cloud_settings_file':path})
    with pytest.raises(CloudError):update_connection(settings)
    assert not cloud.data_dir.exists()
    with open_cloud_runtime(cloud) as runtime:
        found=update_connection(settings)
        assert found.instance_id==runtime.instance_id
        assert found.owner_token==cloud.owner_token
        assert found.endpoint==cloud.origin


def test_updater_rejects_stale_explicit_connection(tmp_path):
    from test_offline_operations import setup
    from naiyue_test_agent.cloud_install.connection import update_connection
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    settings,_,_,_=setup(tmp_path)
    raw=json.loads(settings.connection_file.read_text());raw['instance_id']=str(uuid4())
    settings.connection_file.write_text(json.dumps(raw))
    with pytest.raises(CloudError,match='cloud_instance_mismatch'):update_connection(settings)


def test_network_state_cannot_replace_connections_or_other_config(tmp_path):
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    for name in ('connections.json','settings.toml','same.networks.json'):
        path=tmp_path/name
        with pytest.raises(ValidationError):CloudTaskSettings(connections_file=path,network_state_file=path)


def test_foreign_json_is_not_treated_as_empty_network_state(tmp_path):
    from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    path=tmp_path/'connections.json';path.write_text(json.dumps(config()));path.chmod(0o600)
    state=tmp_path/'old.networks.json';original=json.dumps(config());state.write_text(original);state.chmod(0o600)
    with pytest.raises(CloudError,match='cloud_network_state_invalid'):
        ManualCloudTasks(CloudTaskSettings(connections_file=path,network_state_file=state))
    assert state.read_text()==original


def test_shared_pool_key_cannot_mix_different_connection_authorities(tmp_path):
    from naiyue_test_agent.cloud_files.profiles import CloudProfile,ProfileBook
    first=CloudProfile(profile_id='a',connection_id='shared',connection_file=tmp_path/'a.json',
        template_ids=['test'],default_template='test')
    second=first.model_copy(update={'profile_id':'b','connection_file':tmp_path/'b.json'})
    with pytest.raises(ValidationError):ProfileBook(profiles=[first,second])


def test_both_profiles_report_the_shared_connected_state(monkeypatch):
    from types import SimpleNamespace
    from naiyue_test_agent.service import cloud_tasks
    from naiyue_test_agent.cloud_files.connections import ConnectionBook
    from naiyue_test_agent.cloud_files.profiles import ProfileConnections
    book=ConnectionBook.model_validate(config()).profile_book();pool=ProfileConnections(book)
    pool.connections['dev']=object()
    monkeypatch.setattr(cloud_tasks,'access',lambda request:SimpleNamespace(book=book,connections=pool))
    values=asyncio.run(cloud_tasks.profiles(None))
    assert [p['connection'] for p in values]==['connected','connected']
    assert [p['connection_id'] for p in book.public()]==['dev','dev']
