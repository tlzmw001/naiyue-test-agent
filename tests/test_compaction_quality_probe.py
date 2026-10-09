"""The bounded quality probe reports facts and phases without raw model text."""

import subprocess
import json
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from scripts import probe_compaction_quality as probe
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog


def test_fixed_fact_scoring_requires_valid_json_and_exact_values():
    answer = ('{"endpoint":"GET /api/v1/coupons/{user_id}",'
              '"priority":"blacklist_before_stock",'
              '"status":403,"case_id":"CASE-ALPHA-17"}')
    assert probe.score_answer(answer) == {
        'parse_ok': True, 'correct_count': 4,
        'matches': {key: True for key in probe.EXPECTED_FACTS},
    }
    wrong = probe.score_answer(answer.replace('CASE-ALPHA-17', 'CASE-OTHER'))
    assert wrong['parse_ok'] is True
    assert wrong['correct_count'] == 3
    assert wrong['matches']['case_id'] is False
    assert probe.score_answer('not JSON')['parse_ok'] is False


def test_timeout_terminates_worker_and_keeps_last_safe_phase():
    class WaitingWorker:
        returncode = 1
        terminated = False

        def communicate(self, timeout):
            if not self.terminated:
                raise subprocess.TimeoutExpired(
                    cmd=['probe'], timeout=timeout,
                    output=b'{"event":"context_summary_model_started"}\n',
                )
            return '', ''

        def terminate(self):
            self.terminated = True

    worker = WaitingWorker()
    result = probe.run_bounded_worker(
        ['probe'], 30,
        popen=lambda *_args, **_kwargs: worker,
    )
    assert worker.terminated is True
    assert result == {
        'status': 'timeout', 'last_event': 'context_summary_model_started',
    }


def test_fixed_case_uses_real_app_path_with_and_without_compaction(
    monkeypatch, capsys,
):
    class FactAwareFake(FakeMessagesListChatModel):
        summary_inputs: list = []
        normal_inputs: list = []

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, *args, **kwargs):
            if str(messages[0].content).startswith('Summarize completed'):
                self.summary_inputs.append(messages)
                answer = AIMessage(
                    content=' '.join(str(value) for value in probe.EXPECTED_FACTS.values()),
                    usage_metadata={'input_tokens': 100, 'output_tokens': 20,
                                    'total_tokens': 120},
                )
            else:
                self.normal_inputs.append(messages)
                history = ' '.join(str(message.content) for message in messages)
                found = all(str(value) in history for value in
                            probe.EXPECTED_FACTS.values())
                answer = AIMessage(content=(
                    '{"endpoint":"GET /api/v1/coupons/{user_id}",'
                    '"priority":"blacklist_before_stock",'
                    '"status":403,"case_id":"CASE-ALPHA-17"}'
                ) if found else '{}')
            return ChatResult(generations=[ChatGeneration(message=answer)])

    def catalog(_path):
        model = FactAwareFake(responses=[], metadata={
            'naiyue_model_limits': {
                'model_id': 'A', 'context_window_tokens': 128000,
                'max_output_tokens': 20, 'context_window_source': 'config',
                'max_output_source': 'config',
            },
            'naiyue_compaction_reserve': {'tokens': 20, 'source': 'config'},
            'naiyue_compaction_keep_recent': {'tokens': 100, 'source': 'config'},
            'naiyue_compaction_summary_output': {'tokens': 8, 'source': 'config'},
        })
        created.append(model)
        return ModelCatalog('A', {'A': model})

    created = []
    monkeypatch.setattr(application, 'load_model_catalog', catalog)
    for mode in ('compacted', 'baseline'):
        args = SimpleNamespace(config='unused', model_id='A', worker=mode,
                               summary_output_tokens=8)
        assert probe._run_case(args) == 0
        output = capsys.readouterr().out
        assert '"correct_count": 4' in output
        result = [json.loads(line) for line in output.splitlines()][-1]
        assert result.get('compaction_state_confirmed') is (mode == 'compacted')
        assert ('summary_duration_ms' in result and
                (isinstance(result['summary_duration_ms'], int)
                 if mode == 'compacted' else result['summary_duration_ms'] is None))
        assert ('"event": "context_summary_model_started"' in output) == (
            mode == 'compacted'
        )
        assert len(created[-1].summary_inputs) == (1 if mode == 'compacted' else 0)
        assert len(created[-1].normal_inputs) == 1


def test_probe_failure_reports_fixed_context_reason_without_summary_text(monkeypatch, capsys, caplog):
    import logging
    from test_context_compaction import _model

    model = _model([AIMessage(content='unused')], window=128000, output=2048,
                   summaries=[AIMessage(content='PRIVATE_SUMMARY', tool_calls=[
                       {'name': 'unexpected', 'args': {}, 'id': 'bad'},
                   ])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    with caplog.at_level(logging.INFO):
        assert probe._run_case(SimpleNamespace(config='unused', model_id='A', worker='compacted',
                                               summary_output_tokens=2048)) == 1
    output = capsys.readouterr().out
    result = json.loads(output.splitlines()[-1])
    assert result['context_failure_reason'] == 'summary_invalid'
    assert result['error_type'] == 'ContextCompactionError'
    assert 'PRIVATE_' not in output


def test_probe_with_large_output_cap_reserves_space_for_summary_and_retained_history(monkeypatch, capsys, caplog):
    import logging
    from test_context_compaction import _model
    from naiyue_test_agent.model_access import MODEL_LIMITS_METADATA_KEY

    summary = ' '.join(str(value) for value in probe.EXPECTED_FACTS.values()) + '\n' + 's' * 1600
    model = _model([AIMessage(content=json.dumps(probe.EXPECTED_FACTS))], window=128000, output=16384,
                   summaries=[AIMessage(content=summary)])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    with caplog.at_level(logging.INFO):
        assert probe._run_case(SimpleNamespace(config='unused', model_id='A', worker='compacted',
                                               summary_output_tokens=2048)) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['compaction_state_confirmed'] is True and result['correct_count'] == 4
    limits = model.metadata[MODEL_LIMITS_METADATA_KEY]
    assert limits['context_window_tokens'] - limits['max_output_tokens'] >= 2048 + 1024
    assert len(model.summary_inputs) == len(model.normal_inputs) == 1
