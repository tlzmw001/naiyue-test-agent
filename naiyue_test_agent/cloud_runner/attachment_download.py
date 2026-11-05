"""Range-download accepted attachments outside the project, then publish a batch."""
from contextlib import AsyncExitStack
import hashlib
import logging
import os
from pathlib import Path
import shutil
import httpx
from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled,acquire_blocking
from naiyue_test_agent.application.cloud.ownership import private_directory
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_file
from naiyue_test_agent.cloud_files.archive import validate_package
from .state import read_json,write_json
from .http import task_request
from .attachment_install import install

logger=logging.getLogger(__name__)


def prepare_cache(state,attachment,limits):
    base=Path(state)/'attachments';base.mkdir(mode=0o700,exist_ok=True);private_directory(base)
    target=base/attachment.attachment_id;target.mkdir(mode=0o700,exist_ok=True);private_directory(target)
    if os.path.lexists(target/'input.json'):
        package=InputPackage.model_validate(read_json(target/'input.json',limits.max_metadata_bytes*3))
        if package.input_id!=attachment.input_id:raise CloudError('attachment_download_identity')
        return target,package
    from naiyue_test_agent.cloud_files.cache import _used
    if _used(base)+attachment.size>limits.max_cache_bytes or shutil.disk_usage(base).free<attachment.size:
        raise CloudError('attachment_cache_capacity')
    return target,None


def open_partial(target,facts):
    marker=target/'download.json'
    value=read_json(marker) if os.path.lexists(marker) else dict(package=facts,offset=0,sha256=hashlib.sha256().hexdigest())
    if value['package']!=facts or not 0<=value['offset']<=facts['size']:raise CloudError('attachment_download_identity')
    stream=os.fdopen(open_store_file(target/'archive'),'r+b')
    try:
        sha=hashlib.sha256();remaining=value['offset']
        while remaining:
            chunk=stream.read(min(remaining,262144))
            if not chunk:raise CloudError('attachment_download_identity')
            sha.update(chunk);remaining-=len(chunk)
        if sha.hexdigest()!=value['sha256']:raise CloudError('attachment_download_identity')
        stream.truncate(value['offset']);stream.seek(value['offset'])
        return stream,value,sha
    except BaseException:
        stream.close();raise


def finish(target,facts,limits):
    manifest,selection=validate_package(target/'archive',facts['codec'],limits,expected_id=facts['input_id'],
        expected_archive_sha256=facts['archive_sha256'],expected_size=facts['size'])
    package=InputPackage(**facts,manifest=manifest,selection=selection)
    write_json(target/'input.json',package.model_dump(by_alias=True))
    return package


async def receive(kernel,command,*,http=None):
    paths=[];limits=kernel.bootstrap.input_limits
    try:
        async with AsyncExitStack() as stack:
            if http is None:
                http=await stack.enter_async_context(httpx.AsyncClient(base_url=kernel.bootstrap.endpoint,
                    timeout=limits.upload_timeout_seconds,follow_redirects=False,trust_env=False))
            for attachment in command.attachments:
                target,cached=await run_blocking(prepare_cache,kernel.attachment_state,attachment,limits)
                if cached is None:
                    path=f'/internal/cloud/v1/tasks/{kernel.bootstrap.task_id}/commands/{command.command_id}/attachments/{attachment.attachment_id}'
                    facts=await task_request(http,kernel.bootstrap,'GET',path)
                    if (set(facts)!={'input_id','archive_sha256','size','codec'} or facts['input_id']!=attachment.input_id
                            or facts['codec'] not in ('tar','gzip') or not 0<facts['size']<=limits.max_archive_bytes
                            or facts['size']!=attachment.size):raise CloudError('attachment_download_identity')
                    opened=await acquire_blocking(lambda:open_partial(target,facts),lambda value:run_blocking(value[0].close))
                    stream,value,sha=opened
                    try:
                        while value['offset']<facts['size']:
                            offset=value['offset'];end=min(offset+limits.chunk_bytes,facts['size'])-1
                            headers={'Authorization':'Bearer '+kernel.bootstrap.token.get_secret_value(),
                                'Range':f'bytes={offset}-{end}','If-Range':'"'+facts['archive_sha256']+'"'}
                            async with http.stream('GET',path+'/archive',headers=headers) as response:
                                if (response.status_code!=206 or response.headers.get('content-type')!='application/octet-stream'
                                        or response.headers.get('content-encoding') not in (None,'identity')
                                        or response.headers.get('content-range')!=f'bytes {offset}-{end}/{facts["size"]}'
                                        or response.headers.get('etag')!='"'+facts['archive_sha256']+'"'):
                                    raise CloudError('attachment_download_rejected')
                                count=0
                                async for chunk in response.aiter_bytes(limits.io_bytes):
                                    count+=len(chunk)
                                    if offset+count>end+1:raise CloudError('attachment_download_identity')
                                    await run_blocking(stream.write,chunk);sha.update(chunk)
                                if offset+count!=end+1:raise CloudError('attachment_download_incomplete')
                            def checkpoint():
                                stream.flush();os.fsync(stream.fileno())
                                write_json(target/'download.json',dict(package=facts,offset=end+1,sha256=sha.hexdigest()))
                            await run_blocking(checkpoint);value['offset']=end+1
                    finally:await await_settled(run_blocking(stream.close))
                    if sha.hexdigest()!=facts['archive_sha256']:raise CloudError('attachment_download_identity')
                    await run_blocking(finish,target,facts,limits)
                relative=await run_blocking(install,target,kernel.root,kernel.attachment_state,attachment.attachment_id,attachment.input_id,limits)
                paths.append(dict(attachment_id=attachment.attachment_id,display_name=attachment.display_name,path=relative))
    except (OSError,httpx.HTTPError) as error:
        logger.warning('cloud_attachment_download_failed error_type=%s',type(error).__name__)
        raise CloudError('attachment_download_failed') from None
    return paths
