"""Internal CLI preserves caller command/CAS identities and shares authenticated HTTP."""
import json
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.cli import main
from naiyue_test_agent.cloud_files.client import CloudClient


def test_cli_requests_preserve_commands_and_validate_before_http(tmp_path,monkeypatch,capsys):
    from naiyue_test_agent.cli import cloud_tasks
    settings=tmp_path/'connection.json'
    settings.write_text(json.dumps(dict(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',owner_token='synthetic-owner',instance_id=str(uuid4()))))
    settings.chmod(0o600);seen=[]
    def handler(request):
        assert request.headers['Authorization']=='Bearer synthetic-owner'
        seen.append((request.method,request.url.path,json.loads(request.content) if request.content else None))
        return httpx.Response(200,json={'observed':True})
    monkeypatch.setattr(cloud_tasks,'CloudClient',lambda settings:CloudClient(settings,transport=httpx.MockTransport(handler)))
    ident=str(uuid4());request=tmp_path/'request.json'
    value=dict(command_id=ident,expected_revision=7,expected_generation=2,relative_name='reports/output.bin')
    request.write_text(json.dumps(value));task='a'*32
    for _ in range(2):
        assert main(['cloud','artifact','capture',task,'--request',str(request),'--settings',str(settings)])==0
    assert seen==[('POST',f'/api/cloud/v1/tasks/{task}/artifacts',value)]*2
    assert 'observed' in capsys.readouterr().out
    request.write_text(json.dumps(dict(value,expected_revision=0,message='PRIVATE_SYNTHETIC_VALUE')))
    with pytest.raises(SystemExit):main(['cloud','artifact','capture',task,'--request',str(request),'--settings',str(settings)])
    assert len(seen)==2 and 'PRIVATE_SYNTHETIC_VALUE' not in capsys.readouterr().err
    assert main(['cloud','history',task,'--after-seq','3','--settings',str(settings)])==0
    assert seen[-1][0:2]==('GET',f'/api/cloud/v1/tasks/{task}/history/events')


def test_cli_archive_views_validate_and_forward_query_identity(tmp_path,monkeypatch):
    from naiyue_test_agent.cli import cloud_tasks
    config=tmp_path/'connection.json';config.write_text(json.dumps(dict(endpoint='http://127.0.0.1:28281',
        owner_authority='127.0.0.1:28101',owner_token='synthetic-owner',instance_id=str(uuid4()))));config.chmod(0o600)
    seen=[]
    def handler(request):
        seen.append((request.url.path,dict(request.url.params)))
        return httpx.Response(200,json={})
    monkeypatch.setattr(cloud_tasks,'CloudClient',lambda settings:CloudClient(settings,transport=httpx.MockTransport(handler)))
    base=['cloud','history','a'*32,'--settings',str(config)]
    assert main([*base,'--view','messages','--thread','child','--cursor','opaque','--limit','2'])==0
    assert seen[-1]==('/api/cloud/v1/tasks/'+'a'*32+'/history',dict(thread_id='child',cursor='opaque',limit='2'))
    assert main([*base,'--view','detail','--thread','child','--message-id','msg','--revision','2','--offset','3','--limit','10'])==0
    assert seen[-1][0].endswith('/history/messages/msg') and seen[-1][1]['revision']=='2'
    assert main([*base,'--view','threads'])==0 and seen[-1][0].endswith('/history/threads')
    assert main(['cloud','snapshot','a'*32,'--settings',str(config),'--thread','child'])==0
    assert seen[-1][0].endswith('/snapshot') and seen[-1][1]['thread_id']=='child'
    assert main(['cloud','usage','a'*32,'--settings',str(config),'--after-model','A','--limit','2'])==0
    assert seen[-1][0].endswith('/usage') and seen[-1][1]==dict(after_model='A',limit='2')
    with pytest.raises(SystemExit):main([*base,'--view','detail','--thread','child','--message-id','msg','--revision','0'])
    assert len(seen)==5


@pytest.mark.parametrize('rejection',[True,False])
def test_cli_releases_input_use_only_after_definitive_create_rejection(tmp_path,monkeypatch,rejection):
    from argparse import Namespace
    from naiyue_test_agent.cli import cloud_tasks
    from naiyue_test_agent.contracts.cloud_tasks import CloudError

    config=tmp_path/'connection.json'
    config.write_text(json.dumps(dict(endpoint='http://127.0.0.1:28281',
        owner_authority='127.0.0.1:28101',owner_token='synthetic-owner',instance_id=str(uuid4()))));config.chmod(0o600)
    body=dict(command_id=str(uuid4()),project_id=str(uuid4()),input_id='c'*64,title='test',template_id='test')
    request_file=tmp_path/'request.json';request_file.write_text(json.dumps(body))
    held=set()

    def handler(request):
        if request.url.path.endswith('/input-uses'):
            use=json.loads(request.content);held.add(use['command_id'])
            return httpx.Response(200,json=use|{'state':'held'})
        if request.url.path.endswith('/tasks'):
            assert request.headers['X-Input-Use-Id'] in held
            if rejection:return httpx.Response(409,json={'error':{'code':'template_unavailable'}})
            raise httpx.ReadTimeout('controlled')
        if request.method=='DELETE':
            held.remove(request.url.path.split('/')[-1])
            return httpx.Response(200,json={'state':'released'})
        pytest.fail('unexpected request')

    monkeypatch.setattr(cloud_tasks,'CloudClient',lambda settings:CloudClient(settings,transport=httpx.MockTransport(handler)))
    with pytest.raises(CloudError,match='template_unavailable' if rejection else 'cloud_connection_failed'):
        cloud_tasks.execute(Namespace(command='task',action='create',request=request_file,settings=config))
    assert len(held)==(0 if rejection else 1)


def test_cli_verifies_legacy_cutover_through_owner_endpoint(tmp_path,monkeypatch,capsys):
    from naiyue_test_agent.cli import cloud_tasks

    settings=tmp_path/'connection.json'
    settings.write_text(json.dumps(dict(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',
                                      owner_token='synthetic-owner',instance_id=str(uuid4()))))
    settings.chmod(0o600)
    proof=tmp_path/'proof.json'
    value=dict(command_id=str(uuid4()),sources_verified=True,input_use_ids=[str(uuid4())])
    proof.write_text(json.dumps(value))
    seen=[]
    def handler(request):
        seen.append((request.method,request.url.path,json.loads(request.content)))
        return httpx.Response(200,json={'verified':True})
    monkeypatch.setattr(cloud_tasks,'CloudClient',lambda config:CloudClient(config,transport=httpx.MockTransport(handler)))
    args=['cloud','verify-legacy-inputs','--settings',str(settings),'--request',str(proof)]
    assert main(args)==0
    assert json.loads(capsys.readouterr().out)=={'verified':True}
    assert seen==[('POST','/api/cloud/v1/input-legacy-cutover',value)]
    proof.write_text(json.dumps(value|{'sources_verified':False}))
    with pytest.raises(SystemExit):main(args)
    assert len(seen)==1
