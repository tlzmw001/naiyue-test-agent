"""Explicit verified download never overwrites a local file or publishes partial bytes."""
import hashlib
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_files.client import CloudConnection
from test_artifact_capture import command
from test_runner_kernel import bootstrap


@pytest.mark.parametrize('bad',[None,'hash','short','exists'])
def test_download_checks_frozen_metadata_and_no_clobber(tmp_path,bad):
    from naiyue_test_agent.cloud_files.client import CloudClient
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
    b=bootstrap();cmd=command();data=b'\x00\xff\r\n'*32768
    record=ArtifactRecord(**cmd.model_dump(),task_id=b.task_id,project_id=b.project_id,input_id=b.input_id,
        generation=b.generation,size=len(data),sha256=hashlib.sha256(data).hexdigest(),captured_at=1.0)
    settings=CloudConnection(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',owner_token='test-owner',instance_id=str(uuid4()))
    def handler(request):
        assert request.headers['Authorization']=='Bearer test-owner'
        if not request.url.path.endswith('/bytes'):return httpx.Response(200,json=record.model_dump())
        return httpx.Response(200,content=data if bad in (None,'exists') else data[:-1] if bad=='short' else b'x'*len(data),
            headers={'Content-Length':str(len(data)),'ETag':'"'+record.sha256+'"','Content-Type':'application/octet-stream'})
    destination=tmp_path/'report.bin'
    if bad=='exists':destination.write_bytes(b'existing user file')
    user=CloudClient(settings,transport=httpx.MockTransport(handler))
    try:
        if bad:
            with pytest.raises(CloudError):user.download(b.task_id,cmd.artifact_id,destination)
            assert destination.read_bytes()==b'existing user file' if bad=='exists' else not destination.exists()
        else:
            result=user.download(b.task_id,cmd.artifact_id,destination)
            assert result['sha256']==record.sha256 and destination.read_bytes()==data
    finally:user.close()
    assert not list(tmp_path.glob('.nyt-download-*'))


def test_download_source_directory_replacement_cannot_publish_other_bytes(tmp_path,monkeypatch):
    import os
    from pathlib import Path
    from naiyue_test_agent.cloud_files.client import CloudClient
    from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
    b=bootstrap();cmd=command();data=b'verified original bytes'
    record=ArtifactRecord(**cmd.model_dump(),task_id=b.task_id,project_id=b.project_id,input_id=b.input_id,
        generation=b.generation,size=len(data),sha256=hashlib.sha256(data).hexdigest(),captured_at=1.0)
    settings=CloudConnection(endpoint='http://127.0.0.1:28281',owner_authority='127.0.0.1:28101',owner_token='test-owner',instance_id=str(uuid4()))
    def handler(request):
        if not request.url.path.endswith('/bytes'):return httpx.Response(200,json=record.model_dump())
        return httpx.Response(200,content=data,headers={'Content-Length':str(len(data)),'ETag':'"'+record.sha256+'"','Content-Type':'application/octet-stream'})
    original=os.link;moved=tmp_path/'moved-by-racing-directory-writer'
    def replace_source(source,destination,**kwargs):
        temporary=next(tmp_path.glob('.nyt-download-*'))
        temporary.rename(moved)
        if moved.is_dir():
            temporary.mkdir();(temporary/'bytes').write_bytes(b'unverified replacement')
        else:temporary.write_bytes(b'unverified replacement')
        return original(source,destination,**kwargs)
    monkeypatch.setattr(os,'link',replace_source)
    user=CloudClient(settings,transport=httpx.MockTransport(handler))
    try:
        try:user.download(b.task_id,cmd.artifact_id,tmp_path/'result.bin')
        except CloudError:pass # Publication/path reconciliation may report unknown.
        assert not (tmp_path/'result.bin').exists() or (tmp_path/'result.bin').read_bytes()==data
    finally:user.close()
