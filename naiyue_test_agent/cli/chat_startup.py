"""Terminal selection uses authenticated service facts before loading models."""
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlencode
from naiyue_test_agent.config_paths import get_data_dir, get_agent_dir
from naiyue_test_agent.resources import frontend_directory
from naiyue_test_agent.service.settings import ServiceOptions, ServiceError
from .terminal_client import TerminalClient

logger = logging.getLogger(__name__)


def pick_session(rows, selected_id):
    if selected_id:
        if not any(row['session_id'] == selected_id for row in rows):
            raise ServiceError('session_not_found')
        return selected_id
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


def prepare_terminal(args, settings):
    options = ServiceOptions(config_path=args.config,
        settings_path=args.settings or get_agent_dir() / 'settings.toml',
        data_dir=get_data_dir(settings.storage), frontend_dist=frontend_directory())
    client = TerminalClient(options)
    try:
        if args.no_session:
            args.workspace = (args.workspace or Path.cwd()).expanduser().resolve()
            client.lease(args.workspace)
            models = client.request('GET', '/models')
            sid = client.request('POST', '/sessions', json={'model_id': models['default_model_id']})['session_id']
            return client, sid, None
        root = args.workspace if args.workspace is not None else None if args.project or args.session_db and args.resume is not None else Path.cwd()
        body = dict(root=str(root) if root is not None else None, project_id=args.project,
            database_path=str(args.session_db) if args.session_db else None,
            resume=args.resume is not None, new_workspace=args.new_workspace, trusted=args.trust_project)
        try:
            prepared = client.request('POST', '/api/v1/terminal/prepare', json=body)
        except ServiceError as error:
            if error.code != 'project_identity_conflict' or args.resume is None or args.session_db is None:
                raise
            query = {'database_path': str(args.session_db)}
            if args.workspace is not None:
                query['root'] = str(args.workspace)
            legacy = client.request('GET', '/api/v1/terminal/legacy?' + urlencode(query))
            selected = pick_session(legacy['sessions'], args.resume)
            if selected is not None:
                from .cli_display import CliEventDisplay
                CliEventDisplay(selected).show_history_view(client.legacy_history(args.session_db, selected))
            client.close()
            return None, None, None
        if prepared['status'] == 'trust_required':
            print('项目目录：' + json.dumps(prepared['root'], ensure_ascii=False))
            if not sys.stdin.isatty():
                raise ServiceError('project_trust_required')
            if input('允许此项目中的 Agent 执行工具？输入 trust 确认，空行取消：').strip() != 'trust':
                client.close()
                return None, None, None
            body['trusted'] = True
            prepared = client.request('POST', '/api/v1/terminal/prepare', json=body)
        if prepared['status'] == 'empty':
            if args.resume:
                raise ServiceError('history_database_missing' if args.session_db else 'history_database_required')
            print('暂无历史会话')
            client.close()
            return None, None, None
        client.bind(prepared['scope_id'])
        args.workspace = Path(prepared['root']) if prepared['root'] is not None else None
        if args.resume is not None:
            sid = pick_session(prepared['sessions'], args.resume)
            if sid is None:
                client.close()
                return None, None, None
            return client, sid, next(row for row in prepared['sessions'] if row['session_id'] == sid)
        models = client.request('GET', '/models')
        body={'model_id':models['default_model_id']}
        if getattr(args,'execution',None)=='sandbox':
            body.update(execution='sandbox',include_untracked=args.include_untracked)
        elif prepared.get('source_kind')=='git':
            raise ServiceError('project_execution_unavailable')
        sid=client.request('POST','/sessions',json=body)['session_id']
        status=client.request('GET',f'/sessions/{sid}')
        execution=status.get('execution',{})
        print('执行位置：'+('Docker 沙箱' if execution.get('kind')=='sandbox' else '此计算机'))
        if execution.get('state')!='ready':
            print('执行位置不可用；会话历史保留。原因='+str((execution.get('error') or {}).get('code','unavailable')))
        return client,sid,None
    except BaseException:
        try:
            client.close()
        except BaseException as error:
            logger.error('terminal_startup_cleanup_secondary_failed error_type=%s', type(error).__name__)
        raise
