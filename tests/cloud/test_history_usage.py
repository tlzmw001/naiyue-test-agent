"""Usage is deduplicated per invocation, survives restart, and remains optional."""
import asyncio

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from lifecycle_helpers import config
from observation_helpers import running,item,frame


def test_usage_is_counted_once_and_persisted_without_changing_required_history(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        start=item(cmd,'model_call_started',dict(invocation_id='call',model_id='A',purpose='agent'))
        finish=item(cmd,'model_call_finished',dict(invocation_id='call',status='returned',usage_status='final',
            input_tokens=10,output_tokens=3,cache_read_tokens=2,cache_write_tokens=None,duration_ms=5,error_type=None))
        packet=frame(b,c,[start,finish]);await r.control.receive(c,packet);await r.control.receive(c,packet)
        value=await r.observations.usage(b.task_id)
        assert value['recorded_calls']==1 and value['metrics']['input_tokens']['known_sum']=='10'
        assert value['models']['items'][0]['model_id']=='A'
        assert r.history_page(b.task_id)['last_seq']==0
        await r.observations.aclose();await r.control.aclose()
        return b.task_id
    settings=config(tmp_path)
    with open_cloud_runtime(settings) as r:task_id=asyncio.run(run(r))
    with open_cloud_runtime(settings) as r:
        value=asyncio.run(r.observations.usage(task_id))
        assert value['recorded_calls']==1 and value['metrics']['output_tokens']['known_sum']=='3'


def test_interrupted_capture_reopens_as_incomplete_with_unknown_usage(tmp_path):
    async def run(r):
        b,c,cmd=await running(r)
        await r.control.receive(c,frame(b,c,[item(cmd,'model_call_started',dict(invocation_id='unfinished',model_id='A',purpose='agent'))]))
        await r.observations.aclose();await r.control.aclose()
        return b.task_id
    settings=config(tmp_path)
    with open_cloud_runtime(settings) as r:task_id=asyncio.run(run(r))
    with open_cloud_runtime(settings) as r:
        value=asyncio.run(r.observations.usage(task_id))
        assert value['capture']['capture_incomplete'] and value['status_counts']['unconfirmed']==1
        assert value['metrics']['input_tokens']['known_sum'] is None
