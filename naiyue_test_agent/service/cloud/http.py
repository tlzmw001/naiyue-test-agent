"""Bounded JSON bodies and runtime-owned local work for private cloud routes."""
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_tasks import CloudError


async def _body(request, runtime):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > runtime.settings.max_frame_bytes:
            raise CloudError('request_too_large', 413)
        data.extend(chunk)
    return parse_object(data,byte_limit=runtime.settings.max_frame_bytes,depth_limit=runtime.settings.json_depth_limit)
