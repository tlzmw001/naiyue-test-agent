"""Private profiles have explicit identity and own only their SSH child."""
import json
from uuid import uuid4
import httpx
import pytest
from pydantic import ValidationError


def connection():
    return dict(endpoint='http://127.0.0.1:43121',owner_authority='127.0.0.1:43122',
        owner_token='private-test-token',instance_id=str(uuid4()))


def test_private_profile_lists_only_public_fields_and_checks_handshake(tmp_path):
    from naiyue_test_agent.cloud_files.profiles import ProfileBook,ConnectedProfile
    raw=connection();path=tmp_path/'connection.json';path.write_text(json.dumps(raw));path.chmod(0o600)
    config=tmp_path/'profiles.json';config.write_text(json.dumps({'profiles':[dict(profile_id='work',connection_file=str(path),
        template_ids=['test'],default_template='test')]}));config.chmod(0o600)
    book=ProfileBook.read(config);profile=book.require('work')
    text=json.dumps(book.public())
    assert 'private-test-token' not in text and str(path) not in text and '43121' not in text
    def reply(request):return httpx.Response(200,json=dict(instance_id=raw['instance_id'],protocol=1,
        capabilities=['projects:create','commands:read','tasks:create','tasks:read','inputs:import','environment:commands']))
    conn=ConnectedProfile.open(profile,transport=httpx.MockTransport(reply))
    assert conn.handshake['instance_id']==raw['instance_id'];conn.close()
    def wrong(request):return httpx.Response(200,json=dict(instance_id=str(uuid4()),protocol=1,capabilities=[]))
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    with pytest.raises(CloudError,match='cloud_instance_mismatch'):
        ConnectedProfile.open(profile,transport=httpx.MockTransport(wrong))


def test_owned_ssh_argv_has_no_shell_and_rejects_unsafe_alias(tmp_path):
    from naiyue_test_agent.cloud_files.profiles import CloudProfile,ssh_argv
    from naiyue_test_agent.cloud_files.client import CloudConnection
    profile=CloudProfile(profile_id='work',connection_file=tmp_path/'connection.json',template_ids=['test'],
        default_template='test',tunnel='owned',ssh_host='my-dev')
    args=ssh_argv(profile,CloudConnection(**connection()))
    assert args[-1]=='my-dev' and 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args
    assert 'ExitOnForwardFailure=yes' in args and '-L' in args and '127.0.0.1:43121:127.0.0.1:43122' in args
    with pytest.raises(ValidationError):profile.model_copy(update={'ssh_host':'-evil'}).__class__.model_validate(profile.model_dump()|{'ssh_host':'-evil'})


def test_dead_owned_tunnel_is_replaced_without_business_replay(tmp_path,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from naiyue_test_agent.cloud_files.profiles import ProfileConnections,ProfileBook,CloudProfile,ConnectedProfile
    profile=CloudProfile(profile_id='work',connection_file=tmp_path/'c.json',template_ids=['test'],default_template='test',
        tunnel='owned',ssh_host='dev')
    created=[]
    class Value:
        def __init__(self):
            self.process=SimpleNamespace(poll=lambda:None);self.client=object();self.checks=[];self.closed=False
        def check(self,expected=None):self.checks.append(expected)
        def close(self):self.closed=True
    def open_one(p):value=Value();created.append(value);return value
    monkeypatch.setattr(ConnectedProfile,'open',open_one)
    async def scenario():
        pool=ProfileConnections(ProfileBook(profiles=[profile]))
        first=await pool.get('work');created[0].process.poll=lambda:255
        second=await pool.get('work')
        assert second is not first and len(created)==2
        await pool.close();assert all(v.closed for v in created)
    asyncio.run(scenario())


def test_handshake_timeout_is_passed_to_the_actual_http_request(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files.profiles import CloudProfile,ConnectedProfile
    from naiyue_test_agent.cloud_files.client import CloudConnection,CloudClient
    profile=CloudProfile(profile_id='work',connection_file=tmp_path/'c.json',template_ids=['test'],default_template='test',connect_timeout_seconds=2.0)
    raw=connection();timeouts=[]
    def handler(request):
        timeouts.append(request.extensions['timeout'])
        return httpx.Response(200,json=dict(instance_id=raw['instance_id'],protocol=1,capabilities=[
            'projects:create','commands:read','tasks:create','tasks:read','inputs:import','environment:commands']))
    client=CloudClient(CloudConnection(**raw),transport=httpx.MockTransport(handler))
    value=ConnectedProfile(profile,client)
    try:value.check()
    finally:value.close()
    assert all(0<t<=2.0 for t in timeouts[0].values())


@pytest.mark.parametrize('broken',[{'protocol':True},{'protocol':2},{'capabilities':[{}]},{'capabilities':[]}])
def test_malformed_handshake_never_opens_mutation_capability(tmp_path,broken):
    from naiyue_test_agent.cloud_files.profiles import CloudProfile,ConnectedProfile,REQUIRED
    from naiyue_test_agent.cloud_files.client import CloudConnection,CloudClient
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    profile=CloudProfile(profile_id='work',connection_file=tmp_path/'c.json',template_ids=['test'],default_template='test')
    raw=connection();seen=[]
    def handler(request):
        seen.append(request.method)
        return httpx.Response(200,json=dict(instance_id=raw['instance_id'],protocol=1,capabilities=list(REQUIRED))|broken)
    value=ConnectedProfile(profile,CloudClient(CloudConnection(**raw),transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(CloudError,match='cloud_protocol_incompatible'):value.check()
        assert seen==['GET']
    finally:value.close()
