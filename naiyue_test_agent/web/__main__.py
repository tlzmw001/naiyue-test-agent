"""Start a single local Web service using the existing model configuration."""

import argparse
import logging
import os
from pathlib import Path
import socket

import uvicorn

from .app import create_web_app
from .settings import WebSettings


logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--host-bash', action='store_true', help='Enable approved host commands inside the workspace')
    parser.add_argument('--session-db', type=Path,
                        default=Path.home() / '.naiyue-test-agent' / 'sessions.sqlite')
    parser.add_argument('--host', default=os.environ.get('NAIYUE_WEB_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('NAIYUE_WEB_PORT', '0')))
    parser.add_argument('--frontend-dist', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'frontend' / 'dist')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s %(message)s')
    try:
        options = {'access_token': os.environ['NAIYUE_WEB_TOKEN']} if 'NAIYUE_WEB_TOKEN' in os.environ else {}
        settings = WebSettings(config_path=args.config, workspace_root=args.workspace,
            database_path=args.session_db, frontend_dist=args.frontend_dist, host=args.host, port=args.port,
            host_bash=args.host_bash, **options)
        if not (settings.frontend_dist / 'index.html').is_file():
            print('前端尚未构建，请先执行 npm --prefix frontend run build')
            return 1
        family = socket.AF_INET6 if ':' in settings.host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((settings.host, settings.port))
            listener.listen(128)
            settings = settings.model_copy(update={'port': listener.getsockname()[1]})
            app = create_web_app(settings)
            # One-time local bootstrap credential is not emitted through diagnostic logging.
            print(f'本地访问链接：{settings.origin}/#access_token={settings.access_token}', flush=True)
            print('默认持久会话；工作区启用文件工具，Bash 需显式开启并审批。Ctrl-C 等待安全收尾。', flush=True)
            server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port,
                workers=1, log_level='warning', access_log=False))
            server.run(sockets=[listener])
            return 0 if server.started else 1
    except KeyboardInterrupt:
        print('收到退出信号，本地 Web 服务已退出。')
        return 130
    except Exception as error:
        logger.error('web_startup_failed error_type=%s', type(error).__name__)
        print(f'启动失败（{type(error).__name__}）')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
