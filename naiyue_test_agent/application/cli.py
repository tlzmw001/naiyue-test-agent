"""Minimal synchronous terminal entry for one local Agent session."""

import argparse
import json
import logging
import sys
from pathlib import Path
from threading import Thread
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.application import (
    SessionBusyError, SessionManualReviewRequiredError, SessionPendingApprovalError,
    build_local_application,
)
from naiyue_test_agent.application.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.agent_runtime.tools import (
    create_session_edit_tool, create_session_find_tool, create_session_grep_tool,
    create_session_ls_tool, create_session_read_tool, create_session_write_tool,
    create_session_bash_tool,
)
from naiyue_test_agent.agent_runtime.tools.write import WriteOutcomeUnknown
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from naiyue_test_agent.agent_runtime.tools.bash_output import (
    clear_bash_outputs, preview_bash_outputs,
)
from naiyue_test_agent.agent_runtime.tools.read import resolve_workspace_root
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.application.cli_display import CliEventDisplay
from naiyue_test_agent.application.persistent import build_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError


logger = logging.getLogger(__name__)


def _print_pending_approvals(result, *, write=print) -> None:
    lines = []
    for index, item in enumerate(result.get('approval_requests', []), start=1):
        if item['tool_name'] == 'bash':
            command = json.dumps(item['target'], ensure_ascii=False)
            workspace = json.dumps(item['workspace'], ensure_ascii=False)
            lines.append(f"待审批 {index}: bash 工作区={workspace} 命令={command}")
        else:
            lines.append(f"待审批 {index}: {item['tool_name']} → {item['target']}")
    if result.get('approval_requests'):
        lines.append('单项使用 :approve 或 :deny；多项使用 :review approve reject ...')
    if lines:
        write('\n'.join(lines))


def _print_bash_partial_output(error: BashOutcomeUnknown, *, write=print) -> None:
    lines = []
    if error.partial_stdout or error.partial_stderr:
        lines.append('已捕获部分输出（可能不完整，不能据此判断命令是否完成）：')
        if error.output_truncated:
            lines.append('输出过长，仅保留各流末尾')
        if error.partial_stdout:
            lines.append('stdout末尾=' + json.dumps(error.partial_stdout, ensure_ascii=False))
        if error.partial_stderr:
            lines.append('stderr末尾=' + json.dumps(error.partial_stderr, ensure_ascii=False))
    if error.output_paths:
        lines.append('已保存输出：' + '; '.join(
            f'{stream}={path}' for stream, path in error.output_paths.items()
        ))
    if error.archive_reason is not None:
        lines.append(f'完整输出保存不全（{error.archive_reason}）')
    if lines:
        write('\n'.join(lines))


def _run_and_print(app, session_id: str, text: str, *, display=None) -> None:
    write = display.write if display is not None else print
    if display is not None:
        display.begin_call()
    request = HumanMessage(content=text, id=uuid4().hex)
    try:
        result = app.run(session_id, [request])
        if display is not None:
            display.finish_result(result, input_message_id=request.id)
            _print_pending_approvals(result, write=write)
            return
        messages = result['messages']
        start = next((index for index, message in enumerate(messages)
                      if message.id == request.id), None)
        if start is None:
            raise RuntimeError('Current input is missing from the run result')
        replies = [str(message.text) for message in messages[start + 1:]
                   if isinstance(message, AIMessage) and str(message.text).strip()]
    except SessionManualReviewRequiredError:
        write('本会话工具副作用待核对；请检查执行结果后重启会话')
        return
    except (WriteOutcomeUnknown, BashOutcomeUnknown) as error:
        logger.error('cli_run_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write('工具副作用无法确认；请先检查执行结果，勿直接重试')
        if isinstance(error, BashOutcomeUnknown):
            _print_bash_partial_output(error, write=write)
        return
    except Exception as error:
        logger.error('cli_run_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write(f'运行失败（{type(error).__name__}）')
        return

    if replies:
        for reply in replies:
            print(reply)
    elif not result.get('approval_requests'):
        print('本轮没有可展示的回答')
    _print_pending_approvals(result)
    if result.get('run_stopped'):
        print('已在当前 turn 后停止')


def _resume_and_print(app, session_id: str, decisions: list[str], *, display=None) -> None:
    write = display.write if display is not None else print
    if display is not None:
        display.begin_call()
    try:
        result = app.resume_tool_approval(session_id, decisions)
        if display is not None:
            display.finish_result(result, resume_start_index=result.get('resume_start_index'))
            _print_pending_approvals(result, write=write)
            return
        messages = result['messages']
        start = result['resume_start_index']
        replies = [str(message.text) for message in messages[start:]
                   if isinstance(message, AIMessage) and str(message.text).strip()]
    except SessionManualReviewRequiredError:
        write('本会话工具副作用待核对；请检查执行结果后重启会话')
        return
    except (WriteOutcomeUnknown, BashOutcomeUnknown) as error:
        logger.error('cli_approval_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write('工具副作用无法确认；请先检查执行结果，勿直接重试')
        if isinstance(error, BashOutcomeUnknown):
            _print_bash_partial_output(error, write=write)
        return
    except Exception as error:
        logger.error('cli_approval_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write(f'审批恢复失败（{type(error).__name__}）')
        return

    if replies:
        for reply in replies:
            print(reply)
    elif not result.get('approval_requests'):
        print('本轮没有可展示的回答')
    _print_pending_approvals(result)
    if result.get('run_stopped'):
        print('已在当前 turn 后停止')


def _pick_session(storage, selected_id):
    if selected_id:
        return selected_id
    rows = storage.list_sessions()
    if not rows:
        print('暂无历史会话')
        return None
    for index, row in enumerate(rows, 1):
        print(f'{index}. {row["session_id"]} 更新时间={row["updated_at"]} '
              f'模型={json.dumps(row["model_id"], ensure_ascii=False)} 状态={row["state"]} '
              f'工作区={json.dumps(row["workspace_root"], ensure_ascii=False)}')
    if not sys.stdin.isatty():
        print('请使用 --resume <session_id> 恢复指定会话')
        return None
    while True:
        try:
            choice = input('输入编号或完整会话 ID，空行取消：').strip()
        except EOFError:
            return None
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            return rows[int(choice) - 1]['session_id']
        if any(row['session_id'] == choice for row in rows):
            return choice
        print('会话选择无效，请重新输入')


def main(argv: list[str] | None = None) -> int:
    emit = print
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--no-session', action='store_true', help='Use an ephemeral in-memory session')
    parser.add_argument('--session-db', type=Path, help='Override the private session database path')
    parser.add_argument('--resume', nargs='?', const='', help='Choose a session, or resume the exact ID')
    parser.add_argument('--host-bash', action='store_true',
                        help='Expose approval-gated Bash with current user host access')
    args = parser.parse_args(argv)
    if args.no_session and (args.resume is not None or args.session_db is not None):
        parser.error('--no-session conflicts with --resume and --session-db')
    if args.host_bash and args.workspace is None and args.resume is None:
        parser.error('--host-bash requires --workspace')

    storage = None
    restored = None
    try:
        if not args.no_session:
            db = args.session_db or Path.home() / '.naiyue-test-agent' / 'sessions.sqlite'
            if args.resume is not None and not db.expanduser().exists() and not db.expanduser().is_symlink():
                if args.resume == '':
                    print('暂无历史会话')
                    return 0
                raise SessionPersistenceError('session_not_found')
            storage = SessionStorage(db)
            if args.resume is not None:
                selected = _pick_session(storage, args.resume)
                if selected is None:
                    storage.close()
                    return 0
                restored = storage.get(selected)
                if restored['state'] != 'ready':
                    print(f'该会话状态为 {restored["state"]}，首片不能自动恢复执行')
                    raise SessionPersistenceError('session_recovery_blocked')
                if args.workspace is not None and str(resolve_workspace_root(args.workspace)) != restored['workspace_root']:
                    raise SessionPersistenceError('workspace_mismatch')
                args.workspace = Path(restored['workspace_root']) if restored['workspace_root'] else None
        if args.host_bash and args.workspace is None:
            raise ValueError('Bash requires a workspace')
        tools = ([create_session_read_tool(), create_session_ls_tool(),
                  create_session_find_tool(), create_session_grep_tool(),
                  create_session_write_tool(), create_session_edit_tool(),
                  *([create_session_bash_tool()] if args.host_bash else [])]
                 if args.workspace is not None else [])
        factory = build_local_application if storage is None else build_persistent_application
        options = {} if storage is None else {'storage': storage}
        app = factory(
            args.config, tools=tools, system_prompt=BASE_SYSTEM_PROMPT,
            response_format=None, **options,
        )
        session_id = (app.restore_session(restored['session_id']) if restored is not None else
                      app.create_workspace_session(app.default_model_id, args.workspace)
                      if args.workspace is not None else app.create_session(app.default_model_id))
        display = (CliEventDisplay(session_id)
                   if callable(getattr(app, 'subscribe_events', None)) else None)
        unsubscribe = app.subscribe_events(display) if display is not None else None
        emit = display.write if display is not None else print
    except Exception as error:
        logger.error('cli_initialization_failed error_type=%s', type(error).__name__)
        emit(f'初始化失败（{type(error).__name__}）')
        if storage is not None:
            storage.__exit__(type(error), error, error.__traceback__)
        return 1

    worker: Thread | None = None
    try:
        emit(f'当前模型：{restored["model_id"] if restored else app.default_model_id}')
        emit(f'{"持久会话" if storage is not None else "临时会话"}：{session_id}')
        if args.host_bash:
            emit('已启用宿主机 Bash：每条命令需审批；命令可访问当前用户可访问的文件和网络，工作区不是沙箱。')
        interactive = sys.stdin.isatty()
        output_clear_plan = None
        output_workspace = resolve_workspace_root(args.workspace) if args.workspace is not None else None

        def start_run(text: str) -> None:
            nonlocal worker
            if interactive:
                worker = Thread(target=_run_and_print, args=(app, session_id, text),
                                kwargs={'display': display})
                worker.start()
            else:
                _run_and_print(app, session_id, text, display=display)

        def start_approval(decisions: list[str]) -> None:
            nonlocal worker
            if interactive:
                worker = Thread(target=_resume_and_print, args=(app, session_id, decisions),
                                kwargs={'display': display})
                worker.start()
            else:
                _resume_and_print(app, session_id, decisions, display=display)

        while True:
            try:
                line = input('> ').strip()
            except EOFError:
                if worker is not None:
                    worker.join()
                return 0

            if not line:
                continue
            command, *rest = line.split(maxsplit=1)
            if command != ':outputs':
                output_clear_plan = None
            if command == ':quit':
                if worker is not None:
                    worker.join()
                if args.workspace is not None and app.pending_tool_approvals(session_id):
                    emit('待审批未处理；退出后内存审批状态无法恢复')
                return 0
            if command == ':outputs':
                action = rest[0] if rest else ''
                if action not in ('clear', 'clear confirm'):
                    emit('用法：:outputs clear；确认后输入 :outputs clear confirm')
                    output_clear_plan = None
                    continue
                if output_workspace is None:
                    emit('当前没有工作区，无法清理 Bash 输出')
                    output_clear_plan = None
                    continue
                if worker is not None and worker.is_alive():
                    emit('当前运行尚未结束，不能清理 Bash 输出')
                    output_clear_plan = None
                    continue
                try:
                    if action == 'clear':
                        output_clear_plan = preview_bash_outputs(output_workspace)
                        if output_clear_plan.file_count:
                            emit(f'将清理 {output_clear_plan.file_count} 个文件，'
                                  f'{output_clear_plan.total_bytes} 字节；'
                                  '确认请输入 :outputs clear confirm')
                        else:
                            emit('没有可清理的 Bash 输出')
                            output_clear_plan = None
                    elif output_clear_plan is None:
                        emit('请先输入 :outputs clear 查看待清理文件')
                    else:
                        count, size = clear_bash_outputs(output_clear_plan)
                        output_clear_plan = None
                        emit(f'已清理 {count} 个文件，{size} 字节')
                except (OSError, ValueError, UnicodeError) as error:
                    output_clear_plan = None
                    logger.warning('bash_output_clear_failed error_type=%s', type(error).__name__)
                    emit('Bash 输出目录异常或预览后已变化，未完成清理')
                continue
            if command in (':approve', ':deny', ':review'):
                if worker is not None and worker.is_alive():
                    worker.join()
                pending = app.pending_tool_approvals(session_id) if args.workspace is not None else []
                if not pending:
                    emit('当前没有待审批的工具调用')
                    continue
                decisions = ([command.removeprefix(':')]
                             if command != ':review' else rest[0].split() if rest else [])
                if len(decisions) != len(pending) or any(
                    decision not in ('approve', 'reject', 'deny') for decision in decisions
                ):
                    emit(f'需要为 {len(pending)} 个调用分别给出决定（approve/reject）')
                    continue
                start_approval(['reject' if decision == 'deny' else decision
                                for decision in decisions])
                continue
            if command == ':stop':
                if worker is None or not worker.is_alive():
                    emit('当前没有运行')
                    continue
                try:
                    receipt_before = display.control_receipt_count('stop') if display is not None else 0
                    app.request_stop_after_turn(session_id)
                except SessionBusyError:
                    emit('停止请求未被接受（运行尚未开窗或已进入收尾）')
                else:
                    if display is None or display.control_receipt_count('stop') == receipt_before:
                        emit('已请求在当前 turn 后停止')
                continue
            if command == ':model':
                if not rest:
                    emit('请输入模型 ID')
                    continue
                model_id = rest[0].strip()
                try:
                    app.select_model(session_id, model_id)
                except ModelConfigError:
                    emit('模型 ID 不可用')
                except SessionPersistenceError as error:
                    logger.error('cli_model_selection_save_failed error_type=%s', type(error).__name__)
                    emit('模型选择未保存，继续使用原选择')
                else:
                    emit(f'模型已切换：{model_id}（下一次模型调用生效）')
                continue
            if command == ':follow':
                if not rest or not rest[0].strip():
                    emit('请输入 Follow-up 内容')
                    continue
                text = rest[0].strip()
                if args.workspace is not None and app.pending_tool_approvals(session_id):
                    emit('当前有待审批工具调用，请先处理审批')
                    continue
                if worker is not None and worker.is_alive():
                    try:
                        receipt_before = display.control_receipt_count('follow_up') if display is not None else 0
                        app.follow_up(session_id, text)
                    except SessionBusyError:
                        worker.join()
                    else:
                        if display is None or display.control_receipt_count('follow_up') == receipt_before:
                            emit('Follow-up 已排队')
                        continue
                start_run(text)
                continue
            if command.startswith(':'):
                emit('未知命令')
                continue

            if args.workspace is not None and app.pending_tool_approvals(session_id):
                emit('当前有待审批工具调用，请先处理审批')
                continue
            if worker is not None and worker.is_alive():
                try:
                    receipt_before = display.control_receipt_count('steering') if display is not None else 0
                    app.steer(session_id, line)
                except (SessionBusyError, SessionPendingApprovalError):
                    worker.join()
                else:
                    if display is None or display.control_receipt_count('steering') == receipt_before:
                        emit('Steering 已排队')
                    continue
            start_run(line)
    finally:
        if worker is not None and worker.is_alive():
            worker.join()
        try:
            if unsubscribe is not None:
                unsubscribe()
        finally:
            if storage is not None:
                storage.__exit__(*sys.exc_info())


if __name__ == '__main__':
    raise SystemExit(main())
