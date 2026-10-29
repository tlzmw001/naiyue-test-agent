import asyncio

from native_application_helpers import async_test
from test_application_run_events import make_app


@async_test
async def test_real_resource_preparation_projects_phase_before_model_without_body(monkeypatch):
    from naiyue_test_agent.web.delivered_run import DeliveredRun
    from naiyue_test_agent.web.settings import WebSettings
    from naiyue_test_agent.application.sessions import session_execution
    from naiyue_test_agent.application.observability.diagnostic_persistence import project_diagnostic_event
    app=await make_app(monkeypatch)
    sid=await app.host.sessions.create('A')
    entered,release=asyncio.Event(),asyncio.Event();original=session_execution.prepare_resources
    async def prepare(*args,**kwargs):entered.set();await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(session_execution,'prepare_resources',prepare)
    job=DeliveredRun(sid,[],WebSettings(database_path='unused',frontend_dist='unused',access_token='unit-test'),lambda _:None)
    events=[]
    app.host.observability.subscribe(job.observe);app.host.observability.subscribe(events.append)
    task=asyncio.create_task(app.host.runs.start(sid,[('user','PRIVATE_TASK_BODY')]))
    try:
        await asyncio.wait_for(entered.wait(),5)
        assert job.view()['preparation_phase']=='resources'
        assert not any(event.event_type=='model_call_started' for event in events)
    finally:release.set();await task
    phases=[event for event in events if event.event_type=='run_preparation']
    assert [event.data.phase for event in phases]==['resources','model_request']
    assert all(project_diagnostic_event(event)['data']['phase']==event.data.phase for event in phases)
    assert 'PRIVATE_TASK_BODY' not in repr(phases)
    assert job.view()['preparation_phase']=='model'


@async_test
async def test_phase_observer_failure_keeps_resource_and_model_success(monkeypatch,caplog):
    app=await make_app(monkeypatch)
    received=[]
    def broken(event):
        if event.event_type=='run_preparation':raise OSError('PRIVATE_OBSERVER')
    app.host.observability.subscribe(broken);app.host.observability.subscribe(received.append)
    result=await app.host.runs.start(await app.host.sessions.create('A'),[('user','task')])
    assert result['messages'][-1].content=='PRIVATE_REPLY'
    assert [event.data.phase for event in received if event.event_type=='run_preparation']==['resources','model_request']
    assert received[-1].event_type=='run_finished' and received[-1].data.status=='completed'
    assert 'event_observer_failed' in caplog.text
    assert 'PRIVATE_OBSERVER' not in caplog.text
