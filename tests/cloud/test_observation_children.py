"""Real registered child events keep their thread, parent call and usage scope."""
import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.cloud_runner.transport import RunnerClient
from naiyue_test_agent.contracts.cloud_runner import RunCommand,RunnerFrame
from naiyue_test_agent.model_access import ModelCatalog
from lifecycle_helpers import config
from observation_helpers import Socket
from test_runner_control import prepared
from test_async_application import StreamingToolModel,call
from test_subagent_execution import ChildModel


def test_actual_child_loop_is_observed_then_replaced_by_confirmed_archive(tmp_path):
    async def run(r):
        b=prepared(r);root=tmp_path/'root';root.mkdir();state=tmp_path/'runner';state.mkdir(mode=0o700)
        child=ChildModel(replies=[call('write',dict(path='child.txt',content='verified'),'child-write'),AIMessage(content='child report')])
        model=StreamingToolModel(replies=[call('subagent',dict(agent='tester',task='verify'),'delegate'),AIMessage(content='root done')])
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':model,'B':child})) as runner:
            connection=await r.control.attach(Socket(),b.token.get_secret_value(),RunnerClient(runner).hello())
            task=r.registry.get_task(b.task_id)
            receipt=await r.control.runs.start(b.task_id,dict(command_id=str(uuid4()),expected_generation=task.generation,
                expected_revision=task.revision,message='delegate work',model_id='A'))
            assert (await runner.execute(RunCommand.model_validate(receipt.result['command'])))['status']=='completed'
            while batch:=runner.observations.drain():
                await r.control.receive(connection,RunnerFrame.validate_python(dict(kind='observations',batch=batch,**connection.fence)))
            root_view=await r.observations.snapshot(b.task_id)
            source=next(t for t in root_view['live']['threads'] if t['source'] is not None)
            assert source['source']['parent_tool_call_id']=='delegate'
            child_view=await r.observations.snapshot(b.task_id,thread_id=source['thread_id'])
            assert any(m['content']=='child report' for m in child_view['live']['messages'])
            usage=await r.observations.usage(b.task_id)
            assert usage['recorded_calls']==4 and {s['id'] for s in usage['scopes']}=={'root','child'}
            from naiyue_test_agent.contracts.cloud_events import EventBatch
            while events:=runner.journal.pending():
                packet=EventBatch(task_id=b.task_id,generation=b.generation,epoch=b.state_epoch,events=events)
                ack=r.append_events(b.token.get_secret_value(),packet.model_dump());runner.journal.ack(ack['last_seq'])
                r.observations.changed(b.task_id,archive=ack)
            child_view=await r.observations.snapshot(b.task_id,thread_id=source['thread_id'])
            assert child_view['archive']['messages'][-1]['content']=='child report'
            assert child_view['live']['messages']==[] and (root/'child.txt').read_text()=='verified'
        await r.observations.aclose();await r.control.aclose()
    settings=config(tmp_path,subagents=[dict(name='tester',description='verify',system_prompt='Test role',tool_names=['write'],model_id='B')])
    with open_cloud_runtime(settings) as r:asyncio.run(run(r))
