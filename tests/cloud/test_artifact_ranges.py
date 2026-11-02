"""Pinned-file single-range downloads keep full-file integrity semantics explicit."""
import pytest
from test_artifact_api import accept,metadata,prepare,upload,task_headers
from test_runner_control import prepared,client,connect
from lifecycle_helpers import config
from cloud_helpers import owner_headers
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime

@pytest.mark.parametrize('header,expected',[('bytes=1-3',b'bcd'),('bytes=4-',b'ef'),('bytes=-2',b'ef'),('bytes=0-999',b'abcdef')])
def test_range_and_if_range(tmp_path,header,expected):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            cmd,_=accept(r,c,ws,b);data=b'abcdef';record=metadata(b,cmd,data);path=prepare(c,r,b,record)
            assert upload(c,r,b,path,record,data).status_code==200
            assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==200
            url='/api/cloud/v1/tasks/'+b.task_id+'/artifacts/'+cmd['artifact_id']+'/bytes';etag='"'+record['sha256']+'"'
            reply=c.get(url,headers=owner_headers(r)|{'Range':header,'If-Range':etag})
            assert reply.status_code==206 and reply.content==expected
            assert reply.headers['content-length']==str(len(expected)) and reply.headers['accept-ranges']=='bytes'
            assert reply.headers['content-range'].endswith('/6')
            full=c.get(url,headers=owner_headers(r)|{'Range':header,'If-Range':'"stale"'})
            assert full.status_code==200 and full.content==data
            for invalid in ('bytes=6-','bytes=2-1','bytes=0-1,3-4','items=0-1','bytes=-0'):
                failed=c.get(url,headers=owner_headers(r)|{'Range':invalid})
                assert failed.status_code==416 and failed.headers['content-range']=='bytes */6'
            assert not r.artifacts.downloads
