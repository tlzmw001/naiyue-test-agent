"""Independent terminal management; no Agent request or tool approval is created."""

import json
import logging
import re

from naiyue_test_agent.service.settings import ServiceError
from urllib.parse import urlencode


logger = logging.getLogger(__name__)
USAGE = '用法：:tasks [before]；:task output ID；:task stop ID（ID为32位十六进制）'


def _text(value):
    # Preserve newlines, but do not execute terminal control bytes from commands.
    return '\n'.join(json.dumps(line, ensure_ascii=False)[1:-1] for line in str(value).split('\n'))


def _status(row, emit):
    if row.get('kind') == 'memory':
        result = {'completed': '已完成', 'partial': '部分完成', 'failed': '失败', 'stopped': '已停止'}
        status = result.get(row.get('result_status'), row['status'])
        emit(_text(f'{row["task_id"]}  会话 {row["session_id"]}  {status}\n'
            f'记忆整理: {row["command_preview"]}\n'
            f'结束原因：{row["termination"]}；收尾已确认：{row["cleanup_confirmed"]}'))
        return
    emit(_text(f'{row["task_id"]}  会话 {row["session_id"]}  {row["status"]}\n'
        f'{row["shell"]}: {row["command_preview"]}\n'
        f'退出码：{row["exit_code"]}；结束原因：{row["termination"]}；清理已确认：{row["cleanup_confirmed"]}'))


def handle_background_command(app, line, emit):
    parts = line.split()
    valid_id = lambda value: re.fullmatch(r'[0-9a-f]{32}', value) is not None
    listing = parts[0] == ':tasks' and len(parts) in (1, 2) and (len(parts) == 1 or valid_id(parts[1]))
    single = len(parts) == 3 and parts[:2] in ([':task', 'output'], [':task', 'stop']) and valid_id(parts[2])
    if not listing and not single:
        emit(USAGE)
        return
    try:
        if listing:
            result = app.request('GET', '/background-tasks' + ('?' + urlencode({'before': parts[1]}) if len(parts) == 2 else ''))
            emit(f'后台活动任务：{result["active_count"]}')
            if not result['items']:
                emit('没有后台任务')
            for row in result['items']:
                _status(row, emit)
            if result['next_before']:
                emit(f'下一页：:tasks {result["next_before"]}')
            return
        if parts[1] == 'stop':
            result = app.request('POST', f'/background-tasks/{parts[2]}/stop')
            _status(result, emit)
            if not result['cleanup_confirmed']:
                emit('记忆整理收尾尚未确认，请人工核对；任务记录仍保留。' if result.get('kind') == 'memory'
                    else '进程清理尚未确认，请人工核对；任务记录仍保留。')
        else:
            result = app.request('GET', f'/background-tasks/{parts[2]}/output')
            _status(result, emit)
            emit(_text(result['output']))
            if result['truncated']:
                emit('以上为有界结果摘要。' if result.get('kind') == 'memory' else '以上为有界输出尾部。')
            if result.get('kind') != 'memory':
                emit('归档引用：' + _text(json.dumps(result['archive_paths'], ensure_ascii=False)))
            if not result['output_available']:
                emit('日志文件不可用，以上为保留的内存尾部。')
    except ServiceError as error:
        if error.code == 'task_not_found':
            emit('任务不存在或不在当前范围。')
        elif error.code == 'task_cursor_invalid':
            emit('分页位置无效，请重新执行 :tasks。')
        else:
            logger.error('cli_background_task_failed code=%s', error.code)
            emit('后台任务操作未确认，请重新查询状态后核对。')
    except Exception as error:
        logger.error('cli_background_task_failed error_type=%s', type(error).__name__)
        emit('后台任务操作未确认，请重新查询状态后核对。')
