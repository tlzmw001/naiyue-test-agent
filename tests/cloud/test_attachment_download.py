"""Runner download resumes only verified byte offsets and binds package identity."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import httpx
from test_attachment_install import fixture
from naiyue_test_agent.cloud_runner.state import read_json
from naiyue_test_agent.contracts.cloud_tasks import TaskAttachment
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_runner import RunCommand
from pydantic import SecretStr


def test_runner_download_and_install_carries_paths_not_file_content(tmp_path):
    from naiyue_test_agent.cloud_runner.attachment_download import receive
    captured,root,state=fixture(tmp_path);package=read_json(captured.directory/'input.json');raw=(captured.directory/'archive').read_bytes()
    aid=str(uuid4());requests=[]
    def serve(request):
        requests.append(request)
        assert request.headers['authorization']=='Bearer task-credential'
        if request.url.path.endswith('/archive'):
            start,end=map(int,request.headers['range'][6:].split('-'))
            return httpx.Response(206,content=raw[start:end+1],headers={'Content-Type':'application/octet-stream',
                'Content-Range':f'bytes {start}-{end}/{len(raw)}','ETag':'"'+captured.archive_sha256+'"'})
        return httpx.Response(200,json={k:package[k] for k in ('input_id','archive_sha256','size','codec')})
    kernel=SimpleNamespace(bootstrap=SimpleNamespace(task_id='a'*32,endpoint='http://task:9000',token=SecretStr('task-credential'),
        max_frame_bytes=1048576,json_depth_limit=32,input_limits=InputLimits(chunk_bytes=64)),root=root,attachment_state=state)
    command=RunCommand(command_id=str(uuid4()),run_id='b'*32,message='use the selected files',model_id='A',
        attachments=[TaskAttachment(attachment_id=aid,input_id=captured.input_id,display_name='files',size=len(raw))])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve),base_url=kernel.bootstrap.endpoint) as client:
            paths=await receive(kernel,command,http=client)
            assert paths==[{'attachment_id':aid,'display_name':'files','path':'.nyt-input/'+aid}]
            assert (root/'.nyt-input'/aid/'.env').read_text()=='selected'
            first=len(requests);(root/'.nyt-input'/aid/'.env').write_text('agent edits')
            assert await receive(kernel,command,http=client)==paths
            assert len(requests)==first and (root/'.nyt-input'/aid/'.env').read_text()=='agent edits'
    asyncio.run(scenario())
