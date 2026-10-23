"""Read-only views of saved usage and finite diagnostics on the existing CLI."""

import logging
import shlex
import unicodedata

from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.contracts.failures import safe_error_type


logger = logging.getLogger(__name__)
_HELP = (':usage [--after-model <model_id>]；:diagnostics [--before <cursor>]；'
         ':diagnostics <run_id> [--after <sequence>]')
_LABELS = {'root_agent': '主智能体', 'child_agent': '子智能体', 'summary': '压缩摘要',
    'auto_fast': 'Auto 快审', 'auto_detailed': 'Auto 详审', 'memory': '记忆整理', 'unattributed': '用途未归属',
    'root': '根运行', 'child': '子运行', 'started': '进行中', 'returned': '返回',
    'failed': '失败', 'cancelled': '取消', 'unconfirmed': '未确认', 'completed': '已完成',
    'stopped': '已停止', 'running': '运行中', 'interrupted': '已中断',
    'response_headers': '收到响应头', 'success': '成功', 'error_result': '错误结果',
    'not_executed': '未执行'}
_EVENTS = {'model_call_started': '模型开始', 'model_call_finished': '模型结束',
    'model_http_attempt_started': 'HTTP 尝试开始', 'model_http_attempt_finished': 'HTTP 尝试结束',
    'tool_call_started': '工具开始', 'tool_call_finished': '工具结束',
    'run_started': '运行开始', 'run_finished': '运行结束'}
_FIELDS = {'model_id': '模型', 'purpose': '用途', 'invocation_id': '模型执行', 'attempt_id': 'HTTP 尝试',
    'tool_name': '工具', 'tool_call_id': '工具调用', 'boundary': '边界', 'outcome': '结果',
    'status': '状态', 'error_type': '错误类型', 'reason': '原因', 'operation': '操作', 'trigger': '触发'}


def _text(value):
    if value is None:
        return '未提供'
    return ''.join(f'\\u{ord(ch):04x}' if unicodedata.category(ch).startswith('C') else ch for ch in str(value))


def _duration(value):
    if value is None:
        return '未提供'
    amount = int(value)
    if amount < 1000:
        return f'{amount} 毫秒'
    whole, rest = divmod(amount, 1000)
    return f'{whole}' + (f'.{rest:03d}'.rstrip('0') if rest else '') + ' 秒'


def _parse(line):
    command, *args = shlex.split(line)
    if command == ':usage':
        if not args:
            return command, None, None
        if len(args) == 2 and args[0] == '--after-model' and 1 <= len(args[1]) <= 128:
            return command, None, args[1]
        raise ValueError('usage_arguments')
    run_id = args.pop(0) if args and not args[0].startswith('--') else None
    if run_id is not None and not 1 <= len(run_id) <= 128:
        raise ValueError('run_identity')
    if not args:
        return command, run_id, None
    flag = '--after' if run_id is not None else '--before'
    if len(args) != 2 or args[0] != flag or not args[1].isascii() or not args[1].isdecimal():
        raise ValueError('diagnostic_arguments')
    cursor = int(args[1])
    if not (0 if run_id is not None else 1) <= cursor <= 2**63 - 1:
        raise ValueError('diagnostic_cursor')
    return command, run_id, cursor


def _bucket(row):
    duration = row.get('duration') or {}
    return (f"{row['recorded_calls']} 次调用 · 已记录模型调用耗时 {_duration(duration.get('known_sum_ms'))}"
            f" · 覆盖 {duration.get('reported_calls', 0)} 次 · 另有 {duration.get('unknown_calls', row['recorded_calls'])} 次未提供")


def _usage_lines(value):
    if not value['recorded_calls']:
        yield '暂无已保存的调用用量记录。'
    else:
        yield _bucket(value)
        for name, label in (('input_tokens', '总输入'), ('output_tokens', '输出'),
                            ('cache_read_tokens', '其中缓存读取'), ('cache_write_tokens', '其中缓存写入')):
            metric = value['metrics'][name]
            yield f"{label}：{_text(metric['known_sum'])} · 另有 {metric['unknown_calls']} 次未提供"
        for name in ('activities', 'scopes'):
            for row in value[name]:
                yield f"{_text(_LABELS.get(row['id'], row['id']))}：{_bucket(row)}"
        for row in value['models']['items']:
            yield f"模型 {_text(row['model_id'])}：{_bucket(row)}"
        unknown = value['models']['unattributed']
        if unknown['recorded_calls']:
            yield f'未归属模型：{_bucket(unknown)}'
    capture = value['capture']
    if capture['incomplete_runs']:
        yield f"记录缺口：{capture['incomplete_runs']} 次运行，已知丢失 {capture['dropped_events']} 条事实。"
    yield '仅汇总已保存记录；未知不记为零，并发调用耗时之和不等于运行经过时间。'
    if value['models']['next_after'] is not None:
        yield '下一页：' + _text(shlex.join([':usage', '--after-model', value['models']['next_after']]))


def _run_lines(row):
    yield f"运行 {_text(row['run_id'])} · {_text(_LABELS.get(row['status'], row['status']))} · 耗时{_duration(row.get('duration_ms'))}"
    if row.get('mode') == 'memory':
        yield '用途：记忆整理'
    yield '开始：' + _text(row.get('started_at'))
    http = row.get('http_attempts') or {}
    if http.get('status', 'unavailable') == 'unavailable':
        yield 'HTTP 采集不可用'
    else:
        yield (f"已记录 HTTP 尝试 {http['observed_attempts']} · 响应头 {http['response_headers']}"
               f" · 未确认 {http['unconfirmed']}")
        if http['status'] == 'partial':
            yield 'HTTP 采集不完整'
    if row.get('capture_incomplete'):
        yield f"运行诊断记录不完整，已知丢失 {row.get('dropped_events', 0)} 条事实。"


def _event_line(event):
    data, source = event['data'], event.get('source')
    parts = [f"#{event['sequence']} {_text(_EVENTS.get(event['event_type'], event['event_type']))}"]
    parts.append('主智能体' if source is None else
        f"{_text(source.get('agent_path'))} · 子运行 {_text(source.get('agent_run_id'))}")
    for name, label in _FIELDS.items():
        if name in data:
            value = _LABELS.get(str(data[name]), data[name]) if name in ('purpose', 'status', 'outcome') else data[name]
            parts.append(f"{label} {_text(value)}")
    if data.get('http_status') is not None:
        parts.append(f"HTTP {data['http_status']}")
    for name, label in (('duration_ms', '耗时'), ('response_headers_ms', '响应头耗时')):
        if name in data:
            parts.append(label + ' ' + _duration(data[name]))
    return ' · '.join(parts)


def handle_observation_command(app, session_id, line, write, *, persistent):
    try:
        command, run_id, cursor = _parse(line)
    except ValueError:
        write('用法：' + _HELP)
        return
    if not persistent:
        write('当前为临时会话，持久化用量与运行诊断查询不可用。')
        return
    try:
        if command == ':usage':
            value = app.get_model_usage(session_id, after_model=cursor)
        elif run_id is None:
            value = app.list_diagnostic_runs(session_id, before=cursor)
        else:
            value = app.get_diagnostic_events(session_id, run_id, after=cursor or 0)
        if value['session_id'] != session_id:
            raise ValueError('observation_session_mismatch')
        if command == ':usage':
            lines = list(_usage_lines(value))
        elif run_id is None:
            if any(row['session_id'] != session_id for row in value['runs']):
                raise ValueError('observation_run_mismatch')
            lines = [line for row in value['runs'] for line in _run_lines(row)]
            if not lines:
                lines = ['暂无已保留的运行诊断。']
            if value['next_before'] is not None:
                lines.append(f"更早运行：:diagnostics --before {value['next_before']}")
        else:
            if (value['run_id'] != run_id or value['summary']['session_id'] != session_id
                    or value['summary']['run_id'] != run_id or any(
                        e['session_id'] != session_id or e['run_id'] != run_id for e in value['events'])):
                raise ValueError('observation_event_mismatch')
            lines = [*_run_lines(value['summary']), *(_event_line(e) for e in value['events'])]
            if not value['events']:
                lines.append('暂无本页事件。')
            if value['next_after'] is not None:
                lines.append('下一页：' + _text(shlex.join([':diagnostics', run_id, '--after', str(value['next_after'])])))
        for output in lines:
            write(output)
        if command != ':usage':
            write('仅查看有限保留的运行诊断；收到响应头不代表模型执行成功。')
    except Exception as error:
        logger.warning('cli_observation_read_failed error_type=%s', safe_error_type(error))
        missing = isinstance(error, SessionPersistenceError) and error.args == ('diagnostic_run_not_found',)
        write('该运行诊断不可用。' if missing else '观测记录读取失败，可以重试；当前任务不会受到影响。')
