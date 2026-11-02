"""Required large history uses the real Runner/unit-of-work, not an observer."""
import asyncio

from langchain_core.messages import AIMessage

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.model_access import ModelCatalog
from test_runner_kernel import bootstrap,request
from test_async_application import StreamingToolModel


def test_runner_large_reply_parts_and_original_identity_are_durable(tmp_path):
    async def run():
        b=bootstrap().model_copy(update={'max_frame_bytes':4096,'batch_events':2})
        root=tmp_path/'root';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        content='结果\\\"'*5000
        async with open_runner(b,root,state,catalog=ModelCatalog('A',{'A':StreamingToolModel(replies=[AIMessage(content=content)])})) as runner:
            result=await runner.execute(request())
            assert result['status']=='completed'
            events=[]
            while pending:=runner.journal.pending():
                assert all(len(e.model_dump_json().encode())+1024<=4096 for e in pending)
                events.extend(pending);runner.journal.ack(pending[-1].seq)
            assert any(e.payload['record']=='message_part' for e in events)
            assert events[-1].payload['record']=='run_finished'
            assert [e.seq for e in events]==list(range(1,len(events)+1))
            raw=''.join(e.payload['text'] for e in events if e.payload['record']=='message_part')
            assert content in __import__('json').loads(raw)['data']['content']
    asyncio.run(run())
