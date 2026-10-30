"""Accepted environment waits survive observers and keep original request identity."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions
from naiyue_test_agent.contracts.session_events import RunStartedData,RunFinishedData
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.run_delivery import RunDelivery
from naiyue_test_agent.web.schemas import MessageInput
from naiyue_test_agent.web.settings import WebSettings


def test_accepted_wait_has_no_fake_run_id_and_duplicate_never_starts_twice():
    async def scenario():
        release=asyncio.Event();entered=asyncio.Event();calls=[];subscriptions=ApplicationEventSubscriptions()
        async def start(sid,messages,**kwargs):
            entered.set();await release.wait();calls.append((sid,messages,kwargs))
            publisher=subscriptions.publisher(sid,kwargs['run_id'])
            publisher.publish('run_started',RunStartedData('message'))
            publisher.publish('run_finished',RunFinishedData('completed'))
            return {'messages':messages}
        app=SimpleNamespace(runs=SimpleNamespace(start=start),observability=SimpleNamespace(subscribe=subscriptions.subscribe))
        delivery=RunDelivery(app,WebSettings(database_path='unused',frontend_dist='unused'))
        body=MessageInput(request_id=str(uuid4()),text='original A',wait_for_environment=True)
        waiting={'binding_id':'binding-A','settings':{'model_id':'model-A'}}
        receipt=await delivery.start('A',body,[],can_run=False,environment_wait=waiting)
        await entered.wait()
        assert receipt=={'session_id':'A','request_id':body.request_id,'status':'waiting_environment'}
        assert delivery.submission('A')['status']=='waiting_environment' and delivery.latest=={}
        assert await delivery.start('A',body,[],can_run=False,environment_wait=waiting)==receipt
        with pytest.raises(WebError,match='session_busy'):
            await delivery.start('A',MessageInput(request_id=str(uuid4()),text='second'),[])
        release.set();await delivery.active_by_session['A'].task
        assert len(calls)==1 and calls[0][0]=='A'
        assert calls[0][1][0].content=='original A' and calls[0][2]['wait_for_environment'] is waiting
        assert delivery.latest['A']==calls[0][2]['run_id']
        assert await delivery.start('A',body,[],can_run=False,environment_wait=waiting)==receipt
        await delivery.close(close_application=False)
    asyncio.run(scenario())


def test_waiting_job_ignores_another_clients_run_in_the_same_session():
    async def scenario():
        subscriptions=ApplicationEventSubscriptions();entered=asyncio.Event()
        async def start(*_,**__):entered.set();await asyncio.Event().wait()
        app=SimpleNamespace(runs=SimpleNamespace(start=start),observability=SimpleNamespace(subscribe=subscriptions.subscribe))
        delivery=RunDelivery(app,WebSettings(database_path='unused',frontend_dist='unused'))
        body=MessageInput(request_id=str(uuid4()),text='ours',wait_for_environment=True)
        await delivery.start('A',body,[],can_run=False,environment_wait={'binding_id':'b'})
        await entered.wait()
        subscriptions.publisher('A','foreign-cli-run').publish('run_started',RunStartedData('message'))
        await asyncio.sleep(0)
        assert delivery.submission('A')['status']=='waiting_environment' and not delivery.latest
        assert delivery.active_by_session['A'].run_id is None
        await delivery.stop_submission('A',body.request_id)
        await delivery.close(close_application=False)
    asyncio.run(scenario())


def test_cancelled_wait_does_not_start_and_original_text_is_available_for_recovery():
    async def scenario():
        entered=asyncio.Event();started=[]
        async def start(sid,messages,**kwargs):
            entered.set();await asyncio.Event().wait();started.append(sid)
        app=SimpleNamespace(runs=SimpleNamespace(start=start),observability=SimpleNamespace(subscribe=lambda *_,**__:lambda:None))
        delivery=RunDelivery(app,WebSettings(database_path='unused',frontend_dist='unused'))
        body=MessageInput(request_id=str(uuid4()),text='keep me',wait_for_environment=True)
        await delivery.start('A',body,[],can_run=False,environment_wait={'binding_id':'b'})
        await entered.wait()
        result=await delivery.stop_submission('A',body.request_id)
        assert result['status']=='cancelled' and result['text']=='keep me' and not started
        assert delivery.submission('A')['status']=='cancelled'
        await delivery.close(close_application=False)
    asyncio.run(scenario())


def test_close_cancels_waiting_producers_and_releases_registration():
    async def scenario():
        entered=asyncio.Event()
        async def start(*_,**__):entered.set();await asyncio.Event().wait()
        app=SimpleNamespace(runs=SimpleNamespace(start=start),observability=SimpleNamespace(subscribe=lambda *_,**__:lambda:None))
        delivery=RunDelivery(app,WebSettings(database_path='unused',frontend_dist='unused'))
        body=MessageInput(request_id=str(uuid4()),text='A',wait_for_environment=True)
        await delivery.start('A',body,[],can_run=False,environment_wait={'binding_id':'b'})
        await entered.wait()
        await asyncio.wait_for(delivery.close(close_application=False),5)
        assert not delivery.active_by_session and delivery.submission('A')['status']=='cancelled'
    asyncio.run(scenario())
