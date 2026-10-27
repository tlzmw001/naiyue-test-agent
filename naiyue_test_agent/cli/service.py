"""Stop an existing shared service or read its local diagnostic log."""

import argparse
import asyncio
import logging
import os
from pathlib import Path
import sys
import time

from naiyue_test_agent.config_paths import get_agent_dir, get_data_dir, get_models_path, load_storage_settings
from naiyue_test_agent.resources import frontend_directory
from naiyue_test_agent.service.client import ServiceClient
from naiyue_test_agent.service.settings import ServiceError, ServiceOptions
from naiyue_test_agent.storage.files import check_store_directory, open_store_readonly

logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nyt service', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    stop = commands.add_parser('stop', help='Normally stop the existing shared service')
    logs = commands.add_parser('logs', help='Show the service diagnostic log')
    for command in (stop, logs):
        command.add_argument('--settings', type=Path, default=get_agent_dir() / 'settings.toml')
    stop.add_argument('--config', type=Path, default=get_models_path())
    logs.add_argument('--follow', '-f', action='store_true', help='Keep showing new log lines')
    args = parser.parse_args(argv)
    try:
        data_dir = get_data_dir(load_storage_settings(args.settings))
        if args.command == 'stop':
            options = ServiceOptions(data_dir=data_dir, config_path=args.config,
                settings_path=args.settings, frontend_dist=frontend_directory())
            async def shutdown():
                client = await ServiceClient.connect(options)
                try:
                    return await client.request('POST', '/api/v1/shutdown')
                finally:
                    await client.aclose()
            result = asyncio.run(shutdown())
            if result != {'accepted': True}:
                raise ServiceError('service_response_invalid')
            print('服务退出请求已接受，正在收尾。')
            return 0
        directory = data_dir / 'service'
        check_store_directory(directory)
        fd = open_store_readonly(directory / 'service.log')
        with os.fdopen(fd, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            start = max(0, end - 65536)
            stream.seek(start)
            content = stream.read(end - start)
            if start and b'\n' in content:
                content = content.split(b'\n', 1)[1]
            sys.stdout.write(b''.join(content.splitlines(keepends=True)[-100:]).decode('utf-8', errors='replace'))
            sys.stdout.flush()
            if args.follow:
                while True:
                    addition = stream.read()
                    if addition:
                        sys.stdout.write(addition.decode('utf-8', errors='replace'))
                        sys.stdout.flush()
                    else:
                        time.sleep(0.25)
        return 0
    except KeyboardInterrupt:
        return 130
    except ServiceError as error:
        logger.error('service_command_failed command=%s code=%s', args.command, error.code)
        print(f'服务操作失败（{error.code}）')
        return 1
    except (OSError, ValueError) as error:
        logger.error('service_command_failed command=%s error_type=%s', args.command, type(error).__name__)
        code = 'service_log_unavailable' if args.command == 'logs' else 'service_settings_invalid'
        print(f'服务操作失败（{code}）')
        return 1
