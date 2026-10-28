"""Connect the local browser to the shared service without owning its lifecycle."""
import argparse
import asyncio
import logging
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlencode

from naiyue_test_agent.config_paths import get_agent_dir, get_data_dir, get_models_path
from naiyue_test_agent.settings import load_settings
from naiyue_test_agent.resources import frontend_directory, PackageResourceError
from naiyue_test_agent.service.launcher import ensure_service
from naiyue_test_agent.service.settings import ServiceError, ServiceOptions
from naiyue_test_agent.cli.startup_display import startup_messages

logger = logging.getLogger(__name__)


async def prepare_page(args, options):
    client = await ensure_service(options)
    primary = None
    try:
        root = args.workspace if args.workspace is not None else None if args.project or args.session_db else Path.cwd()
        body = {'root': str(root.expanduser().resolve()) if root is not None else None,
            'project_id': args.project,
            'database_path': str(args.session_db.expanduser().absolute()) if args.session_db else None,
            'new_workspace': args.new_workspace, 'resume': args.session_db is not None,
            'trusted': args.trust_project}
        try:
            prepared = await client.request('POST', '/api/v1/terminal/prepare', json=body)
        except ServiceError as error:
            if error.code != 'project_identity_conflict' or args.session_db is None:
                raise
            query = {'database_path': body['database_path']}
            if root is not None:
                query['root'] = body['root']
            await client.request('GET', '/api/v1/terminal/legacy?' + urlencode(query))
            return client._record.origin + '/#' + urlencode({
                'access_token': client._record.access_token, 'scope_id': 'legacy',
                'instance_id': client.instance_id, 'legacy_database': body['database_path']})
        if prepared['status'] == 'trust_required':
            if root is not None and not root.is_dir():
                raise ServiceError('history_database_required')
            print('项目目录：' + json.dumps(prepared['root'], ensure_ascii=False))
            if not sys.stdin.isatty():
                raise ServiceError('project_trust_required')
            try:
                answer = input('允许此项目中的 Agent 执行工具？输入 trust 确认，空行取消：').strip()
            except EOFError:
                answer = ''
            if answer != 'trust':
                return None
            prepared = await client.request('POST', '/api/v1/terminal/prepare', json={**body, 'trusted': True})
        if prepared['status'] == 'empty':
            raise ServiceError('history_database_missing')
        if prepared['status'] != 'ready':
            raise ServiceError('service_response_invalid')
        record = client._record
        fragment = urlencode({'access_token': record.access_token, 'scope_id': prepared['scope_id'],
                              'instance_id': client.instance_id})
        return record.origin + '/#' + fragment
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await client.aclose()
        except BaseException as error:
            logger.error('web_client_close_failed error_type=%s', type(error).__name__)
            if primary is None:
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=get_models_path())
    parser.add_argument('--settings', type=Path, default=get_agent_dir() / 'settings.toml')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--project')
    parser.add_argument('--trust-project', action='store_true')
    parser.add_argument('--session-db', type=Path)
    parser.add_argument('--new-workspace', action='store_true')
    parser.add_argument('--host', default=os.environ.get('NAIYUE_WEB_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('NAIYUE_WEB_PORT', '0')))
    parser.add_argument('--frontend-dist', type=Path)
    args = parser.parse_args(argv)
    if args.new_workspace and (args.session_db is not None or args.project):
        parser.error('--new-workspace conflicts with --session-db/--project')
    from naiyue_test_agent.diagnostics import SafeDiagnosticFormatter
    handler = logging.StreamHandler()
    handler.setFormatter(SafeDiagnosticFormatter())
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    try:
        frontend = args.frontend_dist if args.frontend_dist is not None else frontend_directory()
        if not (frontend / 'index.html').is_file():
            print('前端尚未构建，请先执行 npm --prefix frontend run build')
            return 1
        settings = load_settings(args.settings)
        options = ServiceOptions(config_path=args.config, settings_path=args.settings,
            data_dir=get_data_dir(settings.storage), frontend_dist=frontend, host=args.host, port=args.port)
        link = asyncio.run(prepare_page(args, options))
        if link is not None:
            print('本地访问链接：' + link, flush=True)
            print('请在浏览器打开链接。启动终端可关闭；共享服务和持久任务继续运行。', flush=True)
        return 0
    except KeyboardInterrupt:
        print('收到退出信号，启动器已断开，共享服务继续运行。')
        return 130
    except Exception as error:
        logger.error('web_startup_failed error_type=%s', type(error).__name__)
        print(f'启动失败（{type(error).__name__}）')
        if isinstance(error, PackageResourceError):
            print(str(error))
        elif isinstance(error, ServiceError):
            print(error.code)
            if error.code == 'project_trust_required':
                print('请交互式确认项目，或显式使用 --trust-project。')
            for message in startup_messages(error, args.config):
                print(message)
        else:
            for message in startup_messages(error, args.config):
                print(message)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
