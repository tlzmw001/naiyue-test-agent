"""The actual request handler owns search registrations until UDS EOF, including lost ACKs."""
import io
import json
import struct
from threading import Event,Thread
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.envd.__main__ import EnvdRequestHandler
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.envd.protocol import encode
from naiyue_test_agent.sandbox_settings import SandboxSettings


@pytest.mark.parametrize('broken_reply',[False,True])
def test_eof_or_lost_registration_reply_does_not_leave_an_orphan(tmp_path,broken_reply,monkeypatch):
    from naiyue_test_agent.envd import __main__ as module
    # SO_PEERCRED is Linux-only; inject only the socket's authenticated peer result.
    monkeypatch.setattr(module.socket,'SO_PEERCRED',17,raising=False)
    env=EnvironmentRef('a','sandbox','p',str(tmp_path),'linux')
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings()) as ops:
        request=dict(protocol=2,environment=env.public(),session_id='s',request_id='register',
            operation='request_register',params={'request_id':'search'})
        handler=object.__new__(EnvdRequestHandler)
        handler.server=SimpleNamespace(dispatcher=ops,limit=65536)
        handler.request=SimpleNamespace(getsockopt=lambda *_:struct.pack('3i',1,0,0))
        handler.rfile=io.BytesIO(encode(request,65536))
        class Output(io.BytesIO):
            def write(self,value):
                if broken_reply:raise BrokenPipeError('ACK lost')
                return super().write(value)
        handler.wfile=Output()
        if broken_reply:
            with pytest.raises(BrokenPipeError):handler.handle()
        else:
            handler.handle();assert json.loads(handler.wfile.getvalue())['ok'] is True
        assert not ops._searches


def test_lease_disconnect_cancels_actual_active_search_without_attach_finally(tmp_path,monkeypatch):
    from naiyue_test_agent.envd import __main__ as module
    monkeypatch.setattr(module.socket,'SO_PEERCRED',17,raising=False)
    env=EnvironmentRef('a','sandbox','p',str(tmp_path),'linux')
    with EnvironmentOperations(env,'s',tmp_path/'state',SandboxSettings()) as ops:
        registered,disconnect,search_started=Event(),Event(),Event();errors=[]
        request=dict(protocol=2,environment=env.public(),session_id='s',request_id='register',
            operation='request_register',params={'request_id':'search'})
        class Input(io.BytesIO):
            def read(self,size=-1):assert disconnect.wait(5);return b''
        class Output(io.BytesIO):
            def write(self,value):result=super().write(value);registered.set();return result
        handler=object.__new__(EnvdRequestHandler)
        handler.server=SimpleNamespace(dispatcher=ops,limit=65536)
        handler.request=SimpleNamespace(getsockopt=lambda *_:struct.pack('3i',1,0,0))
        handler.rfile=Input(encode(request,65536));handler.wfile=Output()
        def searching(req,control=None):search_started.set();assert control.event.wait(5);return 'stopped'
        ops.files.dispatch=searching
        def run(fn):
            try:fn()
            except BaseException as error:errors.append(error)
        lease=Thread(target=run,args=(handler.handle,));lease.start()
        assert registered.wait(5)
        search=Thread(target=run,args=(lambda:ops.dispatch(dict(request,request_id='search',operation='find',params={'registered':True})),))
        search.start()
        try:assert search_started.wait(5)
        finally:disconnect.set();lease.join(5);search.join(5)
        assert not lease.is_alive() and not search.is_alive() and not errors
        assert not ops._searches and ops._readers==0
