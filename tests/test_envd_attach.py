"""The installed attach endpoint checks identity and delegates to the protected socket."""
import asyncio
import json
import tempfile
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.sandbox import SandboxError


def test_transport_hello_names_a_runtime_generation(tmp_path,monkeypatch):
    from naiyue_test_agent.envd.operations import EnvironmentOperations
    from naiyue_test_agent.sandbox_settings import SandboxSettings
    root=tmp_path/'workspace';root.mkdir()
    env=EnvironmentRef('a','sandbox','p',str(root),'linux')
    request=dict(protocol=2,request_id='r',environment=env.public(),session_id='s',operation='transport_hello',params={})
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings()) as first:
        one=first.dispatch(request);two=first.dispatch(request)
        assert one==two and one['attach']==1 and one['generation']
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings()) as second:
        assert second.dispatch(request)['generation']!=one['generation']
        assert second.dispatch(dict(request,generation=second.generation))['generation']==second.generation
        with pytest.raises(SandboxError,match='env_binding_mismatch'):
            second.dispatch(dict(request,generation=one['generation'],operation='operation_submit',
                params={'operation_id':'a'*32,'kind':'write','payload':{},'metadata':{}}))
        with pytest.raises(SandboxError,match='env_operation_not_found'):second.journal.get('a'*32)


def test_attach_rejects_foreign_session_before_socket_io():
    from naiyue_test_agent.envd.attach import check_envelope
    env=EnvironmentRef('a','sandbox','p','/workspace','linux')
    request=dict(protocol=2,request_id='r',environment=env.public(),session_id='other',operation='read',params={})
    with pytest.raises(SandboxError,match='env_binding_mismatch'):check_envelope('r',request,env,'s')


def test_attach_rpc_checks_original_response_identity():
    from naiyue_test_agent.envd.attach import socket_request
    async def run():
        temporary=tempfile.TemporaryDirectory(prefix='nyt-attach-')
        path=temporary.name+'/socket'
        async def handle(reader,writer):
            r=json.loads(await reader.readline());r.update(ok=True,result={},session_id='foreign')
            writer.write(json.dumps(r).encode()+b'\n');await writer.drain();writer.close();await writer.wait_closed()
        server=await asyncio.start_unix_server(handle,path)
        request=dict(protocol=2,request_id='r',environment={'binding_id':'a'},session_id='s',operation='read',params={})
        async with server:
            with pytest.raises(SandboxError,match='env_binding_mismatch'):
                await socket_request(path,request,limit=65536,timeout=2)
        temporary.cleanup()
    asyncio.run(run())


def test_first_attachment_waits_for_socket_before_sending(tmp_path,monkeypatch):
    from naiyue_test_agent.envd import attach as module
    async def scenario():
        directory=tempfile.TemporaryDirectory(prefix='nyt-ready-')
        path=directory.name+'/starting.sock';first=asyncio.Event()
        loop=asyncio.get_running_loop();connect=loop.sock_connect
        async def connecting(channel,address):
            try:return await connect(channel,address)
            except FileNotFoundError:first.set();raise
        monkeypatch.setattr(loop,'sock_connect',connecting)
        async def handle(reader,writer):
            request=json.loads(await reader.readline())
            writer.write(json.dumps(dict(request,ok=True,result='ready')).encode()+b'\n')
            await writer.drain();writer.close();await writer.wait_closed()
        request=dict(protocol=2,request_id='r',environment={'binding_id':'a'},session_id='s',operation='hello',params={})
        task=asyncio.create_task(module.socket_request(path,request,limit=65536,timeout=3))
        await asyncio.wait_for(first.wait(),3)
        server=await asyncio.start_unix_server(handle,path)
        async with server:assert (await task)['result']=='ready'
        directory.cleanup()
    asyncio.run(scenario())
