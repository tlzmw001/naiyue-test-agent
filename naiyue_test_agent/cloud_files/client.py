"""Explicit private-SSH HTTP client; binary data never enters JSON."""
from contextlib import ExitStack
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL
from pydantic import Field,SecretStr,model_validator
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord,CloudError,UUIDText
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly,open_store_file,acquire_store_lock,release_store_lock,sync_store_directory

logger=logging.getLogger(__name__)


class CloudConnection(CloudRecord):
    endpoint:str
    owner_authority:str
    owner_token:SecretStr
    instance_id:UUIDText
    timeout_seconds:float=Field(default=60.0,gt=0,le=3600)
    io_bytes:int=Field(default=256*1024,gt=0,le=1024*1024)
    max_response_bytes:int=Field(default=64*1024*1024,gt=0)

    @model_validator(mode='after')
    def private_connection(self):
        for value in (self.endpoint,'http://'+self.owner_authority):
            url=urlsplit(value)
            if (url.scheme not in ('http','https') or not ipaddress.ip_address(url.hostname or '').is_loopback
                    or not url.port or url.username or url.path not in ('','/') or url.query or url.fragment):
                raise ValueError('cloud_connection_invalid')
        if not self.owner_token.get_secret_value().strip():raise ValueError('cloud_connection_invalid')
        return self


class CloudClient:
    def __init__(self,settings,*,transport=None):
        self.settings=settings
        self.http=httpx.Client(base_url=settings.endpoint.rstrip('/'),transport=transport,trust_env=False,follow_redirects=False,
            timeout=settings.timeout_seconds,headers={'Host':settings.owner_authority,'Origin':'http://'+settings.owner_authority,
                'Authorization':'Bearer '+settings.owner_token.get_secret_value(),'X-Service-Instance':settings.instance_id,'X-Cloud-Protocol':str(OWNER_PROTOCOL)})

    def acquire_input_use(self,input_id,operation_id,*,purpose='associate'):
        use_id=self.input_use_id(input_id,operation_id,purpose=purpose)
        value=self.request('POST','/input-uses',json=dict(command_id=use_id,input_id=input_id,
                           purpose=purpose,origin_operation_id=operation_id))
        if (value.get('command_id'),value.get('input_id'),value.get('origin_operation_id'),value.get('purpose'))!=(
                use_id,input_id,operation_id,purpose):
            raise CloudError('input_use_conflict',502)
        if value['state']=='released':raise CloudError('input_use_released')
        return use_id

    def input_use_id(self,input_id,operation_id,*,purpose='associate'):
        from uuid import NAMESPACE_URL, uuid5
        return str(uuid5(NAMESPACE_URL,f'naiyue-input:{self.settings.instance_id}:{purpose}:{operation_id}:{input_id}'))

    def release_operation_input(self,input_id,operation_id):
        try:self.release_input_use(self.input_use_id(input_id,operation_id))
        except CloudError as error:
            if error.code!='input_use_not_found':raise

    def release_input_use(self,use_id):
        return self.request('DELETE','/input-uses/'+use_id)

    def cancel_attachment_input(self,directory,input_id,attachment_id):
        from .input_upload import cancel_attachment_input
        from .cache_access import package_reader
        with package_reader(directory, missing_ok=True):
            return cancel_attachment_input(self,directory,input_id,attachment_id)

    def close(self):self.http.close()

    def download(self,task_id,artifact_id,destination):
        from .download import download
        return download(self,task_id,artifact_id,destination)

    def request(self,method,path,**kwargs):
        phase = 'response_headers'
        try:
            with self.http.stream(method,'/api/cloud/v1'+path,**kwargs) as response:
                phase = 'response_body'
                body=bytearray()
                for chunk in response.iter_bytes(self.settings.io_bytes):
                    body.extend(chunk)
                    if len(body)>self.settings.max_response_bytes:raise CloudError('cloud_response_too_large',502)
                phase = 'response_json'
                value=json.loads(body)
                if response.is_error:
                    code=value.get('error',{}).get('code','remote_error')
                    if not isinstance(code,str) or not re.fullmatch('[a-z_]{1,128}',code):code='remote_error'
                    raise CloudError(code,response.status_code)
                return value
        except (httpx.HTTPError,ValueError) as error:
            operation = 'network' if path.startswith('/networks/') else 'cloud_api'
            logger.warning('cloud_request_failed operation=%s phase=%s error_type=%s',
                           operation, phase, type(error).__name__)
            raise CloudError('cloud_connection_failed',503) from None

    async def observe(self,task_id,*,thread_id=None,last_event_id=None,max_frame_bytes=None):
        from pydantic import TypeAdapter
        from naiyue_test_agent.contracts.cloud_tasks import TaskId
        TypeAdapter(TaskId).validate_python(task_id)
        limit=self.settings.max_response_bytes if max_frame_bytes is None else max_frame_bytes
        headers=dict(self.http.headers)
        if last_event_id is not None:
            if len(last_event_id)>16384:raise CloudError('invalid_observation_cursor',422)
            headers['Last-Event-ID']=last_event_id
        try:
            async with httpx.AsyncClient(base_url=self.settings.endpoint,headers=headers,trust_env=False,
                    follow_redirects=False,timeout=self.settings.timeout_seconds) as client:
                async with client.stream('GET','/api/cloud/v1/tasks/'+task_id+'/stream',
                        params={'thread_id':thread_id} if thread_id is not None else {}) as response:
                    if response.is_error:
                        body=bytearray()
                        async for chunk in response.aiter_bytes(self.settings.io_bytes):
                            body.extend(chunk)
                            if len(body)>self.settings.max_response_bytes:raise CloudError('cloud_response_too_large',502)
                        code=json.loads(body).get('error',{}).get('code','remote_error')
                        if not isinstance(code,str) or not re.fullmatch('[a-z_]{1,128}',code):code='remote_error'
                        raise CloudError(code,response.status_code)
                    if response.status_code!=200 or response.headers.get('content-type','').split(';')[0]!='text/event-stream':
                        raise CloudError('cloud_stream_invalid',502)
                    buffer=b''
                    async for chunk in response.aiter_bytes(self.settings.io_bytes):
                        buffer=(buffer+chunk).replace(b'\r\n',b'\n')
                        while b'\n\n' in buffer:
                            frame,buffer=buffer.split(b'\n\n',1)
                            if len(frame)>limit:raise CloudError('cloud_response_too_large',502)
                            yield frame+b'\n\n'
                        if len(buffer)>limit:raise CloudError('cloud_response_too_large',502)
                    if buffer.strip():raise CloudError('cloud_stream_truncated',502)
        except (httpx.HTTPError,ValueError) as error:
            logger.warning('cloud_observation_request_failed error_type=%s',type(error).__name__)
            raise CloudError('cloud_connection_failed',503) from None

    def upload(self,directory,*,upload_id=None,check_cancel=None,on_progress=None,use_id=None):
        directory=Path(directory).absolute()
        stack=ExitStack();primary=None
        try:
            from pydantic import TypeAdapter
            from .cache_access import package_reader
            stack.enter_context(package_reader(directory))
            check_store_directory(directory)
            if upload_id is not None:TypeAdapter(UUIDText).validate_python(upload_id)
            fd=open_store_file(directory/('upload-'+upload_id+'.lock' if upload_id else 'upload.lock'));stack.callback(os.close,fd)
            try:acquire_store_lock(fd)
            except BlockingIOError:raise CloudError('input_capture_busy') from None
            stack.callback(release_store_lock,fd)
            from .input_upload import upload
            return upload(self,directory,upload_id=upload_id,check_cancel=check_cancel,on_progress=on_progress,use_id=use_id)
        except BaseException as error:
            primary=error;raise
        finally:
            try:stack.close()
            except OSError as error:
                logger.error('cloud_input_unlock_failed error_type=%s',type(error).__name__)
                if primary is None:raise
