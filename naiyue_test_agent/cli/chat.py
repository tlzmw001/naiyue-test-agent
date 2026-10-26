"""Terminal input driver for one session owned by the shared service."""

import argparse
import json
import logging
import sys
from pathlib import Path

from naiyue_test_agent.config_paths import get_models_path
from threading import Thread, Lock


from naiyue_test_agent.settings import load_settings
from .cli_display import CliEventDisplay
from naiyue_test_agent.service.settings import ServiceError
from .cli_approval import handle_approval, handle_scope_stop, show_approval_batches, settle_cli_worker, report_command_failure
from .chat_startup import prepare_terminal
from .startup_display import startup_messages


logger = logging.getLogger(__name__)


def _run_and_print(app, session_id, text, *, display=None):
    write = display.write if display else print
    try:
        result = app.run(session_id, text)
        if display:
            display.finish_view(result)
        else:
            for row in result['reply_messages']:
                if row['content'].strip(): write(row['content'])
        if display:
            display.show_approvals(app.pending_approvals(session_id))
        else:
            show_approval_batches(app.pending_approvals(session_id), write)
        return True
    except ServiceError as error:
        if app.disconnecting:
            return True
        if error.code in ('session_busy', 'approval_required'):
            write('当前会话尚不能接收新请求，待发送内容保留')
            return False
        if error.code in ('manual_review_required', 'session_recovery_blocked',
                          'run_capacity_exceeded', 'execution_root_unavailable'):
            write('本会话尚不能执行；工具副作用待核对，待发送内容仍保留')
            return False
        logger.error('terminal_run_failed code=%s', error.code)
        write('工具副作用待核对，请检查执行结果。' if error.code == 'manual_review_required' else
              f'运行结果未确认（{error.code}），请查询会话状态，勿直接重发。')
        return True


def _resume_and_print(app, session_id, receipt, *, display=None):
    try:
        result = app.wait(session_id, receipt['run_id'], before=receipt.get('terminal_before', ()))
        if display:
            display.finish_view(result)
            display.show_approvals(app.pending_approvals(session_id))
        else:
            for row in result['reply_messages']:
                if row['content'].strip(): print(row['content'])
            show_approval_batches(app.pending_approvals(session_id), print)
    except ServiceError as error:
        if app.disconnecting:
            return
        logger.error('terminal_approval_failed code=%s', error.code)
        (display.write if display else print)(f'审批恢复结果未确认（{error.code}），请查询状态。')


def main(argv: list[str] | None = None) -> int:
    emit = print
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=get_models_path())
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--project', help='Registered project UUID')
    parser.add_argument('--trust-project', action='store_true', help='Explicitly trust the selected project directory')
    parser.add_argument('--no-session', action='store_true', help='Use an ephemeral in-memory session')
    parser.add_argument('--session-db', type=Path, help='Override the session database path')
    parser.add_argument('--new-workspace', action='store_true', help='Give a copied workspace a new identity')
    parser.add_argument('--resume', nargs='?', const='', help='Choose a session, or resume the exact ID')
    parser.add_argument('--execution',choices=('host','sandbox'),help='Execution location for a new persistent session')
    parser.add_argument('--include-untracked',action='append',default=[],help='Explicit relative file to include in sandbox input; repeatable')
    args = parser.parse_args(argv)
    if args.include_untracked and args.execution!='sandbox':parser.error('--include-untracked requires --execution sandbox')
    if args.execution is not None and args.resume is not None:parser.error('--execution cannot replace a restored session binding')
    if args.no_session and args.execution=='sandbox':parser.error('--no-session cannot use sandbox')
    if args.project and (args.no_session or args.session_db is not None or args.new_workspace):
        parser.error('--project conflicts with --no-session, --session-db and --new-workspace')
    if args.no_session and (args.resume is not None or args.session_db is not None):
        parser.error('--no-session conflicts with --resume and --session-db')

    if args.new_workspace and (args.no_session or args.session_db is not None or args.resume is not None):
        parser.error('--new-workspace conflicts with --no-session, --session-db and --resume')

    app = None
    restored = None
    history = None
    try:
        tool_settings = load_settings(args.settings) if args.settings else load_settings()
        app, session_id, restored = prepare_terminal(args, tool_settings)
        if app is None:
            return 0
        history = None
        if restored is not None:
            status = app.request('GET', f'/sessions/{session_id}')
            if status.get('execution_root_error'):
                history = app.history(session_id)
            elif 'recovery' in status:
                if restored['state'] != 'ready':
                    history = app.history(session_id)
                else:
                    try:
                        app.request('POST', f'/sessions/{session_id}/prepare')
                    except ServiceError:
                        history = app.history(session_id)
        display = CliEventDisplay(session_id, progress_settings=tool_settings.progress)
        if history is None: app.watch(session_id, display)
        emit = display.write if display is not None else print
    except Exception as error:
        logger.error('cli_initialization_failed error_type=%s', type(error).__name__)
        emit(f'初始化失败（{error.code if isinstance(error, ServiceError) else type(error).__name__}）')
        for message in startup_messages(error, args.config):
            emit(message)
        if app is not None and callable(getattr(app, 'close', None)):
            try:
                app.close()
            except Exception as closing_error:
                logger.error('cli_initialization_cleanup_failed error_type=%s', type(closing_error).__name__)
        return 1

    worker: Thread | None = None
    try:
        emit(f'当前模型：{restored["model_id"] if restored else app.request("GET", "/models")["default_model_id"]}')
        emit(f'{"临时会话" if args.no_session else "持久会话"}：{session_id}\n查询：:usage；:diagnostics [run_id]；:tasks；后台管理：:task output/stop ID')
        interactive = sys.stdin.isatty()
        if history is not None:
            display.show_history_view(history)
            kind, actions = history['recovery']['kind'], history['recovery']['actions']
            action = ('restore_approval' if kind == 'pending_approval' and 'restore_approval' in actions else
                      'continue' if kind == 'interrupted_execution' and 'continue' in actions else None)
            if action is None:
                return 0
            try:
                app.request('POST', f'/sessions/{session_id}/prepare')
                app.request('POST', f'/sessions/{session_id}/recover', json={'checkpoint_id': history['checkpoint_id'], 'action': action})
            except Exception as error:
                logger.error('cli_session_recovery_failed error_type=%s code=%s', type(error).__name__,
                             error.code if isinstance(error, ServiceError) else 'unknown')
                if action != 'restore_approval' or str(error) != 'approval_restore_unavailable' or not interactive:
                    emit('会话恢复未确认；已保留历史，请重新打开会话核对当前状态。')
                    return 1
                emit('原审批条件已变化；输入 continue 放弃旧批次后继续沟通，空行退出。')
                try:
                    if input('恢复操作：').strip() != 'continue':
                        return 0
                    current = app.history(session_id)
                    app.request('POST', f'/sessions/{session_id}/recover', json={'checkpoint_id': current['checkpoint_id'], 'action': 'continue'})
                    action = 'continue'
                except EOFError:
                    return 0
                except Exception as fault:
                    logger.error('cli_session_recovery_failed error_type=%s', type(fault).__name__)
                    emit('会话恢复未确认；已保留历史，请重新打开会话核对当前状态。')
                    return 1
            app.watch(session_id, display)
            if action == 'restore_approval':
                emit('已恢复待审批批次，工具尚未执行。用 :review 为每项提供 approve/reject。')
                show_approval_batches(app.pending_approvals(session_id), emit)
            else:
                emit('旧运行已结束；下一条新消息才会启动运行。')
        output_clear_plan = None
        pending_input = []
        pending_lock = Lock()
        stopped_input = False
        piped_input = []
        submitted_root = None

        def start_run(text: str, pending_count=0) -> None:
            nonlocal worker
            def execute():
                accepted = _run_and_print(app, session_id, text, display=display)
                if pending_count and accepted:
                    with pending_lock:
                        del pending_input[:pending_count]
                elif pending_count:
                    emit('待发送内容仍保留，可用 :pending 查看')
                elif not accepted:
                    with pending_lock:
                        pending_input.append(text)
                    emit('待发送内容仍保留，可用 :pending 查看')
            worker = Thread(target=execute)
            worker.start()

        def start_approval(receipt) -> None:
            nonlocal worker
            worker = Thread(target=_resume_and_print, args=(app, session_id, receipt), kwargs={'display': display})
            worker.start()

        def start_approval_stop(batch):
            nonlocal worker
            worker = Thread(target=_resume_and_print, args=(app, session_id, app.approve(session_id, {k: batch[k] for k in ('approval_batch_id', 'origin_run_id')}, stop=True)), kwargs={'display': display})
            worker.start()

        def drain_pipe(*, allow_approval=False):
            while piped_input:
                if allow_approval and app.pending_approvals(session_id):
                    return
                if not settle_cli_worker(app, session_id, worker, emit, stop_waits=not allow_approval):
                    return
                if allow_approval and app.pending_approvals(session_id):
                    return
                kind, text = piped_input.pop(0)
                if kind == 'model': app.request('PATCH', f'/sessions/{session_id}/model', json={'model_id': text})
                else: start_run(text)

        while True:
            try:
                line = input('> ').strip()
            except EOFError:
                if not interactive:
                    drain_pipe()
                    settle_cli_worker(app, session_id, worker, emit)
                return 0

            if not line:
                continue
            command, *rest = line.split(maxsplit=1)
            if command in (':tasks', ':task'):
                from .cli_background import handle_background_command
                handle_background_command(app, line, emit)
                continue
            if command in (':usage', ':diagnostics'):
                from .cli_observation import handle_observation_command
                handle_observation_command(app, session_id, line, emit)
                continue
            if command in (':mcp-refresh', ':tools'):
                from .cli_mcp import handle_mcp_command
                handle_mcp_command(app, session_id, line, emit)
                continue
            if command != ':outputs':
                output_clear_plan = None
            if command == ':quit':
                if not interactive:
                    drain_pipe()
                    settle_cli_worker(app, session_id, worker, emit)
                if app.pending_approvals(session_id):
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
                        output_clear_plan = app.request('POST', '/terminal/outputs/preview')
                        if output_clear_plan['file_count']:
                            emit(f'将清理 {output_clear_plan['file_count']} 个文件，'
                                  f'{output_clear_plan['total_bytes']} 字节；'
                                  '确认请输入 :outputs clear confirm')
                        else:
                            emit('没有可清理的 Shell 输出')
                            output_clear_plan = None
                    elif output_clear_plan is None:
                        emit('请先输入 :outputs clear 查看待清理文件')
                    else:
                        cleared = app.request('POST', '/terminal/outputs/clear', json={'confirmation_id': output_clear_plan['confirmation_id']})
                        count, size = cleared['file_count'], cleared['total_bytes']
                        output_clear_plan = None
                        emit(f'已清理 {count} 个文件，{size} 字节')
                except (OSError, ValueError, UnicodeError, ServiceError) as error:
                    output_clear_plan = None
                    logger.warning('bash_output_clear_failed error_type=%s', type(error).__name__)
                    emit('Shell 输出目录异常或预览后已变化，未完成清理')
                continue
            if command in (':approve', ':deny', ':review', ':approvals'):
                while (submitted_root is not None and worker is not None and worker.is_alive() and
                       any(batch['approval_batch_id'] == submitted_root for batch in app.pending_approvals(session_id))):
                    worker.join(0.05)
                if not interactive and command != ':approvals':
                    drain_pipe(allow_approval=True)
                submitted_root = handle_approval(app, session_id, line, worker=worker, interactive=interactive,
                    write=emit, start_root=start_approval)
                continue
            if command == ':stop':
                target = rest[0].strip() if rest else 'root'
                if handle_scope_stop(app, session_id, target, worker=worker, write=emit, start_root=start_approval_stop):
                    if target == 'root': stopped_input = True
                    continue
                if app.active_run(session_id) is None:
                    emit('当前没有运行')
                    continue
                try:
                    receipt_before = display.control_receipt_count('stop') if display is not None else 0
                    retained = app.control(session_id, 'stop')['retained']
                    pending_input.extend(retained or [])
                    stopped_input = True
                    emit('停止后的输入将保留待发送；用 :pending 查看，:send 显式提交')
                    if pending_input:
                        emit('待发送内容：\n' + '\n\n'.join(pending_input))
                except ServiceError as error:
                    report_command_failure(error, '停止', emit)
                else:
                    if display is None or display.control_receipt_count('stop') == receipt_before:
                        emit('已请求停止，正在等待收尾')
                continue
            if command == ':model':
                if not rest:
                    emit('请输入模型 ID')
                    continue
                model_id = rest[0].strip()
                if not interactive and ((worker is not None and worker.is_alive()) or piped_input):
                    piped_input.append(('model', model_id))
                    continue
                try:
                    app.request('PATCH', f'/sessions/{session_id}/model', json={'model_id': model_id})
                except ServiceError as error:
                    logger.error('cli_model_selection_save_failed error_type=%s', type(error).__name__)
                    emit('模型 ID 不可用' if isinstance(error, ServiceError) and error.code == 'model_unavailable'
                         else '模型选择未保存，继续使用原选择')
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
                if app.pending_approvals(session_id):
                    emit('当前有待审批工具调用，请先处理审批')
                    continue
                if app.active_run(session_id) is not None:
                    try:
                        receipt_before = display.control_receipt_count('follow_up') if display is not None else 0
                        app.control(session_id, 'follow_up', text=text)
                    except ServiceError as error:
                        if error.code not in ('stale_run', 'session_busy'):
                            pending_input.append(text)
                            emit(f'控制请求结果未确认（{error.code}）；内容保留，请核对状态后再决定是否发送。')
                            continue
                        if worker is not None: worker.join()
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
            if not interactive and ((worker is not None and worker.is_alive()) or piped_input):
                piped_input.append(('run', line))
                continue
            if app.pending_approvals(session_id):
                emit('当前有待审批工具调用，请先处理审批')
                continue
            if app.active_run(session_id) is not None:
                try:
                    receipt_before = display.control_receipt_count('steering') if display is not None else 0
                    app.control(session_id, 'steering', text=line)
                except ServiceError as error:
                    if error.code not in ('stale_run', 'session_busy'):
                        pending_input.append(line)
                        emit(f'控制请求结果未确认（{error.code}）；内容保留，请核对状态后再决定是否发送。')
                        continue
                    if worker is not None: worker.join()
                else:
                    if display is None or display.control_receipt_count('steering') == receipt_before:
                        emit('Steering 已排队')
                    continue
            start_run(line)
    except ServiceError as error:
        emit(f'连接或请求结果未确认（{error.code}）；请重新连接当前会话核对，不会自动重发。')
        return 1
    finally:
        primary, close_failed = sys.exception(), False
        try:
            app.close()
        except Exception as error:
            close_failed = True
            logger.error('cli_close_failed error_type=%s code=%s', type(error).__name__,
                         error.code if isinstance(error, ServiceError) else 'unknown')
            try:
                emit('退出清理结果未确认，请核对服务中的会话和任务状态。')
            except Exception as display_error:
                logger.error('cli_close_display_failed error_type=%s', type(display_error).__name__)
        finally:
            if worker is not None:
                worker.join()
        if close_failed and primary is None:
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
