"""The portable probe cannot mutate a normal sandbox or leak raw IO errors."""
import asyncio
import pytest


def test_readonly_worker_rejects_mutation_before_rpc():
    from scripts.sandbox_probe.worker import dispatch
    calls=[]
    async def rpc(request):calls.append(request);return {'ok':True,'result':{}}
    result=asyncio.run(dispatch({'operation':'operation_submit','params':{}},rpc,owned=False))
    assert result=={'ok':False,'error':{'code':'probe_readonly'}}
    assert calls==[]


@pytest.mark.parametrize('operation,params',[
    ('prepare_target',{'path':'/workspace/a','operation':'write'}),
    ('probe_source',{'size':10**9}),('probe_sink',{'data':'not-base64'}),
])
def test_invalid_probe_input_is_rejected_without_io(operation,params):
    from scripts.sandbox_probe.worker import dispatch
    async def rpc(request):raise AssertionError('unexpected IO')
    result=asyncio.run(dispatch({'operation':operation,'params':params},rpc,owned=False))
    assert result['ok'] is False
    assert result['error']['code'] in ('probe_readonly','probe_invalid_request')


def test_memory_upload_is_verified_and_does_not_touch_envd():
    import base64,hashlib
    from scripts.sandbox_probe.worker import dispatch
    data=b'bounded-random-marker'
    async def rpc(request):raise AssertionError('unexpected IO')
    result=asyncio.run(dispatch({'operation':'probe_sink','params':{'data':base64.b64encode(data).decode()}},rpc,owned=False))
    assert result=={'ok':True,'result':{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}


def test_native_read_request_is_forwarded_unchanged():
    from scripts.sandbox_probe.worker import dispatch
    request={'operation':'resource_info','params':{'path':'/workspace'}}
    calls=[]
    async def rpc(value):calls.append(value);return {'ok':True,'result':{'exists':True}}
    assert asyncio.run(dispatch(request,rpc,owned=False))['result']=={'exists':True}
    assert calls==[request]


def test_git_transfer_is_available_only_to_owned_probe():
    from scripts.sandbox_probe.worker import dispatch
    calls=[]
    async def rpc(value):calls.append(value);return {'ok':True,'result':{}}
    request={'operation':'input_begin','params':{}}
    assert not asyncio.run(dispatch(request,rpc,owned=False))['ok']
    assert asyncio.run(dispatch(request,rpc,owned=True))['ok']
    assert calls==[request]


def test_socket_backlog_has_a_distinct_fixed_diagnostic():
    from scripts.sandbox_probe.worker import dispatch
    async def rpc(_):raise BlockingIOError(11,'private socket path')
    result=asyncio.run(dispatch({'operation':'hello','params':{}},rpc,owned=False))
    assert result=={'ok':False,'error':{'code':'probe_envd_backlog'}}


def test_socket_error_reports_errno_without_private_message():
    from scripts.sandbox_probe.worker import dispatch
    async def rpc(_):raise ConnectionResetError(104,'private socket and credential')
    result=asyncio.run(dispatch({'operation':'hello','params':{}},rpc,owned=False))
    assert result=={'ok':False,'error':{'code':'probe_envd_io_failed','errno':104}}
