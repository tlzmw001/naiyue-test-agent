"""The comparison must keep the Git request and output checks identical."""
import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest


def test_native_uses_exact_request_and_platform_creation_flags(monkeypatch, tmp_path):
    from scripts import profile_git_process_cost as probe
    request=SimpleNamespace(argv=['PRIVATE_GIT','--version'],cwd=tmp_path,
        env={'PRIVATE':'SECRET'},timeout_seconds=7,stdin_fd=None)
    seen={}
    def run(argv,**kwargs):
        seen.update(argv=argv,**kwargs)
        return SimpleNamespace(returncode=0,stdout=b'git version fixture\n',stderr=b'')
    monkeypatch.setattr(probe.subprocess,'run',run)
    probe.native_call(request,b'git version fixture\n')
    assert seen['argv'] is request.argv and seen['env'] is request.env
    assert seen['cwd']==tmp_path and seen['timeout']==7
    assert seen['stdin']==subprocess.DEVNULL and seen['stdout']==subprocess.PIPE
    assert seen['stderr']==subprocess.PIPE and seen.get('shell',False) is False
    if probe.os.name=='nt':
        assert seen['creationflags']==subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW
    else:assert seen['start_new_session'] is True


@pytest.mark.parametrize('change',[{'returncode':1},{'stdout':b'wrong'}, {'stderr':b'PRIVATE_ERROR'}])
def test_native_rejects_nonmatching_output(monkeypatch,change):
    from scripts import profile_git_process_cost as probe
    result=dict(returncode=0,stdout=b'expected',stderr=b'');result.update(change)
    monkeypatch.setattr(probe.subprocess,'run',lambda *a,**k:SimpleNamespace(**result))
    request=SimpleNamespace(argv=['git','--version'],cwd='.',env={},timeout_seconds=1)
    with pytest.raises(RuntimeError,match='Git output verification failed'):
        probe.native_call(request,b'expected')


@pytest.mark.parametrize('field',['termination','returncode','output_complete','process_reaped',
    'pipes_closed','consumer_settled'])
def test_host_rejects_unsettled_or_failed_process(field):
    from scripts import profile_git_process_cost as probe
    result=dict(termination='exited',returncode=0,output_complete=True,process_reaped=True,
        pipes_closed=True,consumer_settled=True)
    result[field]='timeout' if field=='termination' else 1 if field=='returncode' else False
    class Runner:
        async def run(self,request,consume):
            await consume('stdout',b'expected')
            return SimpleNamespace(**result)
    with pytest.raises(RuntimeError,match='Git process settlement failed'):
        asyncio.run(probe.host_call(Runner(),object(),b'expected'))


def test_real_groups_have_bounded_verified_samples_and_restore_bridge(tmp_path):
    from scripts import profile_git_process_cost as probe
    from naiyue_test_agent.execution import git_repository
    original=git_repository.run_async_sync;report={}
    probe.scenario(tmp_path,'git',2,10,report)
    assert git_repository.run_async_sync is original
    assert list(report['groups'])==probe.GROUPS
    for group in report['groups'].values():
        assert group['count']==2 and len(group['samples_ms'])==2
        assert group['group_seconds']>=0 and group['max_ms']>=group['min_ms']>=0
    assert report['phase']=='completed' and len(report['output_sha256'])==64
    assert report['provider_requests']==0
    serialized=json.dumps(report)
    assert str(tmp_path) not in serialized and 'argv' not in serialized and 'env' not in serialized


def test_shared_bridge_restores_after_original_failure():
    from scripts import profile_git_process_cost as probe
    from naiyue_test_agent.execution import git_repository
    original=git_repository.run_async_sync;failure=OSError('PRIVATE_ORIGINAL')
    with pytest.raises(OSError) as caught:
        with probe.shared_git_loop():raise failure
    assert caught.value is failure and git_repository.run_async_sync is original


def test_groups_reuse_identical_git_request_and_only_shared_groups_reuse_loops(tmp_path,monkeypatch):
    from scripts import profile_git_process_cost as probe
    from naiyue_test_agent.tools.processes import HostProcessRunner
    original_host=HostProcessRunner.run;original_native=probe.subprocess.run
    report={};requests=[];loops={};native=[]
    async def host(self,request,consume,control=None):
        requests.append(request)
        loops.setdefault(report['phase'],[]).append(asyncio.get_running_loop())
        return await original_host(self,request,consume,control)
    def run(argv,**kwargs):
        native.append((argv,kwargs['cwd'],kwargs['env'],kwargs['timeout']))
        return original_native(argv,**kwargs)
    monkeypatch.setattr(HostProcessRunner,'run',host)
    monkeypatch.setattr(probe.subprocess,'run',run)
    probe.scenario(tmp_path,'git',2,10,report)
    first=requests[0]
    assert len(requests)==9 and all(request==first for request in requests)
    assert len(native)==2
    assert all(item==(first.argv,first.cwd,first.env,first.timeout_seconds) for item in native)
    for group in ['host_runner_shared_loop','git_repository_shared_loop']:
        assert loops[group][0] is loops[group][1] and loops[group][0].is_closed()
    for group in ['host_runner_per_call','git_repository_per_call']:
        assert loops[group][0] is not loops[group][1]
        assert all(loop.is_closed() for loop in loops[group])


def test_shared_bridge_actually_reuses_one_loop():
    from scripts import profile_git_process_cost as probe
    from naiyue_test_agent.execution import git_repository
    async def loop():return asyncio.get_running_loop()
    with probe.shared_git_loop():
        first=git_repository.run_async_sync(loop)
        second=git_repository.run_async_sync(loop)
    assert first is second and first.is_closed()


def test_failed_measurement_keeps_prior_samples_and_original_exception():
    from scripts import profile_git_process_cost as probe
    report={'groups':{}};failure=OSError('PRIVATE_ORIGINAL');calls=[]
    def call():
        calls.append(1)
        if len(calls)==2:raise failure
    with pytest.raises(OSError) as caught:probe.measure('fixture',call,3,report)
    assert caught.value is failure and report['groups']['fixture']['count']==1
    assert 'PRIVATE' not in json.dumps(report)


@pytest.mark.parametrize('failure,expected_status,expected_exit',[
    (OSError('PRIVATE_PATH'),'failed',1),(KeyboardInterrupt(),'cancelled',130),
    (asyncio.CancelledError(),'cancelled',130)])
def test_cli_reports_failure_without_body_and_refuses_overwrite(tmp_path,monkeypatch,capsys,
        failure,expected_status,expected_exit):
    from scripts import profile_git_process_cost as probe
    def fail(*args):raise failure
    monkeypatch.setattr(probe,'scenario',fail)
    output=tmp_path/'report.json'
    assert probe.main(['--output',str(output),'--runs','1'])==expected_exit
    report=json.loads(output.read_text())
    assert report['status']==expected_status and report['error_type']==type(failure).__name__
    assert 'PRIVATE' not in output.read_text() and 'PRIVATE' not in capsys.readouterr().out
    before=output.read_bytes()
    with pytest.raises(FileExistsError):probe.main(['--output',str(output)])
    assert output.read_bytes()==before
