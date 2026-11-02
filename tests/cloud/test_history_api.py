"""Owner archive APIs are bounded and independent of task lifecycle or local memory."""
import asyncio
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt
from naiyue_test_agent.contracts.cloud_events import EventBatch,CloudEvent
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from cloud_helpers import settings,owner_headers
from test_history_ingress import setup_task
from test_history_projection import record


def publish(runtime,task,token,payloads,*,start=1,epoch=None):
    epoch=epoch or str(uuid4())
    batch=EventBatch(task_id=task.task_id,generation=task.generation,epoch=epoch,events=[CloudEvent(seq=i,
        source_generation=1,run_id='a'*32,kind='history_record',payload=p) for i,p in enumerate(payloads,start)])
    return runtime.append_events(token,batch.model_dump())


def owner_command(runtime,task,payload):
    receipt=CommandReceipt(command_id=payload['command_id'],resource_id=task.task_id,operation='run:start',
        request_digest='e'*64,status='running',http_status=202,created_at=1.0,updated_at=1.0,
        result={'command':{'run_id':'a'*32,'message':'owner request'}})
    LifecycleRepository(runtime.database).reserve(task,task,receipt,runtime.settings)


def test_owner_history_detail_cursor_and_input_receipt(tmp_path):
    with open_cloud_runtime(settings(tmp_path,max_frame_bytes=4096)) as runtime:
        task,token=setup_task(runtime)
        user=record(HumanMessage(id='u',content='owner request'),position=1)
        owner_command(runtime,task,user)
        ai=record(AIMessage(id='a',content='reply'*100,additional_kwargs={'secret':'never-public'},
            response_metadata={'credential':'never-public'}),position=2)
        ack=publish(runtime,task,token,[user,ai])
        async def run():
            owner,_=create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin,
                    headers=owner_headers(runtime)) as client:
                base='/api/cloud/v1/tasks/'+task.task_id
                page=await client.get(base+'/history',params={'limit':1})
                assert page.status_code==200,page.text
                value=page.json();assert value['messages'][0]['id']=='a' and value['next_cursor']
                assert 'never-public' not in page.text
                cursor=value['next_cursor']
                next_page=await client.get(base+'/history',params={'cursor':cursor,'limit':1})
                assert next_page.status_code==200 and next_page.json()['messages'][0]['owner_input'] is True
                assert (await client.get(base+'/history',params={'cursor':cursor+'x'})).status_code==422
                assert (await client.get(base+'/history',params={'cursor':cursor,'thread_id':'other'})).status_code==422
                detail=await client.get(base+'/history/messages/a',params=dict(thread_id='root-thread',revision=1,offset=3,limit=7))
                assert detail.status_code==200 and detail.json()['text']==('reply'*100)[3:10]
                assert detail.json()['next_offset']==10
                assert 'never-public' not in detail.text
                assert (await client.get(base+'/history/messages/a',params=dict(thread_id='root-thread',revision=1,field='body_json'))).status_code==422
                assert (await client.get(base+'/history/threads')).json()['threads'][0]['role']=='root'
                assert (await client.get(base+'/history',headers={'Authorization':'Bearer '+token})).status_code==401
                assert (await client.get(base+'/history',params={'unknown':1})).status_code==422
                usage=await client.get(base+'/usage')
                assert usage.status_code==200 and usage.json()['recorded_calls']==0
                assert 'history:read' in (await client.get('/api/cloud/v1/instance')).json()['capabilities']
        asyncio.run(run())
        assert ack['last_seq']==2


def test_fixed_cursor_survives_new_revision_and_foreign_task_is_rejected(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        task,token=setup_task(runtime);other,_=setup_task(runtime)
        payloads=[record(AIMessage(id=str(i),content='old'),position=i) for i in (1,2,3)]
        ack=publish(runtime,task,token,payloads)
        first=runtime.archive.page(task.task_id,limit=2)
        publish(runtime,task,token,[record(AIMessage(id='1',content='new'),position=1,revision=2)],start=4,epoch=ack['epoch'])
        older=runtime.archive.page(task.task_id,limit=2,cursor=first['next_cursor'])
        assert older['messages'][0]['content']=='old' and older['coverage_seq']==3
        from naiyue_test_agent.contracts.cloud_tasks import CloudError
        import pytest
        with pytest.raises(CloudError,match='invalid_history_cursor'):
            runtime.archive.page(other.task_id,cursor=first['next_cursor'])
