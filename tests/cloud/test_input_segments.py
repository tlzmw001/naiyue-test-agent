"""Durable byte offsets reflect fsynced segments, including restart/ACK-loss windows."""
import hashlib

from fastapi.testclient import TestClient
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings,owner_headers
from test_input_formats import package
from test_input_import import transfer_request


def put(c,r,path,data,offset,**extra):
    return c.put(path+'/bytes',content=data,headers=owner_headers(r)|{
        'Content-Length':str(len(data)),'Content-Type':'application/octet-stream','Upload-Offset':str(offset),
        'X-Content-SHA256':hashlib.sha256(data).hexdigest()}|extra)


def test_segments_duplicate_restart_and_commit(tmp_path):
    captured,_,_=package(tmp_path);data=(captured.directory/'archive').read_bytes()
    cfg=settings(tmp_path,input_limits=InputLimits(chunk_bytes=64))
    with open_cloud_runtime(cfg) as r,TestClient(create_cloud_apps(r)[0],base_url=cfg.origin) as c:
        receipt=c.post('/api/cloud/v1/transfers',headers=owner_headers(r),json=transfer_request(captured)).json()
        path='/api/cloud/v1/transfers/'+receipt['resource_id']
        assert receipt['result']['transfer']['chunk_bytes']==64
        first=put(c,r,path,data[:64],0);assert first.status_code==200 and first.json()['offset']==64
        assert put(c,r,path,data[:64],0).json()['offset']==64
        assert put(c,r,path,b'x'*64,0).status_code==409
        assert put(c,r,path,data[128:192],128).status_code==409
        assert c.post(path+'/commit',headers=owner_headers(r),json={}).status_code==409
    with open_cloud_runtime(cfg) as r,TestClient(create_cloud_apps(r)[0],base_url=cfg.origin) as c:
        assert c.get(path,headers=owner_headers(r)).json()['offset']==64
        for offset in range(64,len(data),64):
            response=put(c,r,path,data[offset:offset+64],offset)
            assert response.status_code==200,response.text
        assert response.json()['state']=='uploaded' and response.json()['offset']==len(data)
        assert c.post(path+'/commit',headers=owner_headers(r),json={}).status_code==200
        assert r.inputs.get_input(captured.input_id).input_id==captured.input_id


@pytest.mark.parametrize('corrupt',[False,True])
def test_unconfirmed_suffix_is_truncated_but_confirmed_prefix_cannot_change(tmp_path,monkeypatch,corrupt):
    captured,_,_=package(tmp_path);data=(captured.directory/'archive').read_bytes()
    cfg=settings(tmp_path,input_limits=InputLimits(chunk_bytes=64))
    with open_cloud_runtime(cfg) as r,TestClient(create_cloud_apps(r)[0],base_url=cfg.origin) as c:
        receipt=r.inputs.create(transfer_request(captured));path='/api/cloud/v1/transfers/'+receipt.resource_id
        assert put(c,r,path,data[:64],0).status_code==200
        archive=r.inputs.staging(receipt.resource_id)/'archive'
        original=r.inputs.repository.save
        with monkeypatch.context() as patch:
            def fail(previous,value):
                if value.offset>previous.offset:raise OSError('controlled save failure after fsync')
                return original(previous,value)
            patch.setattr(r.inputs.repository,'save',fail)
            assert put(c,r,path,data[64:128],64).status_code==503
        assert archive.stat().st_size==128 and r.inputs.transfer(receipt.resource_id).offset==64
        if corrupt:
            with archive.open('r+b') as f:f.write(b'bad')
            assert c.get(path,headers=owner_headers(r)).status_code==409
            assert r.inputs.transfer(receipt.resource_id).offset==64
        else:
            status=c.get(path,headers=owner_headers(r));assert status.status_code==200,status.text
            assert status.json()['offset']==64 and archive.read_bytes()==data[:64]
            assert put(c,r,path,data[64:128],64).json()['offset']==128


def test_status_reconciles_published_commit_before_client_retry(tmp_path,monkeypatch):
    captured,_,_=package(tmp_path);data=(captured.directory/'archive').read_bytes();cfg=settings(tmp_path)
    with open_cloud_runtime(cfg) as r,TestClient(create_cloud_apps(r)[0],base_url=cfg.origin) as c:
        receipt=r.inputs.create(transfer_request(captured));path='/api/cloud/v1/transfers/'+receipt.resource_id
        assert put(c,r,path,data,0).status_code==200
        with monkeypatch.context() as patch:
            def fail(*args):raise OSError('controlled published before index failure')
            patch.setattr(r.inputs.repository,'commit',fail)
            assert c.post(path+'/commit',headers=owner_headers(r),json={}).status_code==503
        response=c.get(path,headers=owner_headers(r))
        assert response.status_code==200 and response.json()['state']=='committed'
        assert r.inputs.get_input(captured.input_id).input_id==captured.input_id


def test_confirmed_file_identity_is_observed_after_writer_close(tmp_path,monkeypatch):
    import os
    import naiyue_test_agent.application.cloud.byte_stream as module
    captured,_,_=package(tmp_path);data=(captured.directory/'archive').read_bytes();cfg=settings(tmp_path)
    with open_cloud_runtime(cfg) as r,TestClient(create_cloud_apps(r)[0],base_url=cfg.origin) as c:
        receipt=r.inputs.create(transfer_request(captured));path='/api/cloud/v1/transfers/'+receipt.resource_id
        archive=r.inputs.staging(receipt.resource_id)/'archive';original=os.fdopen
        class Writer:
            def __init__(self,stream):self.stream=stream
            def __getattr__(self,name):return getattr(self.stream,name)
            def close(self):
                self.stream.close();info=archive.stat()
                os.utime(archive,ns=(info.st_atime_ns,info.st_mtime_ns+1000000))
        def opened(fd,mode,*args,**kwargs):
            stream=original(fd,mode,*args,**kwargs)
            return Writer(stream) if mode=='r+b' else stream
        monkeypatch.setattr(module.os,'fdopen',opened)
        response=put(c,r,path,data,0);assert response.status_code==200,response.text
        info=archive.stat()
        assert response.json()['archive_identity']==[getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]
