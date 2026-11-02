"""Internal control CLI uses explicit owner command/checkpoint identities."""
import json
from uuid import uuid4
import httpx
import pytest
from naiyue_test_agent.cli import main
from naiyue_test_agent.cloud_files.client import CloudClient


@pytest.mark.parametrize('kind',['stop','recover'])
def test_control_cli_preserves_request_and_rejects_wrong_kind_before_network(tmp_path,monkeypatch,kind):
    from naiyue_test_agent.cli import cloud_tasks
    connection=tmp_path/'connection.json'
    connection.write_text(json.dumps(dict(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',owner_token='fixture-only',instance_id=str(uuid4()))));connection.chmod(0o600)
    seen=[]
    def handler(request):
        seen.append((request.method,request.url.path,json.loads(request.content)))
        return httpx.Response(202,json={'status':'running'})
    monkeypatch.setattr(cloud_tasks,'CloudClient',lambda settings:CloudClient(settings,transport=httpx.MockTransport(handler)))
    value=dict(command_id=str(uuid4()),expected_revision=3,expected_generation=2,kind=kind,
        run_id='a'*32,checkpoint_id='checkpoint' if kind=='recover' else None)
    path=tmp_path/'request.json';path.write_text(json.dumps(value));task='b'*32
    for _ in range(2):assert main(['cloud','control',kind,task,'--request',str(path),'--settings',str(connection)])==0
    assert seen==[('POST',f'/api/cloud/v1/tasks/{task}/control',value)]*2
    path.write_text(json.dumps(value|{'kind':'recover' if kind=='stop' else 'stop'}))
    with pytest.raises(SystemExit):main(['cloud','control',kind,task,'--request',str(path),'--settings',str(connection)])
    assert len(seen)==2
