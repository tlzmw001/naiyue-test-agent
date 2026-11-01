"""Timing observations must be bounded, private, and preserve the measured call."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_sync_and_async_observations_restore_methods_and_do_not_keep_arguments():
    from scripts.profile_sandbox_preparation import Recorder, observe
    class Target:
        def run(self,argv):return 'PRIVATE_RESULT'
        async def _request(self,row,operation,params,mutates):return 'PRIVATE_RESPONSE'
    original=Target.run;original_async=Target._request;recorder=Recorder()
    recorder.begin()
    with observe(recorder,[(Target,'run','git'),(Target,'_request','rpc')]):
        assert Target().run(['rev-parse','PRIVATE_PATH'])=='PRIVATE_RESULT'
        assert asyncio.run(Target()._request(None,'input_chunk',{'data':'eA==','secret':'PRIVATE_TOKEN'},True))=='PRIVATE_RESPONSE'
    result=recorder.end(1.0)
    assert Target.run is original and Target._request is original_async
    assert result['details']['git']['rev-parse']['count']==1
    assert result['details']['rpc']['input_chunk']['encoded_chars']==4
    assert 'PRIVATE' not in str(result)


def test_failed_call_and_failed_observer_preserve_original_exception(caplog,monkeypatch):
    from scripts.profile_sandbox_preparation import Recorder, observe
    original=OSError('PRIVATE_PRIMARY')
    class Target:
        def run(self,argv):raise original
    recorder=Recorder();recorder.begin()
    def broken(*args,**kwargs):raise RuntimeError('PRIVATE_OBSERVER')
    monkeypatch.setattr(recorder,'record',broken)
    with observe(recorder,[(Target,'run','git')]):
        with pytest.raises(OSError) as caught:Target().run(['status'])
    assert caught.value is original
    assert recorder.end(1.0)['observation_valid'] is False
    assert 'preparation_profile_observer_failed' in caplog.text and 'PRIVATE' not in caplog.text


def test_successful_call_is_preserved_but_observation_failure_invalidates_measurement(monkeypatch):
    from scripts.profile_sandbox_preparation import Recorder, observe
    class Target:
        def run(self,argv):return 7
    recorder=Recorder();recorder.begin()
    monkeypatch.setattr(recorder,'record',lambda *args:(_ for _ in ()).throw(ValueError('PRIVATE')))
    with observe(recorder,[(Target,'run','git')]):assert Target().run(['status'])==7
    assert not recorder.end(.5)['observation_valid']


def test_only_primary_stages_are_summed_and_recording_is_bounded():
    from scripts.profile_sandbox_preparation import Recorder
    recorder=Recorder(limit=3);recorder.begin()
    token=recorder.capture()
    recorder.record(token,'stage','snapshot',2.0,0,False)
    recorder.record(token,'git','fetch',1.0,0,False)
    recorder.record(token,'rpc','input_cache',.5,0,False)
    result=recorder.end(3.0)
    assert result['stage_seconds']=={'snapshot':2.0}
    assert result['unattributed_seconds']==1.0
    recorder.begin();token=recorder.capture()
    for _ in range(4):recorder.record(token,'git','fetch',.1,0,False)
    result=recorder.end(1.0)
    assert result['observation_valid'] is False and result['event_limit_reached']
    assert result['details']['git']['fetch']['count']==3


def test_parallel_primary_stages_use_union_instead_of_double_counting():
    from scripts.profile_sandbox_preparation import Recorder
    recorder=Recorder();recorder.begin();token=recorder.capture()
    for name,started,seconds in [('snapshot',10,5),('preflight',10.2,1),
            ('container_create',11.2,2),('handshake',13.2,.5),('input_install',15,1)]:
        recorder.record(token,'stage',name,seconds,0,False,started=started)
    result=recorder.end(6.2)
    assert result['stage_covered_seconds']==6
    assert result['stage_overlap_seconds']==3.5
    assert result['unattributed_seconds']==.2
    assert result['stage_seconds']['snapshot']==5


def test_profile_exports_relative_bounded_git_timeline_and_snapshot_details(monkeypatch):
    from scripts import profile_sandbox_preparation as profile
    monkeypatch.setattr(profile.time,'monotonic',lambda:100.0)
    recorder=profile.Recorder(limit=3);recorder.begin();token=recorder.capture()
    recorder.record(token,'git','config',.4,0,False,started=100.2)
    recorder.record(token,'git','ls-files',.3,0,False,started=100.25)
    recorder.record(token,'snapshot','read_local',.1,0,True,started=100.3)
    result=recorder.end(1)
    assert result['timeline']==[
        dict(group='git',kind='config',start_seconds=.2,end_seconds=.6,duration_seconds=.4,failed=False),
        dict(group='git',kind='ls-files',start_seconds=.25,end_seconds=.55,duration_seconds=.3,failed=False),
        dict(group='snapshot',kind='read_local',start_seconds=.3,end_seconds=.4,duration_seconds=.1,failed=True)]
    targets={(name,metric) for _,name,metric in profile.targets()}
    assert {('_read_local','snapshot:read_local'),('_verify_local','snapshot:verify_local')}<=targets


def test_fixture_loader_reuses_exact_supplied_script_without_running_main(tmp_path):
    from scripts.profile_sandbox_preparation import load_fixture
    file=tmp_path/'fixture.py'
    file.write_text('def fixture(root):\n    root.mkdir()\nif __name__ == "__main__":\n    raise RuntimeError("do not run")\n')
    fixture,digest=load_fixture(file)
    fixture(tmp_path/'source')
    assert (tmp_path/'source').is_dir() and len(digest)==64


def test_invalid_fixture_fails_before_remote_work(tmp_path):
    from scripts.profile_sandbox_preparation import load_fixture
    file=tmp_path/'fixture.py';file.write_text('fixture = 1\n')
    with pytest.raises(ValueError):load_fixture(file)


def test_main_preserves_existing_report_before_loading_fixture(tmp_path,monkeypatch):
    from scripts import profile_sandbox_preparation as profile
    output=tmp_path/'report.json';output.write_text('PRIVATE_EXISTING')
    monkeypatch.setattr(profile.sys,'argv',['profile','--context','default','--image','fixture:test',
        '--fixture-script',str(tmp_path/'missing.py'),'--output',str(output)])
    with pytest.raises(FileExistsError):profile.main()
    assert output.read_text()=='PRIVATE_EXISTING'


def test_main_saves_partial_failure_without_exception_body(tmp_path,monkeypatch):
    import json
    from scripts import profile_sandbox_preparation as profile
    output=tmp_path/'report.json'
    async def fail(*args,**kwargs):
        kwargs['report']['rounds']=[{'ready_seconds':2.0}]
        raise OSError('PRIVATE_FAILURE')
    monkeypatch.setattr(profile,'scenario',fail)
    monkeypatch.setattr(profile,'load_fixture',lambda file:(lambda root:None,'a'*64))
    monkeypatch.setattr(profile.sys,'argv',['profile','--context','default','--image','fixture:test',
        '--fixture-script','unused.py','--output',str(output)])
    assert profile.main()==1
    result=json.loads(output.read_text())
    assert result['status']=='failed' and result['error_type']=='OSError'
    assert result['rounds']==[{'ready_seconds':2.0}] and 'PRIVATE' not in output.read_text()


def test_concurrent_git_observations_do_not_lose_calls():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from scripts.profile_sandbox_preparation import Recorder, observe
    barrier=Barrier(4)
    class Target:
        def run(self,argv):barrier.wait(timeout=10);return 1
    recorder=Recorder();recorder.begin()
    with observe(recorder,[(Target,'run','git')]),ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _:Target().run(['config','PRIVATE']),range(40)))==[1]*40
    assert recorder.end(1)['details']['git']['config']['count']==40


@pytest.fixture
def application_probe(monkeypatch):
    from contextlib import asynccontextmanager
    from scripts import profile_sandbox_preparation as profile
    import naiyue_test_agent.application.service_application as service
    state=SimpleNamespace(created=[],managed=[],rows=[],failure=None,cleanup_failure=None,
        content=b'Follow fixture rules.\n',file_count=671)
    class Hooks:
        def step(self):return None
        def preflight(self):return None
        def create(self):return None
        def handshake(self):return None
        def install(self):return None
    hooks=Hooks()
    monkeypatch.setattr(profile,'targets',lambda:[(Hooks,name,'stage:'+stage) for name,stage in
        [('step','snapshot'),('preflight','preflight'),('create','container_create'),
         ('handshake','handshake'),('install','input_install')]])
    async def add(*args,**kwargs):return SimpleNamespace(project_id='owned-project')
    async def create(*args,**kwargs):
        sid=str(len(state.created));state.created.append(sid)
        state.rows.append(SimpleNamespace(binding_id=sid,project_id='owned-project',session_id=sid,
            input_id=sid,lifecycle='running'))
        for name in ('step','preflight','create','handshake','install'):getattr(hooks,name)()
        if state.failure is not None:raise state.failure
        return sid
    async def status(sid):return {'execution':{'state':'ready','binding_id':sid}}
    async def open_(*args):return SimpleNamespace(sessions=SimpleNamespace(create_sandbox=create,status=status))
    async def rows(project):
        assert project=='owned-project'
        return state.rows
    async def get(sid):return next(row for row in state.rows if row.binding_id==sid)
    async def input_(*args):return SimpleNamespace(snapshot=SimpleNamespace(bundle_size=100,manifest=range(state.file_count)))
    async def read(*args):return state.content
    async def files(*args,**kwargs):return SimpleNamespace(read_resource=read)
    async def manage(sid,action,*,confirm_destroy,interrupt):
        assert action=='destroy' and confirm_destroy and interrupt
        state.managed.append(sid)
        if state.cleanup_failure is not None:raise state.cleanup_failure
        (await get(sid)).lifecycle='destroyed'
    app=SimpleNamespace(projects=SimpleNamespace(add_local=add,open=open_),
        environments=SimpleNamespace(list=rows,get=get,input=input_,files=files,manage=manage,
            _preparations=SimpleNamespace(active=lambda:[])))
    @asynccontextmanager
    async def application(*args,**kwargs):yield app
    monkeypatch.setattr(service,'open_service_application',application)
    return state


def test_scenario_measures_three_fresh_sessions_and_cleans_each(tmp_path,application_probe):
    from scripts.profile_sandbox_preparation import scenario
    report={}
    asyncio.run(scenario(tmp_path,'default','fixture:test',fixture=lambda root:root.mkdir(),runs=3,report=report))
    assert application_probe.created==application_probe.managed==['0','1','2']
    assert report['summary']['count']==3 and report['phase']=='completed'
    assert all(row['content_verified'] and row['manifest_files']==671 for row in report['rounds'])
    assert report['cleanup']==dict(attempted=3,confirmed=3,errors=[])
    assert report['provider_requests']==0


@pytest.mark.parametrize('fault',['content','count'])
def test_wrong_input_cannot_produce_successful_measurement(tmp_path,application_probe,fault):
    from scripts.profile_sandbox_preparation import scenario
    if fault=='content':application_probe.content=b'Follow fixture rules.\r\n'
    else:application_probe.file_count=1
    report={}
    with pytest.raises(RuntimeError):
        asyncio.run(scenario(tmp_path,'default','fixture:test',fixture=lambda root:root.mkdir(),runs=3,report=report))
    assert application_probe.created==application_probe.managed==['0']
    assert 'summary' not in report and 'content_verified' not in report['rounds'][0]


def test_cleanup_failure_keeps_primary_and_stops_later_rounds(tmp_path,application_probe,caplog):
    from scripts.profile_sandbox_preparation import scenario
    primary=OSError('PRIVATE_PRIMARY');application_probe.failure=primary
    application_probe.cleanup_failure=PermissionError('PRIVATE_SECONDARY');report={}
    with pytest.raises(OSError) as caught:
        asyncio.run(scenario(tmp_path,'default','fixture:test',fixture=lambda root:root.mkdir(),runs=3,report=report))
    assert caught.value is primary and application_probe.created==application_probe.managed==['0']
    assert report['cleanup']==dict(attempted=1,confirmed=0,errors=['PermissionError'])
    assert 'PRIVATE' not in caplog.text
    assert report['rounds'][0]['ready_seconds'] is None
    assert report['rounds'][0]['within_10_seconds'] is None
    assert report['rounds'][0]['attempt_seconds']>=0


def test_cleanup_failure_after_success_invalidates_experiment(tmp_path,application_probe):
    from scripts.profile_sandbox_preparation import scenario
    failure=PermissionError('PRIVATE_SECONDARY');application_probe.cleanup_failure=failure
    with pytest.raises(PermissionError) as caught:
        asyncio.run(scenario(tmp_path,'default','fixture:test',fixture=lambda root:root.mkdir(),runs=3,report={}))
    assert caught.value is failure and application_probe.created==['0']


def test_record_and_logging_failures_cannot_replace_primary(monkeypatch):
    from scripts import profile_sandbox_preparation as profile
    original=OSError('PRIVATE_PRIMARY')
    class Target:
        def run(self,args):raise original
    recorder=profile.Recorder();recorder.begin()
    def fail(*args,**kwargs):raise ValueError('PRIVATE_SECONDARY')
    monkeypatch.setattr(recorder,'record',fail);monkeypatch.setattr(profile.logger,'warning',fail)
    with profile.observe(recorder,[(Target,'run','git')]):
        with pytest.raises(OSError) as caught:Target().run(['config'])
    assert caught.value is original and recorder.end(1)['observation_valid'] is False


def test_cleanup_logging_failure_cannot_replace_primary(tmp_path,application_probe,monkeypatch):
    from scripts import profile_sandbox_preparation as profile
    primary=OSError('PRIVATE_PRIMARY');application_probe.failure=primary
    application_probe.cleanup_failure=PermissionError('PRIVATE_SECONDARY')
    def fail(*args,**kwargs):raise RuntimeError('PRIVATE_LOGGER')
    monkeypatch.setattr(profile.logger,'error',fail)
    with pytest.raises(OSError) as caught:
        asyncio.run(profile.scenario(tmp_path,'default','fixture:test',fixture=lambda root:root.mkdir(),runs=3,report={}))
    assert caught.value is primary


def test_keyboard_interrupt_has_an_explicit_cancelled_report(tmp_path,monkeypatch):
    import json
    from scripts import profile_sandbox_preparation as profile
    output=tmp_path/'report.json'
    async def interrupted(*args,**kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(profile,'scenario',interrupted)
    monkeypatch.setattr(profile,'load_fixture',lambda file:(lambda root:None,'a'*64))
    monkeypatch.setattr(profile.sys,'argv',['profile','--context','default','--image','fixture:test',
        '--fixture-script','unused.py','--output',str(output)])
    assert profile.main()==130
    assert json.loads(output.read_text())['status']=='cancelled'


def test_cancel_during_preflight_drains_creation_before_enumerating_owned_resources(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from scripts import profile_sandbox_preparation as profile
    import naiyue_test_agent.application.service_application as service
    async def run():
        started=asyncio.Event();finish=asyncio.Event();rows=[];managed=[];stopped=[];jobs=[];report={}
        async def add(*args,**kwargs):return SimpleNamespace(project_id='owned')
        async def worker():
            started.set();await finish.wait()
            rows.append(SimpleNamespace(binding_id='binding',project_id='owned',lifecycle='running'))
            return 'session'
        async def create(*args,**kwargs):
            task=asyncio.create_task(worker());jobs.append(task)
            return await asyncio.shield(task)
        async def open_(*args):return SimpleNamespace(sessions=SimpleNamespace(create_sandbox=create))
        def active():return [dict(binding_id='binding',project_id='owned'),dict(binding_id='foreign',project_id='foreign')]
        async def stop(binding):
            assert binding=='binding';stopped.append(binding);finish.set()
            await asyncio.gather(*jobs,return_exceptions=True)
        async def list_(project):assert project=='owned';return rows
        async def get(binding):assert binding=='binding';return rows[0]
        async def manage(binding,*args,**kwargs):managed.append(binding);rows[0].lifecycle='destroyed'
        app=SimpleNamespace(projects=SimpleNamespace(add_local=add,open=open_),environments=SimpleNamespace(
            _preparations=SimpleNamespace(active=active,stop=stop),list=list_,get=get,manage=manage))
        @asynccontextmanager
        async def application(*args,**kwargs):
            try:yield app
            finally:
                finish.set();await asyncio.gather(*jobs,return_exceptions=True)
                for row in rows:
                    if row.lifecycle!='destroyed':row.lifecycle='stopped'
        monkeypatch.setattr(service,'open_service_application',application)
        task=asyncio.create_task(profile.scenario(tmp_path,'default','fixture:test',fixture=lambda root:root.mkdir(),runs=3,report=report))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert stopped==managed==['binding'] and rows[0].lifecycle=='destroyed'
        assert report['rounds'][0]['ready_seconds'] is None
    asyncio.run(run())
