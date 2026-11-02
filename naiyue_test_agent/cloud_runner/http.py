"""Bounded task HTTP replies; credentials stay in headers and errors stay finite."""
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_tasks import CloudError


async def task_request(http,bootstrap,method,path,*,value=None,content=None,headers=None):
    auth={'Authorization':'Bearer '+bootstrap.token.get_secret_value()}
    if headers:auth.update(headers)
    async with http.stream(method,path,json=value,content=content,headers=auth) as response:
        raw=bytearray()
        async for chunk in response.aiter_bytes():
            if len(raw)+len(chunk)>bootstrap.max_frame_bytes:raise CloudError('task_response_too_large')
            raw.extend(chunk)
        if response.status_code!=200:raise CloudError('task_request_unconfirmed',response.status_code)
        return parse_object(raw,byte_limit=bootstrap.max_frame_bytes,depth_limit=bootstrap.json_depth_limit)
