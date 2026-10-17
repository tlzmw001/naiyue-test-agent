"""Terminal input driver with native async Agent execution for one local Agent session."""

import argparse
import json
import logging
import sys
from pathlib import Path

from naiyue_test_agent.config_paths import get_data_dir, get_models_path
from naiyue_test_agent.workspaces import prepare_workspace
from threading import Thread, Lock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.application import (
    SessionBusyError, SessionManualReviewRequiredError, SessionPendingApprovalError,
)
from naiyue_test_agent.application.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.tools import create_builtin_tools, load_tool_settings
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.tools.output import (
    clear_shell_outputs, preview_shell_outputs,
)
from naiyue_test_agent.tools.read import resolve_workspace_root
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.application.cli_display import CliEventDisplay
from naiyue_test_agent.application.cli_async import build_local_application, build_persistent_application
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError


logger = logging.getLogger(__name__)


def _print_pending_approvals(result, *, write=print) -> None:
    lines = []
    for index, item in enumerate(result.get('approval_requests', []), start=1):
        if item['tool_name'] in ('bash', 'powershell'):
            command = json.dumps(item['target'], ensure_ascii=False)
            workspace = json.dumps(item['workspace'], ensure_ascii=False)
            lines.append(f"待审批 {index}: {item['tool_name']} 工作区={workspace} 命令={command}")
        else:
            lines.append(f"待审批 {index}: {item['tool_name']} → {item['target']}")
    if result.get('approval_requests'):
        lines.append('单项使用 :approve 或 :deny；多项使用 :review approve reject ...')
    if lines:
        write('\n'.join(lines))


def _run_and_print(app, session_id: str, text: str, *, display=None) -> bool:
    write = display.write if display is not None else print
    if display is not None:
        display.begin_call()
    request = HumanMessage(content=text, id=uuid4().hex)
    try:
        result = app.run(session_id, [request])
        if display is not None:
            display.finish_result(result, input_message_id=request.id)
            _print_pending_approvals(result, write=write)
            return True
        messages = result['messages']
        start = next((index for index, message in enumerate(messages)
                      if message.id == request.id), None)
        if start is None:
            raise RuntimeError('Current input is missing from the run result')
        replies = [str(message.text) for message in messages[start + 1:]
                   if isinstance(message, AIMessage) and str(message.text).strip()]
    except SessionManualReviewRequiredError:
        write('本会话工具副作用待核对；请检查执行结果后重启会话')
        return False
    except (SessionBusyError, SessionPendingApprovalError):
        write('当前会话尚不能接收新请求，待发送内容保留')
        return False
    except ProcessCleanupError as error:
        logger.error('cli_run_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write('工具副作用无法确认；请先检查执行结果，勿直接重试')
        return True
    except Exception as error:
        logger.error('cli_run_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write(f'运行失败（{type(error).__name__}）')
        return True

    if replies:
        for reply in replies:
            print(reply)
    elif not result.get('approval_requests'):
        print('本轮没有可展示的回答')
    _print_pending_approvals(result)
    if result.get('run_stopped'):
        print('已停止')

    return True

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
    except ProcessCleanupError as error:
        logger.error('cli_approval_failed error_type=%s', type(error).__name__)
        if display is None or not display.terminal_displayed:
            write('工具副作用无法确认；请先检查执行结果，勿直接重试')
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
        print('已停止')


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
    parser.add_argument('--config', type=Path, default=get_models_path())
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--no-session', action='store_true', help='Use an ephemeral in-memory session')
    parser.add_argument('--session-db', type=Path, help='Override the session database path')
    parser.add_argument('--new-workspace', action='store_true', help='Give a copied workspace a new identity')
    parser.add_argument('--resume', nargs='?', const='', help='Choose a session, or resume the exact ID')
    args = parser.parse_args(argv)
    if args.no_session and (args.resume is not None or args.session_db is not None):
        parser.error('--no-session conflicts with --resume and --session-db')

    if args.new_workspace and (args.no_session or args.session_db is not None or args.resume is not None):
        parser.error('--new-workspace conflicts with --no-session, --session-db and --resume')

    storage = None
    app = None
    restored = None
    history = None
    try:
        tool_settings = load_tool_settings()
        args.workspace = resolve_workspace_root(args.workspace or Path.cwd())
        if not args.no_session:
            location = prepare_workspace(args.workspace, data_dir=get_data_dir(tool_settings.storage),
                database_path=args.session_db, new_workspace=args.new_workspace, create=args.resume is None)
            if args.resume is not None and (location is None or not location.database_path.exists()):
                if args.resume == '':
                    print('暂无历史会话')
                    return 0
                raise SessionPersistenceError('session_not_found')
            storage = SessionStorage(location.database_path)
            storage.bind_workspace(location.workspace_id, location.root)
            if args.resume is not None:
                selected = _pick_session(storage, args.resume)
                if selected is None:
                    storage.close()
                    return 0
                restored = storage.get(selected)
                if str(args.workspace) != restored['workspace_root']:
                    raise SessionPersistenceError('workspace_mismatch')
        tools = create_builtin_tools(tool_settings)
        initial_tools = [spec.tool.name for spec in tools if spec.tool.name not in ('bash', 'powershell')]
        factory = build_local_application if storage is None else build_persistent_application
        options = {} if storage is None else {'storage': storage}
        app = factory(
            args.config, tools=tools, system_prompt=BASE_SYSTEM_PROMPT,
            response_format=None, initial_active_tools=initial_tools, settings=tool_settings, **options,
        )
        if restored is None:
            session_id = app.create_workspace_session(app.default_model_id, args.workspace)
        else:
            session_id = restored['session_id']
            if restored['state'] == 'ready':
                try:
                    app.restore_session(session_id)
                except (SessionPersistenceError, ModelConfigError, ValueError):
                    history = app.get_session_history(session_id)
            else:
                history = app.get_session_history(session_id)
        display = (CliEventDisplay(session_id, progress_settings=tool_settings.progress)
                   if callable(getattr(app, 'subscribe_events', None)) else None)
        unsubscribe = app.subscribe_events(display) if display is not None and history is None else None
        emit = display.write if display is not None else print
    except Exception as error:
        logger.error('cli_initialization_failed error_type=%s', type(error).__name__)
        emit(f'初始化失败（{type(error).__name__}）')
        if str(error) == 'workspace_id_conflict':
            emit('检测到工作区副本；请使用 --new-workspace 为副本创建独立历史')
        elif str(error) == 'storage_in_use':
            emit('该工作区数据库已被其他服务占用，请先关闭该服务')
        if app is not None and callable(getattr(app, 'close', None)):
            try:
                app.close()
            except Exception as closing_error:
                logger.error('cli_initialization_cleanup_failed error_type=%s', type(closing_error).__name__)
        if storage is not None:
            storage.__exit__(type(error), error, error.__traceback__)
        return 1

    worker: Thread | None = None
    try:
        emit(f'当前模型：{restored["model_id"] if restored else app.default_model_id}')
        emit(f'{"持久会话" if storage is not None else "临时会话"}：{session_id}')
        interactive = sys.stdin.isatty()
        if history is not None:
            display.show_history(history)
            kind, actions = history['recovery']['kind'], history['recovery']['actions']
            action = ('restore_approval' if kind == 'pending_approval' and 'restore_approval' in actions else
                      'continue' if kind == 'interrupted_execution' and 'continue' in actions else None)
            if action is None:
                return 0
            try:
                app.recover_session(session_id, checkpoint_id=history['checkpoint_id'], action=action)
            except Exception as error:
                logger.error('cli_session_recovery_failed error_type=%s', type(error).__name__)
                if action != 'restore_approval' or str(error) != 'approval_restore_unavailable' or not interactive:
                    emit('会话恢复未确认；已保留历史，请重新打开会话核对当前状态。')
                    return 1
                emit('原审批条件已变化；输入 continue 放弃旧批次后继续沟通，空行退出。')
                try:
                    if input('恢复操作：').strip() != 'continue':
                        return 0
                    current = app.get_session_history(session_id)
                    app.recover_session(session_id, checkpoint_id=current['checkpoint_id'], action='continue')
                    action = 'continue'
                except EOFError:
                    return 0
                except Exception as fault:
                    logger.error('cli_session_recovery_failed error_type=%s', type(fault).__name__)
                    emit('会话恢复未确认；已保留历史，请重新打开会话核对当前状态。')
                    return 1
            unsubscribe = app.subscribe_events(display)
            if action == 'restore_approval':
                emit('已恢复待审批批次，工具尚未执行。用 :review 为每项提供 approve/reject。')
                for item in app.pending_tool_approvals(session_id):
                    emit(f'{item["tool_name"]}：{item["target"]}')
            else:
                emit('旧运行已结束；下一条新消息才会启动运行。')
        output_clear_plan = None
        pending_input = []
        pending_lock = Lock()
        stopped_input = False
        output_workspace = args.workspace

        def start_run(text: str, pending_count=0) -> None:
            nonlocal worker
            def execute():
                accepted = _run_and_print(app, session_id, text, display=display)
                if pending_count and accepted:
                    with pending_lock:
                        del pending_input[:pending_count]
                elif pending_count:
                    emit('待发送内容仍保留，可用 :pending 查看')
            if interactive:
                worker = Thread(target=execute)
                worker.start()
            else:
                execute()

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
                if app.pending_tool_approvals(session_id):
                    emit('待审批未处理；下次打开会话时将重新核对恢复条件')
                return 0
            if command in (':pending', ':send'):
                if command == ':pending':
                    emit('待发送内容：\n' + '\n\n'.join(pending_input) if pending_input else '没有待发送内容')
                elif worker is not None and worker.is_alive():
                    emit('上一轮尚未收尾，待发送内容已保留')
                else:
                    stopped_input = False
                    if pending_input:
                        text = '\n\n'.join(pending_input)
                        start_run(text, len(pending_input))
                    else:
                        emit('没有待发送内容，可输入新的请求')
                continue
            if command == ':outputs':
                action = rest[0] if rest else ''
                if action not in ('clear', 'clear confirm'):
                    emit('用法：:outputs clear；确认后输入 :outputs clear confirm')
                    output_clear_plan = None
                    continue
                if worker is not None and worker.is_alive():
                    emit('当前运行尚未结束，不能清理 Shell 输出')
                    output_clear_plan = None
                    continue
                try:
                    if action == 'clear':
                        output_clear_plan = preview_shell_outputs(output_workspace)
                        if output_clear_plan.file_count:
                            emit(f'将清理 {output_clear_plan.file_count} 个文件，'
                                  f'{output_clear_plan.total_bytes} 字节；'
                                  '确认请输入 :outputs clear confirm')
                        else:
                            emit('没有可清理的 Shell 输出')
                            output_clear_plan = None
                    elif output_clear_plan is None:
                        emit('请先输入 :outputs clear 查看待清理文件')
                    else:
                        count, size = clear_shell_outputs(output_clear_plan)
                        output_clear_plan = None
                        emit(f'已清理 {count} 个文件，{size} 字节')
                except (OSError, ValueError, UnicodeError) as error:
                    output_clear_plan = None
                    logger.warning('bash_output_clear_failed error_type=%s', type(error).__name__)
                    emit('Shell 输出目录异常或预览后已变化，未完成清理')
                continue
            if command in (':approve', ':deny', ':review'):
                if worker is not None and worker.is_alive():
                    worker.join()
                pending = app.pending_tool_approvals(session_id)
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
                    retained = app.request_stop(session_id)
                    pending_input.extend(retained or [])
                    stopped_input = True
                    emit('停止后的输入将保留待发送；用 :pending 查看，:send 显式提交')
                    if pending_input:
                        emit('待发送内容：\n' + '\n\n'.join(pending_input))
                except SessionBusyError:
                    emit('停止请求未被接受（运行尚未开窗或已进入收尾）')
                else:
                    if display is None or display.control_receipt_count('stop') == receipt_before:
                        emit('已请求停止，正在等待收尾')
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
                if stopped_input and rest:
                    pending_input.append(rest[0].strip())
                    emit('待发送内容已保留：' + rest[0].strip())
                    continue
                if not rest or not rest[0].strip():
                    emit('请输入 Follow-up 内容')
                    continue
                text = rest[0].strip()
                if app.pending_tool_approvals(session_id):
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

            if stopped_input:
                pending_input.append(line)
                emit('待发送内容已保留：' + line)
                continue
            if app.pending_tool_approvals(session_id):
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
            try:
                if callable(getattr(app, 'close', None)):
                    app.close()
            finally:
                if storage is not None:
                    storage.__exit__(*sys.exc_info())


if __name__ == '__main__':
    raise SystemExit(main())
