"""Root snapshots keep child histories independent and a bounded draft baseline."""

import asyncio
from types import SimpleNamespace
from naiyue_test_agent.web.run_delivery import DeliveredRun
from naiyue_test_agent.web.agent_projection import observe_agent


def test_root_summary_omits_all_tool_bodies_and_keeps_bounded_current_draft():
    async def scenario():
        settings = SimpleNamespace(event_capacity=256, max_frame_bytes=1024*1024)
        job = DeliveredRun('s', [], settings, lambda _: None)
        source = dict(agent_path='root/tester/A', agent_id='A', agent_run_id='child',
                      parent_run_id='r', parent_tool_call_id='outer')
        job.agents[source['agent_path']] = dict(source=source, status='running', batch_id='batch',
            tool_states={str(i): dict(tool_call_id=str(i), batch_id='batch', state='finished',
                args={'content': 'x'*64000}, result={'content': 'x'*64000}) for i in range(18)}, messages=[
                dict(id='done', type='ai', content='x'*64000, provisional=False, state='complete'),
                dict(id='draft', type='ai', content='first', provisional=True, state='draft', delta_index=1)])
        summary = job.view()['agents'][source['agent_path']]
        assert summary['messages'] == [job.agents[source['agent_path']]['messages'][1]]
        assert all('result' not in row and 'args' not in row for row in summary['tool_states'].values())
        job.agents[source['agent_path']]['messages'][1]['content'] = 'x' * (1024*1024)
        summary = job.view()['agents'][source['agent_path']]
        assert summary['messages'][0]['content_unavailable']
        assert summary['messages'][0]['delta_index'] == 1
    asyncio.run(scenario())


def test_child_message_projection_keeps_parent_batch_and_all_message_kinds():
    import json
    agents = {}
    source = dict(agent_path='root/tester/A', agent_id='A', agent_run_id='child',
                  parent_run_id='r', parent_tool_call_id='outer')
    def send(kind, data, batch='parent-one'):
        observe_agent(agents, {'source':source, 'event_type':kind, 'data':data, 'timestamp':'now'},
            [{'id':batch, 'type':'ai', 'tool_calls':[{'id':'outer'}]}])
    send('message_delta', {'message_id':'m', 'text':'first', 'index':1})
    send('message_completed', {'snapshot_json':json.dumps({'id':'m', 'type':'ai', 'content':'final'})}, 'parent-two')
    send('message_delta', {'message_id':'draft', 'text':'unfinished', 'index':1})
    send('run_finished', {'status':'failed'})
    child = agents[source['agent_path']]
    assert child['parent_batch_id'] == 'parent-one'
    assert [row['content'] for row in child['messages']] == ['final', 'unfinished']
    assert child['messages'][1]['state'] == 'unconfirmed'
