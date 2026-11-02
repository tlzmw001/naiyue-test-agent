"""New remote catalog cleanup must preserve an existing primary failure."""
import asyncio

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.model_access import ModelCatalog
from model_proxy_helpers import model_settings
from test_async_application import StreamingToolModel
from test_runner_control import prepared


@pytest.mark.parametrize('has_primary',[True,False])
def test_remote_model_close_error_preserves_primary_and_surfaces_when_it_is_primary(tmp_path,monkeypatch,has_primary):
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            from naiyue_test_agent.cloud_runner import models
            close_error=OSError('injected model close failure');primary=RuntimeError('injected primary');closed=[]
            class Access:
                def __init__(self,bootstrap):self.catalog=ModelCatalog('A',{'A':StreamingToolModel(replies=[])})
                async def aclose(self):closed.append(True);raise close_error
            monkeypatch.setattr(models,'RunnerModels',Access)
            root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
            with pytest.raises(type(primary) if has_primary else OSError) as caught:
                async with open_runner(b,root,state):
                    if has_primary:raise primary
            assert caught.value is (primary if has_primary else close_error) and closed==[True]
        asyncio.run(scenario())


@pytest.mark.parametrize('has_primary',[True,False])
def test_relay_response_close_error_does_not_override_disconnect(tmp_path,monkeypatch,has_primary):
    import httpx
    from starlette.requests import ClientDisconnect
    from naiyue_test_agent.application.cloud.model_relay import ModelRelay
    from naiyue_test_agent.service.cloud.model_routes import RelayResponse
    config,_=model_settings(tmp_path)
    with open_cloud_runtime(config) as r:
        b=prepared(r)
        async def scenario():
            relay=r.models.relay=ModelRelay(r.models);key=next(iter(r.models.routes))
            lease=relay.reserve(r.models.admit(b.token.get_secret_value(),b.task_id,key),b.token.get_secret_value())
            lease.response=httpx.Response(200,content=b'body');close_error=OSError('injected close error')
            async def close():raise close_error
            monkeypatch.setattr(lease.response,'aclose',close)
            async def receive():return {'type':'http.disconnect'}
            async def send(message):
                if has_primary and message['type']=='http.response.body':raise OSError('injected downstream disconnect')
            try:
                with pytest.raises(ClientDisconnect if has_primary else OSError) as caught:
                    await RelayResponse(lease)({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
                if not has_primary:assert caught.value is close_error
                assert lease.closed and not relay.leases
            finally:await r.models.aclose()
        asyncio.run(scenario())
