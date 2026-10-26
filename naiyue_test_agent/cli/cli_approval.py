"""CLI group selection and submission; saved application facts remain authoritative."""

import json
import logging

from naiyue_test_agent.service.settings import ServiceError

logger = logging.getLogger(__name__)


def report_command_failure(error, action, write):
    logger.warning('cli_command_failed action=%s code=%s', action, error.code)
    if error.code in ('stale_approval', 'stale_run', 'session_busy', 'approval_required',
            'session_not_found', 'run_not_found', 'subagent_not_found', 'invalid_request',
            'invalid_request_id', 'request_id_conflict', 'unauthorized', 'invalid_origin', 'invalid_host'):
        write(f'{action}请求未被接受（{error.code}），请查询当前状态。')
    else:
        write(f'{action}请求结果未确认（{error.code}），请查询当前状态后核对，勿直接重发。')


def show_approval_batches(batches, write):
    for batch in batches:
        write(f"审批批次 {batch['approval_batch_id']} · {batch['agent_path']}")
        for index, item in enumerate(batch['items'], 1):
            target = json.dumps(item['target'], ensure_ascii=False)
            workspace = f" 工作区={json.dumps(item['workspace'], ensure_ascii=False)} 命令=" if 'workspace' in item else ' → '
            write(f"待审批 {index}: {item['tool_name']}{workspace}{target}")
            if source := item.get('mcp_source'):
                write(f"MCP 来源：{source['server_id']}/{source['remote_tool']}；外部工具，副作用未知。")
            if item.get('remember_scope') == 'tool':
                write('记住此项：本会话始终允许此工具；参数可变化，根与子任务共用；配置或工具定义改变后失效。')
    if batches:
        write('用 :approve [批次ID] / :deny [批次ID]；多项用 :review [批次ID] approve reject ...；'
              '--remember=1,2 仅在本会话记住指定批准项；:approvals 查看当前批次')


def handle_approval(app, session_id, line, *, worker, interactive, write, start_root):
    parts = line.split()
    command, tokens = parts[0], parts[1:]
    batches = app.pending_approvals(session_id)
    # A pipe can provide its approval command before the model reaches HITL.
    while command != ':approvals' and not interactive and not batches and worker is not None and worker.is_alive():
        worker.join(0.05)
        batches = app.pending_approvals(session_id)
    if command == ':approvals':
        show_approval_batches(batches, write)
        if not batches: write('当前没有待审批的工具调用')
        return
    if not batches:
        write('当前没有待审批的工具调用')
        return
    batch = next((batch for batch in batches if tokens and tokens[0] == batch['approval_batch_id']), None)
    if batch is not None:
        tokens.pop(0)
    elif len(batches) == 1:
        batch = batches[0]
    else:
        write('当前有多个待审批批次，请在命令后指定完整批次ID')
        show_approval_batches(batches, write)
        return
    remember, choices = [], []
    try:
        for token in tokens:
            if token.startswith('--remember='):
                remember.extend(int(index) for index in token.split('=', 1)[1].split(','))
            else:
                choices.append(token)
        if command != ':review':
            if choices: raise ValueError('Unexpected approval arguments')
            choices = ['approve' if command == ':approve' else 'reject']
        choices = ['reject' if choice == 'deny' else choice for choice in choices]
        if len(choices) != len(batch['items']) or any(choice not in ('approve', 'reject') for choice in choices):
            raise ValueError('Incomplete approval choices')
        if any(index < 1 or index > len(choices) for index in remember):
            raise ValueError('Invalid remembered row')
        submission = dict(approval_batch_id=batch['approval_batch_id'], origin_run_id=batch['origin_run_id'],
            decisions=[{'approval_id': row['approval_id'], 'decision': choice}
                       for row, choice in zip(batch['items'], choices, strict=True)],
            remember_approved=[batch['items'][index - 1]['approval_id'] for index in remember])
        if batch['kind'] == 'live_child':
            app.approve(session_id, submission)
            write(f"子 Agent 审批已提交：{batch['approval_batch_id']}")
        else:
            if worker is not None:
                worker.join()
            receipt = app.approve(session_id, submission)
            start_root(receipt)
            return batch['approval_batch_id']
    except ValueError:
        write(f"需要为 {len(batch['items'])} 个调用分别给出决定（approve/reject）；记住项必须是本批准项")
    except ServiceError as error:
        report_command_failure(error, '审批', write)


def settle_cli_worker(app, session_id, worker, write, *, stop_waits=True, stop_active=False):
    """EOF has no approval input left; wake child waits before joining the root."""
    stopped = False
    while worker is not None and worker.is_alive():
        batches = app.pending_approvals(session_id)
        if batches and not stop_waits:
            return False
        if not stopped and (stop_active or any(batch['kind'] == 'live_child' for batch in batches)):
            try:
                app.control(session_id, 'stop')
            except ServiceError as error:
                report_command_failure(error, '停止', write)
                raise
            else:
                stopped = True
                write('输入已结束，已请求停止当前运行，正在收尾')
        worker.join(0.05)
    return True


def handle_scope_stop(app, session_id, target, *, worker, write, start_root):
    if target != 'root':
        try:
            app.control(session_id, 'stop', agent_path=target)
        except ServiceError as error:
            report_command_failure(error, '子任务停止', write)
        else:
            write(f'已请求停止子任务：{target}；其他子任务继续')
        return True
    pending = next((batch for batch in app.pending_approvals(session_id) if batch['kind'] == 'paused_root'), None)
    if pending is not None:
        if worker is not None:
            worker.join()
        try:
            start_root(pending)
        except ServiceError as error:
            report_command_failure(error, '停止待审批运行', write)
        return True
    return False
