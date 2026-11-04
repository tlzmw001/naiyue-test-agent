import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.cloud_files.client import CloudConnection,CloudClient
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_input_formats import package


@pytest.mark.parametrize('phase',['create','upload','commit'])
def test_client_reuses_transfer_identity_after_ack_loss(tmp_path,phase):
    captured,_,_=package(tmp_path);created=[];tid=str(uuid4());ready=False;lost=False;uploads=[];current=None
    settings=CloudConnection(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',
        owner_token='synthetic-owner',instance_id=str(uuid4()))
    def handler(request):
        nonlocal ready,lost,current
        assert request.headers['host']==settings.owner_authority
        assert request.headers['origin']=='http://'+settings.owner_authority
        if '/inputs/' in request.url.path:
            if ready:return httpx.Response(200,json=json.loads((captured.directory/'input.json').read_text()))
            return httpx.Response(404,json={'error':{'code':'input_not_found'}})
        if request.url.path.endswith('/transfers'):
            payload=json.loads(request.content);created.append(payload['command_id'])
            if current is None:current=dict(payload,transfer_id=tid,state='pending',offset=0)
            if phase=='create' and not lost:
                lost=True;raise httpx.ReadError('controlled create ACK loss',request=request)
            return httpx.Response(201,json={'result':{'transfer':dict(payload,transfer_id=tid,state='pending',offset=0)}})
        if request.method=='GET' and request.url.path.endswith('/transfers/'+tid):return httpx.Response(200,json=current)
        if request.url.path.endswith('/bytes'):
            assert hashlib.sha256(request.content).hexdigest()==captured.archive_sha256
            uploads.append(len(request.content))
            current=dict(current,state='uploaded',offset=captured.size)
            if phase=='upload' and not lost:
                lost=True;raise httpx.ReadError('controlled upload ACK loss',request=request)
            return httpx.Response(200,json=current)
        assert request.url.path.endswith('/commit')
        ready=True
        if phase=='commit' and not lost:
            lost=True;raise httpx.ReadError('controlled commit ACK loss',request=request)
        return httpx.Response(200,json={'input_id':captured.input_id})
    first=CloudClient(settings,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CloudError,match='cloud_connection_failed'):first.upload(captured.directory)
    finally:first.close()
    second=CloudClient(settings,transport=httpx.MockTransport(handler))
    try:result=second.upload(captured.directory)
    finally:second.close()
    assert result['input_id']==captured.input_id and ready
    assert len(set(created))==1
    assert uploads and all(n==captured.size for n in uploads)


def test_cli_capture_is_real_and_invalid_selection_does_not_echo_content(tmp_path,capsys):
    from naiyue_test_agent.cli import main
    root=tmp_path/'source';root.mkdir();(root/'api.proto').write_bytes(b'proto fixture')
    selection=tmp_path/'selection.json';selection.write_text(json.dumps({'schema':1,'paths':['api.proto'],'executables':[]}))
    assert main(['cloud','input','capture','--source',str(root),'--selection',str(selection),'--output',str(tmp_path/'capture')])==0
    result=json.loads(capsys.readouterr().out)
    assert (tmp_path/'capture/archive').exists() and len(result['input_id'])==64
    selection.write_text('{"unexpected":"PRIVATE_SYNTHETIC_VALUE"}')
    with pytest.raises(SystemExit):main(['cloud','input','capture','--source',str(root),'--selection',str(selection),'--output',str(tmp_path/'bad')])
    output=capsys.readouterr()
    assert 'PRIVATE_SYNTHETIC_VALUE' not in output.err


@pytest.mark.parametrize('method,path',[('POST','/transfers/test/commit'),('GET','/inputs/test')])
def test_default_client_accepts_large_input_package_metadata(method,path):
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits,InputManifest,InputPackage,Selection
    limits=InputLimits();selection=Selection(paths=[f'src/file-{i:05}.py' for i in range(limits.max_files)])
    manifest=InputManifest(selection={'policy_sha256':hashlib.sha256(selection.canonical).hexdigest()},entries=[
        dict(path=p,type='file',size=0,sha256=hashlib.sha256(b'').hexdigest(),mode=420) for p in selection.paths])
    package=InputPackage(input_id=manifest.input_id,archive_sha256='0'*64,size=1,codec='tar',manifest=manifest,selection=selection)
    body=package.model_dump_json().encode()
    assert len(manifest.canonical)<limits.max_metadata_bytes and len(body)>4*1024**2
    settings=CloudConnection(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',owner_token='synthetic-owner',instance_id=str(uuid4()))
    client=CloudClient(settings,transport=httpx.MockTransport(lambda request:httpx.Response(200,content=body)))
    try:received=InputPackage.model_validate(client.request(method,path))
    finally:client.close()
    assert len(received.manifest.entries)==limits.max_files
