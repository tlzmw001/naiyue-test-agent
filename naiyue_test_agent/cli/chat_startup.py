"""Select CLI session facts before handing storage ownership to the async bridge."""
import json
import logging
import sys
from pathlib import Path

from naiyue_test_agent.config_paths import get_data_dir
from naiyue_test_agent.workspaces import prepare_workspace, locate_stored_project
from naiyue_test_agent.tools.read import resolve_workspace_root
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.errors import SessionPersistenceError


logger = logging.getLogger(__name__)


def _pick_session(storage, selected_id):
    if selected_id:
        return selected_id
    rows = storage.sessions.list_sessions()
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


def prepare_session(args, settings):
    storage = None
    try:
        try:
            expected = (locate_stored_project(args.session_db, root=args.workspace)
                        if args.resume is not None and args.session_db is not None else None)
        except ValueError as error:
            if args.resume != '' or str(error) != 'history_database_missing':
                raise
            print('暂无历史会话')
            return None, None, None
        if expected is not None:
            args.workspace = expected.canonical_root
            storage = ProjectStore(expected.database_path, require_existing=True)
            binding = storage.sessions.get_workspace_binding()
            if binding != {'workspace_id': expected.project_id, 'root_path': str(expected.canonical_root)}:
                raise SessionPersistenceError('workspace_database_mismatch')
        else:
            try:
                args.workspace = resolve_workspace_root(args.workspace or Path.cwd())
            except ValueError as error:
                if args.resume is not None:
                    raise ValueError('history_database_required') from error
                raise
            if args.no_session:
                return None, None, None
            location = prepare_workspace(args.workspace, data_dir=get_data_dir(settings.storage),
                database_path=args.session_db, new_workspace=args.new_workspace, create=args.resume is None)
            if args.resume is not None and (location is None or not location.database_path.exists()):
                if args.resume == '':
                    print('暂无历史会话')
                    return None, None, None
                raise SessionPersistenceError('session_not_found')
            storage = ProjectStore(location.database_path)
            storage.sessions.bind_workspace(location.workspace_id, location.root)
        restored = None
        if args.resume is not None:
            selected = _pick_session(storage, args.resume)
            if selected is not None:
                restored = storage.sessions.get(selected)
                if expected is not None:
                    if restored['project_id'] != expected.project_id:
                        raise SessionPersistenceError('session_not_found')
                elif str(args.workspace) != restored['workspace_root']:
                    raise SessionPersistenceError('workspace_mismatch')
        return storage, restored, expected
    except BaseException:
        if storage is not None:
            try:
                storage.close()
            except BaseException as error:
                logger.error('cli_preflight_cleanup_failed error_type=%s', type(error).__name__)
        raise
