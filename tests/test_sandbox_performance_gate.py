"""Observed latency budgets must cause a failing gate, not just a pretty report."""
import pytest


@pytest.mark.parametrize('default_newline', ['\n', '\r\n'])
def test_fixture_and_real_snapshot_have_identical_lf_bytes_on_every_host(tmp_path,monkeypatch,default_newline):
    from pathlib import Path
    from scripts.verify_sandbox_persistent import fixture
    from naiyue_test_agent.contracts.projects import LocalSource
    from naiyue_test_agent.execution.git_repository import GitRepository
    from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
    from naiyue_test_agent.sandbox_settings import GitInputSettings
    write_text=Path.write_text
    def host_text_default(path,content,**kwargs):
        # Inject only the platform default; an explicit newline must still win.
        kwargs.setdefault('newline',default_newline)
        return write_text(path,content,**kwargs)
    monkeypatch.setattr(Path,'write_text',host_text_default)
    root=tmp_path/'source';fixture(root)
    expected={'AGENTS.md':b'Follow fixture rules.\n'}
    for source in ('agents','claude'):
        for index in range(10):
            expected[f'.{source}/skills/{source}-{index}/SKILL.md']=(
                f'---\nname: {source}-{index}\ndescription: Fixture skill\n---\nFixture body\n').encode('utf-8')
    assert {name:(root/name).read_bytes() for name in expected}==expected
    policy=GitInputSettings()
    snapshot=SnapshotBuilder(tmp_path/'snapshot',policy).prepare(LocalSource(root),'a'*32)
    objects=GitRepository(tmp_path/'snapshot'/'objects.git',policy)
    assert {name:objects.run(['show',snapshot.input_snapshot+':'+name]) for name in expected}==expected
    assert len(snapshot.manifest)==671


@pytest.mark.parametrize(('field','slow'), [('warm_read',501),('twenty_skills',2001),('warm_offline_model_run_seconds',3.1)])
def test_slow_real_path_cannot_pass_performance_screen(field,slow):
    from scripts.verify_sandbox_persistent import budget_screen
    report=dict(warm_read={'p95_ms':100},twenty_skills={'p95_ms':200},warm_offline_model_run_seconds=.3)
    report[field]=slow if isinstance(slow,float) else {'p95_ms':slow}
    assert not all(budget_screen(report).values())


def test_complete_warm_path_within_spec_budgets_passes():
    from scripts.verify_sandbox_persistent import budget_screen
    assert all(budget_screen(dict(warm_read={'p95_ms':500},twenty_skills={'p95_ms':2000},
        warm_offline_model_run_seconds=3.0)).values())


def test_rpc_failure_still_writes_failed_performance_report(tmp_path,monkeypatch):
    import json
    from scripts import verify_sandbox_persistent as gate
    from naiyue_test_agent.contracts.sandbox import SandboxError
    output=tmp_path/'report.json'
    async def fail(*args,**kwargs):raise SandboxError('env_unreachable')
    monkeypatch.setattr(gate,'scenario',fail)
    monkeypatch.setattr(gate.sys,'argv',['gate','--context','default','--image','fixture:test','--output',str(output)])
    with pytest.raises(SandboxError):gate.main()
    assert json.loads(output.read_text())['status']=='failed'
    assert json.loads(output.read_text())['error_code']=='env_unreachable'


def test_resource_byte_mismatch_is_strict_and_has_content_free_diagnostics(tmp_path,monkeypatch):
    import asyncio
    import hashlib
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from scripts import verify_sandbox_persistent as gate
    import naiyue_test_agent.application.service_application as service
    created=[];managed=[];report={};received=b'Follow fixture rules.\r\n'
    async def add(*args,**kwargs):return SimpleNamespace(project_id='project')
    async def create(*args,**kwargs):created.append(str(len(created)));return created[-1]
    async def status(sid):return {'execution':{'state':'ready','binding_id':sid}}
    async def open_(*args):return SimpleNamespace(sessions=SimpleNamespace(create_sandbox=create,status=status))
    async def rows(*args):return [SimpleNamespace(binding_id=sid,session_id=sid,lifecycle='running') for sid in created]
    async def read(path,limit):
        assert path=='/workspace/AGENTS.md' and limit==4096
        return received
    async def files(*args,**kwargs):return SimpleNamespace(read_resource=read)
    async def manage(sid,*args,**kwargs):managed.append(sid)
    app=SimpleNamespace(projects=SimpleNamespace(add_local=add,open=open_),
        environments=SimpleNamespace(list=rows,files=files,manage=manage))
    @asynccontextmanager
    async def application(*args,**kwargs):yield app
    monkeypatch.setattr(service,'open_service_application',application)
    monkeypatch.setattr(gate,'fixture',lambda root:root.mkdir())
    with pytest.raises(AssertionError):asyncio.run(gate.scenario(tmp_path,'default','fixture:test',report=report))
    assert report['resource_read_mismatch']==dict(expected_bytes=len(b'Follow fixture rules.\n'),
        received_bytes=len(received),received_sha256=hashlib.sha256(received).hexdigest(),crlf_count=1)
    assert report['phase']=='warm_reads' and 'warm_resource_read' not in report
    assert managed==['0','1']


def test_every_owned_sandbox_is_cleaned_even_when_first_cleanup_fails(tmp_path,monkeypatch,caplog):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from scripts import verify_sandbox_persistent as gate
    import naiyue_test_agent.application.service_application as service
    class Primary(Exception):pass
    original=Primary('PRIVATE_PRIMARY');managed=[];created=[]
    async def add(*args,**kwargs):return SimpleNamespace(project_id='project')
    async def create(*args,**kwargs):created.append(str(len(created)));return created[-1]
    async def status(sid):return {'execution':{'state':'ready','binding_id':sid}}
    async def open_(*args):return SimpleNamespace(sessions=SimpleNamespace(create_sandbox=create,status=status))
    async def rows(*args):return [SimpleNamespace(binding_id=sid,session_id=sid,lifecycle='running') for sid in created]
    async def files(*args,**kwargs):raise original
    async def manage(sid,*args,**kwargs):
        managed.append(sid)
        if sid=='0':raise OSError('PRIVATE_CLEANUP')
    app=SimpleNamespace(projects=SimpleNamespace(add_local=add,open=open_),
        environments=SimpleNamespace(list=rows,files=files,manage=manage))
    @asynccontextmanager
    async def application(*args,**kwargs):yield app
    monkeypatch.setattr(service,'open_service_application',application)
    monkeypatch.setattr(gate,'fixture',lambda root:root.mkdir())
    with pytest.raises(Primary) as caught:asyncio.run(gate.scenario(tmp_path,'default','fixture:test'))
    assert caught.value is original and managed==['0','1']
    assert 'persistent_gate_cleanup_failed' in caplog.text and 'PRIVATE' not in caplog.text
