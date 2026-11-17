"""Fixed probe materials exercise manual compaction through the real runtime."""
import asyncio
import json
import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent.model_access import ModelCatalog
from test_context_inspector import model_with_limits
from test_context_calibration_runtime import identified
import scripts.probe_context_calibration as probe


@pytest.mark.parametrize('protocol', ['responses', 'anthropic'])
def test_probe_fixed_materials_reach_compaction_with_actual_config_budgets(monkeypatch,tmp_path,protocol):
    replies=[AIMessage(content='ACK',usage_metadata={'input_tokens':actual,'output_tokens':1,'total_tokens':actual+1})
        for actual in (10416,13881,14132,16984,10000,4000,4000)]
    replies[2].content=[{'type':'function_call','id':'fc_read','call_id':'read','name':'read',
        'arguments':json.dumps({'path':'calibration.txt'})}]
    replies[0].content=[{'type':'reasoning','summary':[]},{'type':'text','text':'ACK'}]
    replies[2].tool_calls=[{'id':'read','name':'read','args':{'path':'calibration.txt'}}]
    if protocol == 'anthropic':
        from anthropic.types import Message
        from langchain_anthropic import ChatAnthropic
        data = Message(id='msg_fixture', type='message', role='assistant', model='fixture',
            stop_reason='tool_use', content=[{'type': 'tool_use', 'id': 'read', 'name': 'read', 'input': {'path': 'calibration.txt'}}],
            usage={'input_tokens': 14132, 'output_tokens': 1})
        replies[2] = ChatAnthropic(model='fixture', api_key='synthetic')._format_output(data).generations[0].message
    replies[4].content=probe.FACT
    chosen=identified(model_with_limits(replies))
    chosen.metadata.update(naiyue_compaction_reserve={'tokens':16384,'source':'default'},
        naiyue_compaction_keep_recent={'tokens':20000,'source':'default'},
        naiyue_compaction_summary_output={'tokens':2048,'source':'default'})
    monkeypatch.setattr(probe,'load_model_catalog',lambda _:ModelCatalog('A',{'A':chosen}))
    monkeypatch.setattr(probe,'guard_catalog',lambda *a:None)
    path=tmp_path/'report.json'
    code=asyncio.run(probe.run_probe('unused','A',path,live=True))
    report=json.loads(path.read_text())
    assert code==0, report['operations']
    assert report['compaction_status']=='applied'
    assert report['quality_checks']['fixed_fact_retained']


@pytest.mark.parametrize('case', ['recovered_overflow', 'unexpected_read', 'unknown_tool'])
def test_probe_recovery_and_tool_deviation_boundaries(monkeypatch, tmp_path, case):
    from test_async_application import StreamingToolModel
    from test_async_context_chain import overflow_error
    sends, budget_holder = [], []
    def ack(actual):
        return AIMessage(content='ACK', usage_metadata={'input_tokens': actual, 'output_tokens': 1, 'total_tokens': actual + 1})
    read = ack(14132)
    read.tool_calls = [{'id': 'read', 'name': 'read', 'args': {'path': 'calibration.txt'}}]
    replies = [ack(10416), ack(13881), read, ack(16984), ack(4000), ack(4000)]
    if case == 'recovered_overflow':
        replies.insert(1, overflow_error())
    else:
        deviation = ack(10416)
        deviation.tool_calls = [{'id': 'deviation', 'name': 'read' if case == 'unexpected_read' else 'unknown', 'args': {'path': 'calibration.txt'}}]
        replies.insert(0, deviation)
    class Model(StreamingToolModel):
        async def _next(self, messages):
            budget_holder[0].reserve('A')
            sends.append('summary' if str(messages[0].content).startswith('Summarize completed') else 'agent')
            if sends[-1] == 'summary':
                return ack(10000).model_copy(update={'content': probe.FACT})
            return await super()._next(messages)
    base = identified(model_with_limits([]))
    base.metadata.update(naiyue_compaction_reserve={'tokens': 16384, 'source': 'config'},
        naiyue_compaction_keep_recent={'tokens': 500, 'source': 'config'},
        naiyue_compaction_summary_output={'tokens': 2048, 'source': 'config'})
    chosen = Model(replies=replies, metadata=base.metadata)
    monkeypatch.setattr(probe, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': chosen}))
    monkeypatch.setattr(probe, 'guard_catalog', lambda catalog, budget: budget_holder.append(budget))
    path = tmp_path / 'report.json'
    code = asyncio.run(probe.run_probe('unused', 'A', path, live=True))
    report = json.loads(path.read_text())
    if case == 'recovered_overflow':
        assert code == 0, report
        assert len(sends) == report['http']['used'] == 9
        assert all(s['status'] == 'completed' for s in report['scenarios'])
        assert any(o['event'] == 'context_recovery_finished' and o['status'] == 'recovered' for o in report['operations'])
        assert report['metrics']['exclusions']['failed'] == 1
    else:
        assert code == 1, report
        assert sends == ['agent']
        assert report['http']['used'] == 1
        assert report['scenarios'][0]['status'] == 'failed'
        assert all(s['status'] == 'not_run' for s in report['scenarios'][1:])


def test_c3_automatic_compaction_keeps_tool_evidence(monkeypatch, tmp_path):
    from test_async_application import StreamingToolModel
    def ack(n, text='ACK'):
        return AIMessage(content=text, usage_metadata={'input_tokens': n, 'output_tokens': 1, 'total_tokens': n + 1})
    read = ack(500)
    read.tool_calls = [{'id': 'read', 'name': 'read', 'args': {'path': 'calibration.txt'}}]
    budgets, sends = [], []
    class Model(StreamingToolModel):
        async def _next(self, messages):
            budgets[-1].reserve('A')
            purpose = 'summary' if str(messages[0].content).startswith('Summarize completed') else 'agent'
            sends.append(purpose)
            return ack(10000, probe.FACT) if purpose == 'summary' else await super()._next(messages)
    base = identified(model_with_limits([]))
    base.metadata.update(naiyue_compaction_reserve={'tokens': 103000, 'source': 'config'},
        naiyue_compaction_keep_recent={'tokens': 1, 'source': 'config'},
        naiyue_compaction_summary_output={'tokens': 2048, 'source': 'config'})
    chosen = Model(replies=[ack(10416), ack(30000), read, ack(4000), ack(4000), ack(4000)], metadata=base.metadata)
    monkeypatch.setattr(probe, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': chosen}))
    monkeypatch.setattr(probe, 'guard_catalog', lambda catalog, budget: budgets.append(budget))
    path = tmp_path / 'report.json'
    code = asyncio.run(probe.run_probe('unused', 'A', path, live=True))
    report = json.loads(path.read_text())
    assert report['scenarios'][2]['status'] == 'completed', report
    assert sends[:5] == ['agent', 'agent', 'summary', 'agent', 'agent']
    assert any(o['status'] == 'applied' for o in report['operations'])
    assert code in (0, 3), report
