"""Missing input recovery binds the immutable archive, never a remote local path."""
import hashlib
from types import SimpleNamespace
import httpx
import pytest
from naiyue_test_agent.application.cloud_local.delivery_inputs import recover_archive
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('fault',[None,'etag','range','hash'])
def test_recovered_input_requires_exact_ranges_etag_and_whole_hash(tmp_path,fault):
    data=b'fixed input archive';sha=hashlib.sha256(data).hexdigest();seen=[]
    package=SimpleNamespace(input_id='a'*64,size=len(data),archive_sha256=sha)
    limits=SimpleNamespace(chunk_bytes=4,io_bytes=2)
    def response(request):
        seen.append(request)
        assert request.url.path=='/api/cloud/v1/inputs/'+package.input_id+'/archive'
        assert request.headers['if-range']=='"'+sha+'"'
        start,end=map(int,request.headers['range'].removeprefix('bytes=').split('-'))
        body=data[start:end+1]
        if fault=='hash':body=b'x'*len(body)
        return httpx.Response(206,content=body,headers={'etag':'"bad"' if fault=='etag' else '"'+sha+'"',
            'content-range':'bad' if fault=='range' else f'bytes {start}-{end}/{len(data)}','content-length':str(end-start+1)})
    with httpx.Client(transport=httpx.MockTransport(response),base_url='http://127.0.0.1:1234') as client:
        destination=tmp_path/'archive'
        if fault:
            with pytest.raises(CloudError,match='cloud_delivery_input_changed'):
                recover_archive(SimpleNamespace(http=client),package,destination,limits)
            assert not destination.exists()
        else:
            assert recover_archive(SimpleNamespace(http=client),package,destination,limits)==destination
            assert destination.read_bytes()==data and len(seen)==5
