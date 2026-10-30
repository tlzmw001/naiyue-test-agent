"""Real service ingress and admission while the controlled sandbox snapshot is blocked."""
import asyncio
from contextlib import ExitStack
from threading import Event
from uuid import uuid4

import httpx
import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_service_ownership import options
from test_composer_image_inputs import upload
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery


@pytest.mark.parametrize('mode',['run','cancel','destroy','settings_change'])
def test_waiting_message_is_bound_to_original_session_and_images_survive_failure(tmp_path,monkeypatch,mode):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            transport.model.profile={'image_inputs':True}
            config=options(tmp_path)
            server=create_service_app(config,application_factory=lambda:application(tmp_path,{'image':'fixture:test'}))
            async with server.router.lifespan_context(server):
                discovery=read_discovery(config);app=server.state.application
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                other=await host.sessions.create('fake')
                entered,release=Event(),Event();prepare=app.environments._inputs.prepare
                def slow(*args,**kwargs):entered.set();assert release.wait(10);return prepare(*args,**kwargs)
                monkeypatch.setattr(app.environments._inputs,'prepare',slow)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()),wait_for_ready=False)
                assert await asyncio.to_thread(entered.wait,5)
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                base=f'/api/v1/scopes/{project.project_id}/sessions'
                headers={'Authorization':'Bearer '+discovery.access_token,'X-Service-Instance':discovery.instance_id,'Origin':discovery.origin}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url=discovery.origin,headers=headers) as client:
                    request_id=str(uuid4());body=dict(request_id=request_id,text='original A',images=[upload()],wait_for_environment=True)
                    try:
                        first=await client.post(f'{base}/{sid}/runs',json=body,headers={'X-Image-Input':'1','X-Request-ID':request_id})
                        assert first.status_code==202,first.text
                        assert first.json()==dict(session_id=sid,request_id=request_id,status='waiting_environment')
                        repeat=await client.post(f'{base}/{sid}/runs',json=body,headers={'X-Image-Input':'1','X-Request-ID':request_id})
                        assert repeat.json()==first.json()
                        assert (await client.get(f'{base}/{other}')).json()['messages']==[]
                        waiting=(await client.get(f'{base}/{sid}')).json()['pending_submission']
                        assert waiting['status']=='waiting_environment' and 'run_id' not in waiting
                        pending=host.runs.pending_inputs(sid)
                        assert len(pending)==1 and pending[0]['status']=='queued'
                        from naiyue_test_agent.application.errors import SessionBusyError
                        with pytest.raises(SessionBusyError):host.runs.remove_pending(sid,pending[0]['message_id'])
                        if mode=='cancel':
                            stopped=await client.post(f'{base}/{sid}/submissions/{request_id}/stop',json={},headers={'X-Request-ID':str(uuid4())})
                            assert stopped.status_code==200 and stopped.json()['status']=='cancelled',stopped.text
                        elif mode=='destroy':
                            stopping=asyncio.create_task(app.environments.manage(binding,'destroy',confirm_destroy=True,interrupt=True))
                            async with asyncio.timeout(5):
                                while not app.environments._preparations.view(binding)['phase']=='stopping':await asyncio.sleep(0)
                            release.set();await stopping
                        elif mode=='settings_change':
                            original=host.sessions._sessions.prepare_admission
                            async def changed(*args,**kwargs):
                                await host.sessions.select_permission_mode(sid,'Default')
                                return await original(*args,**kwargs)
                            monkeypatch.setattr(host.sessions._sessions,'prepare_admission',changed)
                        release.set()
                        service=await server.state.scopes.get(project.project_id)
                        job=service.delivery.submissions[sid]
                        await asyncio.wait_for(job.task,10)
                        view=(await client.get(f'{base}/{sid}')).json()
                        if mode=='run':
                            assert view['pending_submission']['status']=='completed'
                            assert view['pending_submission']['run_id']==view['latest_run_id']
                            assert not host.runs.pending_inputs(sid)
                            assert [m['content'] for m in view['messages'] if m['type']=='human']
                        else:
                            assert not view['latest_run_id'] and view['messages']==[]
                            kept=host.runs.pending_inputs(sid)
                            assert len(kept)==1 and kept[0]['status']=='retained'
                            image=kept[0]['images'][0]
                            pixels=await client.get(f"{base}/{sid}/pending-inputs/{kept[0]['message_id']}/images/{image['index']}")
                            assert pixels.status_code==200 and pixels.content.startswith(b'\x89PNG')
                            if mode=='settings_change':assert view['pending_submission']['error_code']=='env_submission_changed'
                        assert (await client.get(f'{base}/{other}')).json()['messages']==[]
                        assert (await client.post(f'{base}/{sid}/runs',json=body,headers={'X-Image-Input':'1','X-Request-ID':request_id})).json()==first.json()
                    finally:
                        release.set()
    asyncio.run(scenario())
