"""A streaming browser export holds the binding/connection until response close."""
import hashlib,logging
import httpx
from starlette.responses import StreamingResponse
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_results import ResultRecord

logger=logging.getLogger(__name__)


async def _stream(manager,domain,bid,sid,rid,fid,headers):
    with manager.operation(),domain.owner.operation():
        binding=await run_blocking(domain.repo.binding,bid,sid)
        if not binding['retained'] or not binding['task_id']:raise CloudError('cloud_task_unavailable')
        async with manager.connection(binding) as client:
            path='/tasks/'+binding['task_id']+'/results/'+rid
            record=ResultRecord.model_validate(await run_blocking(client.request,'GET',path))
            if record.plan.result_id!=rid or record.plan.task_id!=binding['task_id'] or record.plan.input_id!=binding['input_id']:
                raise CloudError('result_identity_conflict',502)
            slot=next((f.artifact for f in record.bundle.files if f.artifact.artifact_id==fid),None) if record.bundle else None
            if record.phase!='available' or slot is None:raise CloudError('result_file_not_found',404)
            options=client.settings
            async with httpx.AsyncClient(base_url=options.endpoint,headers=dict(client.http.headers),trust_env=False,
                    follow_redirects=False,timeout=options.timeout_seconds) as upstream:
                async with upstream.stream('GET','/api/cloud/v1'+path+'/files/'+fid+'/bytes',headers=headers) as reply:
                    if reply.status_code==416:
                        from starlette.exceptions import HTTPException
                        raise HTTPException(416,detail='invalid_range',headers={'Content-Range':'bytes */'+str(slot.size)})
                    if reply.status_code not in (200,206):raise CloudError('cloud_download_unavailable',reply.status_code)
                    expected='"'+slot.sha256+'"'
                    if reply.headers.get('etag')!=expected:raise CloudError('result_identity_conflict',502)
                    size=int(reply.headers.get('content-length','-1'))
                    if not 0<=size<=slot.size:raise CloudError('cloud_download_invalid',502)
                    if reply.status_code==200 and size!=slot.size:raise CloudError('cloud_download_invalid',502)
                    yield (reply.status_code,{k:v for k,v in reply.headers.items() if k.lower() in
                        ('content-length','etag','accept-ranges','content-range','content-disposition','x-content-type-options','cache-control')})
                    total=0;sha=hashlib.sha256()
                    async for chunk in reply.aiter_bytes(options.io_bytes):
                        total+=len(chunk)
                        if total>size:raise CloudError('artifact_content_mismatch',502)
                        sha.update(chunk);yield chunk
                    if total!=size or reply.status_code==200 and sha.hexdigest()!=slot.sha256:
                        raise CloudError('artifact_content_mismatch',502)


class DownloadResponse(StreamingResponse):
    async def __call__(self,scope,receive,send):
        primary=None
        try:await super().__call__(scope,receive,send)
        except BaseException as error:primary=error;raise
        finally:
            try:await await_settled(self.body_iterator.aclose())
            except BaseException as error:
                logger.warning('cloud_result_download_close_failed error_type=%s',type(error).__name__)
                if primary is None:raise


async def response(request,manager,domain,bid,sid,rid,fid):
    headers={}
    for key in ('range','if-range'):
        if len(request.headers.getlist(key))>1:raise CloudError('invalid_headers',400)
        if key in request.headers:headers[key]=request.headers[key]
    iterator=stream(manager,domain,bid,sid,rid,fid,headers)
    try:status,metadata=await anext(iterator)
    except BaseException:
        await await_settled(iterator.aclose());raise
    return DownloadResponse(iterator,status_code=status,headers=metadata,media_type='application/octet-stream')


def stream(manager,domain,bid,sid,rid,fid,headers):
    from .download_lease import DownloadLease
    return DownloadLease(manager,_stream(manager,domain,bid,sid,rid,fid,headers)).iterate()
