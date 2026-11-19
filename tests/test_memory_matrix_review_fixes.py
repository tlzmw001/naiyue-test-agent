"""Wiki engineering reports retain actual rows and surface execution failures."""
import json

import pytest


@pytest.mark.parametrize('fault', ['body', 'source'])
def test_worker_report_does_not_hide_corrupted_core_write(monkeypatch, fault):
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
    from test_memory_space_time_evaluation import _background_with_followup

    original = WikiWriter.apply

    def broken(self, **changes):
        outcome = original(self, **changes)
        for item in changes.get('memories', ()):
            row = item['row']
            if fault == 'body':
                self._connection.execute('UPDATE naiyue_memories SET body=? WHERE memory_id=?',
                                         ('被改坏的正文', row['memory_id']))
            else:
                self._connection.execute('DELETE FROM naiyue_memory_sources WHERE memory_id=?',
                                         (row['memory_id'],))
        return outcome

    monkeypatch.setattr(WikiWriter, 'apply', broken)
    report, _ = _background_with_followup(AIMessage(content='已读取。'))
    assert report['semantic_review'] is None
    observed = report['observations']
    if fault == 'body':
        assert observed['memories'][0]['body'] == '被改坏的正文'
    else:
        assert report['status'] == 'failed'
        assert observed['sources'] == [] and observed['failure_reason'] == 'memory_source_projection_missing'


def test_read_failure_blocks_runtime_cli_success_but_preserves_report(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    from scripts import memory_wiki_runtime_evaluation as runner
    from scripts.memory_wiki_evaluation import main
    from test_memory_foreground_evaluation import fake_catalog

    fake_catalog(monkeypatch, [AIMessage(content='已检查') for _ in range(8)])
    execute = runner.foreground_case

    async def fail_read(case, *args):
        if case['case_id'] == 'W13':
            raise OSError('PRIVATE')
        return await execute(case, *args)

    monkeypatch.setattr(runner, 'foreground_case', fail_read)
    result = main(['--mode', 'foreground', '--split', 'calibration', '--output-dir', str(tmp_path)])
    assert result == 1
    report = json.loads((tmp_path/'report.json').read_text())
    assert len(report['cases']) == 8
    failed, = [case for case in report['cases'] if case['status'] == 'failed']
    assert failed['case_id'] == 'W13' and failed['observations']['error_type'] == 'OSError'
    assert 'PRIVATE' not in json.dumps(report)
