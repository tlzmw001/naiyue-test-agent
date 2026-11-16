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


def test_runner_calibration_survives_required_parts_and_reopen(tmp_path):
    from uuid import uuid4
    from naiyue_test_agent.agent_runtime.context_calibration import KEY, measure_request
    from test_context_calibration_runtime import identified
    async def run():
        b = bootstrap().model_copy(update={'max_frame_bytes': 4096, 'batch_events': 2})
        root = tmp_path / 'root'; root.mkdir()
        state = tmp_path / 'state'; state.mkdir(mode=0o700)
        reply = AIMessage(content='large result ' * 3000,
            usage_metadata={'input_tokens': 300, 'output_tokens': 1, 'total_tokens': 301})
        model = identified(StreamingToolModel(replies=[reply]))
        async with open_runner(b, root, state, catalog=ModelCatalog('A', {'A': model})) as runner:
            assert (await runner.execute(request()))['status'] == 'completed'
            events = []
            while pending := runner.journal.pending():
                events.extend(pending)
                runner.journal.ack(pending[-1].seq)
            raw = ''.join(e.payload['text'] for e in events if e.payload['record'] == 'message_part')
            metadata = __import__('json').loads(raw)['data']['response_metadata']
            record = metadata[KEY]
            assert record['agent']['text']['actual_input_tokens'] == 300
        next_model = identified(StreamingToolModel(replies=[AIMessage(content='continued')]))
        b = b.model_copy(update={'generation': 2, 'boot_id': str(uuid4()), 'token': 'renewed-test-token'})
        async with open_runner(b, root, state, catalog=ModelCatalog('A', {'A': next_model})) as runner:
            assert (await runner.execute(request()))['status'] == 'completed'
            previous = [m.response_metadata[KEY] for m in next_model.seen[0] if KEY in m.response_metadata]
            assert previous == [record]
            measurement = measure_request(next_model, next_model.seen[0][0], next_model.seen[0][1:], [])
            assert measurement.source_envelope == record
            assert measurement.budget.method == 'ratio_estimate'
    asyncio.run(run())
