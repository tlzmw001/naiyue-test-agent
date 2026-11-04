from types import SimpleNamespace
import subprocess
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_reverse_argv_preserves_authority_and_has_no_shell(tmp_path):
    from naiyue_test_agent.cloud_files.service_tunnels import reverse_argv
    from naiyue_test_agent.cloud_files.profiles import CloudProfile
    from naiyue_test_agent.contracts.cloud_networks import NetworkEndpoints,ServiceTarget
    profile=CloudProfile(profile_id='work',connection_file=tmp_path/'conn',template_ids=['test'],default_template='test',ssh_host='shared',network_id='alice')
    endpoint=NetworkEndpoints(bind_host='172.18.0.1',http_port=21001,grpc_port=21002)
    args=reverse_argv(profile,endpoint,'http',ServiceTarget(address='https://example.test:443/api'))
    assert args[-1]=='shared'
    assert args[args.index('-R')+1]=='172.18.0.1:21001:example.test:443'
    assert 'ExitOnForwardFailure=yes' in args and 'BatchMode=yes' in args


def test_reverse_process_uses_windows_no_window_and_reaps_on_stop(monkeypatch,tmp_path):
    from naiyue_test_agent.cloud_files import service_tunnels as module
    calls=[]
    class Process:
        import io
        stderr=io.BytesIO(b'debug1: remote forward success for: listen 172.18.0.1:21001, connect example.test:443\n')
        def poll(self):return None
        def terminate(self):calls.append('terminate')
        def wait(self,timeout):calls.append('wait');return 0
    monkeypatch.setattr(module,'windows_flags',lambda:123)
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:(calls.append(k) or Process()))
    value=module.ReverseProcess(['ssh','-R','172.18.0.1:21001:example.test:443','shared'],2)
    assert calls[0]['creationflags']==123
    assert calls[0].get('shell',False) is False
    value.close();assert calls[-2:]==['terminate','wait']


def test_reverse_readiness_requires_server_forward_ack(monkeypatch):
    import io
    from naiyue_test_agent.cloud_files import service_tunnels as module
    class Process:
        stderr=io.BytesIO(b'debug1: Authentication succeeded\n')
        def poll(self):return None
        def terminate(self):pass
        def wait(self,timeout):return 0
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:Process())
    value=module.ReverseProcess(['ssh','-R','172.18.0.1:21001:example.test:443','shared'],2)
    assert not value.ready
    value.close()


@pytest.mark.parametrize('target,ack,ready',[
    ('example.test:443',b'debug1: remote forward success for: listen 172.18.0.1:21001, connect example.test:443\r\n',True),
    ('[::1]:443',b'debug1: remote forward success for: listen 172.18.0.1:21001, connect ::1:443\r\n',True),
    ('example.test:443',b'debug1: remote forward success for: listen 172.18.0.1:21002, connect example.test:443\r\n',False),
    ('example.test:443',b'debug1: remote forward failure for: listen 172.18.0.1:21001, connect example.test:443\r\n',False),
])
def test_win32_openssh_crlf_ack_requires_exact_success_identity(monkeypatch,target,ack,ready):
    import io
    from naiyue_test_agent.cloud_files import service_tunnels as module
    class Process:
        stderr=io.BytesIO(ack)
        def poll(self):return None
        def terminate(self):pass
        def wait(self,timeout):return 0
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:Process())
    process=module.ReverseProcess(['ssh','-R','172.18.0.1:21001:'+target,'shared'],5)
    process.reader.join(timeout=5)
    assert not process.reader.is_alive()
    assert process.ready is ready
    process.close()
