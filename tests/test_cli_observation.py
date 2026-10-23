"""Observation commands read bounded saved facts without running the Agent."""

import asyncio
from types import SimpleNamespace

import pytest


def bucket(duration='9007199254740993001'):
    metric = {'known_sum': '0', 'reported_calls': 1, 'unknown_calls': 0}
    return {'recorded_calls': 2, 'status_counts': {'returned': 1, 'failed': 1},
        'duration': {'known_sum_ms': duration, 'reported_calls': 1, 'unknown_calls': 1},
        'metrics': dict.fromkeys(('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'), metric)}


def usage():
    row = bucket()
    return {**row, 'session_id': 's', 'as_of': '2026-10-20T00:00:00Z',
        'activities': [{**row, 'id': 'root_agent'}], 'scopes': [{**row, 'id': 'root'}],
        'models': {'items': [{**row, 'model_id': 'A'}], 'next_after': 'gateway/model x',
                   'unattributed': {**row, 'recorded_calls': 0}},
        'capture': {'incomplete_runs': 1, 'dropped_events': '2'}}


def summary(rid='r', *, capture='partial'):
    return {'session_id': 's', 'run_id': rid, 'status': 'failed', 'duration_ms': 0,
        'started_at': '2026-10-20T00:00:00Z', 'capture_state': 'closed', 'capture_incomplete': True,
        'dropped_events': 1, 'http_attempts': {'status': capture, 'observed_attempts': 3,
                                           'response_headers': 2, 'unconfirmed': 1}}


def test_usage_command_preserves_big_duration_unknown_counts_groups_and_cursor():
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    lines, calls = [], []
    app = SimpleNamespace(get_model_usage=lambda sid, **q: calls.append((sid, q)) or usage())
    handle_observation_command(app, 's', ':usage --after-model A', lines.append)
    out = '\n'.join(lines)
    assert calls == [('s', {'after_model': 'A'})]
    assert '9007199254740993.001 秒' in out and '另有 1 次未提供' in out
    assert '主智能体' in out and '根运行' in out and 'A' in out
    assert ":usage --after-model 'gateway/model x'" in out
    assert '已知丢失 2' in out and '并发' in out


@pytest.mark.parametrize('capture', ['complete', 'partial', 'unavailable'])
def test_diagnostic_list_retains_status_zero_and_unknown_http(capture):
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    lines, calls = [], []
    item = summary(capture=capture)
    app = SimpleNamespace(list_diagnostic_runs=lambda sid, **q: calls.append((sid, q)) or
                          {'session_id': sid, 'runs': [item], 'next_before': 7})
    handle_observation_command(app, 's', ':diagnostics --before 9', lines.append)
    out = '\n'.join(lines)
    assert calls == [('s', {'before': 9})]
    assert 'r' in out and '失败' in out and '0 毫秒' in out and ':diagnostics --before 7' in out
    if capture == 'unavailable':
        assert 'HTTP 采集不可用' in out and 'HTTP 尝试 3' not in out
    else:
        assert 'HTTP 尝试 3' in out and '响应头 2' in out and '未确认 1' in out
    if capture == 'partial':
        assert '采集不完整' in out


def test_detail_shows_headers_and_model_failure_independently_with_child_identity():
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    lines, calls = [], []
    child = {'agent_path': 'root/tester/id', 'agent_id': 'id', 'agent_run_id': 'child-run'}
    events = [{'session_id': 's', 'run_id': 'r', 'sequence': 2, 'event_type': 'model_http_attempt_finished', 'source': child,
               'data': {'invocation_id': 'i', 'attempt_id': 'a', 'status': 'response_headers',
                        'http_status': 200, 'response_headers_ms': 0}},
              {'session_id': 's', 'run_id': 'r', 'sequence': 3, 'event_type': 'model_call_finished', 'source': child,
               'data': {'invocation_id': 'i', 'status': 'failed', 'duration_ms': None,
                        'body': 'PRIVATE_BODY', 'tool_name': '\x1b[31m\nforged'}}]
    app = SimpleNamespace(get_diagnostic_events=lambda sid, rid, **q: calls.append((sid, rid, q)) or
                          {'session_id': sid, 'run_id': rid, 'summary': summary(),
                           'events': events, 'next_after': 3})
    handle_observation_command(app, 's', ':diagnostics r --after 1', lines.append)
    out = '\n'.join(lines)
    assert calls == [('s', 'r', {'after': 1})]
    assert '200' in out and '失败' in out and '0 毫秒' in out and '未提供' in out
    assert 'root/tester/id' in out and 'child-run' in out and 'i' in out and 'a' in out
    assert ':diagnostics r --after 3' in out and 'PRIVATE_BODY' not in out
    assert '\x1b' not in out and '\nforged' not in out


@pytest.mark.parametrize('line', [':usage extra', ':usage --after-model', ':usage --after-model A --after-model B',
    ':diagnostics --before 0', ':diagnostics --before 9223372036854775808', ':diagnostics r --before 1',
    ':diagnostics --after 1', ':diagnostics r --after -1', ':diagnostics r --after 1 --after 2',
    ':diagnostics a b', ':diagnostics "unterminated'])
def test_invalid_command_does_not_query_or_exit(line):
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    lines = []
    handle_observation_command(SimpleNamespace(), 's', line, lines.append)
    assert '用法' in '\n'.join(lines)


@pytest.mark.parametrize('line', [':usage', ':diagnostics', ':diagnostics r'])
def test_empty_records_are_reported_without_inventing_statistics(line):
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    lines, calls = [], []
    empty_usage = {**usage(), 'recorded_calls': 0}
    def read(value):
        def query(*args, **kwargs):
            calls.append((args, kwargs))
            return value
        return query
    app = SimpleNamespace(get_model_usage=read(empty_usage),
        list_diagnostic_runs=read({'session_id': 's', 'runs': [], 'next_before': None}),
        get_diagnostic_events=read({'session_id': 's', 'run_id': 'r', 'summary': summary(),
                                  'events': [], 'next_after': None}))
    handle_observation_command(app, 's', line, lines.append)
    assert len(calls) == 1
    expected = {':usage': '暂无已保存的调用用量记录', ':diagnostics': '暂无已保留的运行诊断',
                ':diagnostics r': '暂无本页事件'}
    assert expected[line] in '\n'.join(lines)
    assert '查询不可用' not in '\n'.join(lines)


@pytest.mark.parametrize('foreign', ['session', 'run', 'event'])
def test_foreign_result_is_not_displayed(foreign):
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    value = {'session_id': 's', 'run_id': 'r', 'summary': summary(), 'events': [], 'next_after': None}
    if foreign == 'event':
        value['events'] = [{'session_id': 'other', 'run_id': 'r', 'sequence': 1,
                            'event_type': 'model_call_started', 'data': {'model_id': 'PRIVATE_FOREIGN'}}]
    else:
        value['session_id' if foreign == 'session' else 'run_id'] = 'PRIVATE_FOREIGN'
    lines = []
    app = SimpleNamespace(get_diagnostic_events=lambda *a, **q: value)
    handle_observation_command(app, 's', ':diagnostics r', lines.append)
    assert '读取失败' in '\n'.join(lines) and 'PRIVATE_FOREIGN' not in '\n'.join(lines)


def test_query_failure_is_safe_and_original_exception_is_not_printed(caplog):
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    def fail(*a, **q): raise OSError('PRIVATE_SQL_BODY')
    lines = []
    handle_observation_command(SimpleNamespace(get_model_usage=fail), 's', ':usage', lines.append)
    assert '读取失败' in '\n'.join(lines)
    assert 'PRIVATE_SQL_BODY' not in '\n'.join(lines) + caplog.text
    assert 'cli_observation_read_failed' in caplog.text


def test_async_bridge_delegates_reads_on_its_existing_loop(monkeypatch):
    from naiyue_test_agent.cli.cli_async import AsyncCliApplication
    bridge = object.__new__(AsyncCliApplication)
    calls = []
    async def read(*args, **kwargs):
        calls.append((args, kwargs)); return 'saved'
    bridge._app = SimpleNamespace(aget_model_usage=read, alist_diagnostic_runs=read, aget_diagnostic_events=read)
    bridge._submit = lambda call: asyncio.run(call())
    assert bridge.get_model_usage('s', after_model='A') == 'saved'
    assert bridge.list_diagnostic_runs('s', before=9) == 'saved'
    assert bridge.get_diagnostic_events('s', 'r', after=4) == 'saved'
    assert calls == [(('s',), {'model_limit': 20, 'after_model': 'A'}),
        (('s',), {'limit': 50, 'before': 9}), (('s', 'r'), {'limit': 50, 'after': 4})]


def test_chat_dispatches_queries_without_model_calls(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent import application
    from naiyue_test_agent.cli import chat as cli
    from naiyue_test_agent.model_access import ModelCatalog
    from test_application_cli import RecordingFake, feed
    from langchain_core.messages import AIMessage
    model = RecordingFake(responses=[AIMessage(content='unused')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, ':usage', ':diagnostics', ':quit')
    assert cli.main(['--config', 'unused', '--session-db', str(tmp_path / 'sessions.sqlite'),
                     '--workspace', str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert '暂无已保存的调用用量记录' in out and '暂无已保留的运行诊断' in out
    assert '未知命令' not in out and model.seen == []


def test_event_identity_is_not_translated_as_a_status():
    from naiyue_test_agent.cli.cli_observation import handle_observation_command
    lines = []
    value = {'session_id': 's', 'run_id': 'r', 'summary': summary(), 'next_after': None,
        'events': [{'session_id': 's', 'run_id': 'r', 'sequence': 1, 'source': None,
            'event_type': 'model_call_started', 'data': {'model_id': 'failed', 'invocation_id': 'summary'}}]}
    handle_observation_command(SimpleNamespace(get_diagnostic_events=lambda *a, **q: value), 's',
                               ':diagnostics r', lines.append)
    assert '模型 failed' in '\n'.join(lines) and '模型执行 summary' in '\n'.join(lines)
