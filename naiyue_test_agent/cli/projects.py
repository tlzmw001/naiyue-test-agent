"""List registered projects through the shared service without opening a runtime."""
import argparse
import json
import logging
from pathlib import Path

from naiyue_test_agent.config_paths import get_models_path, get_agent_dir, get_data_dir
from naiyue_test_agent.resources import frontend_directory
from naiyue_test_agent.service.settings import ServiceOptions, ServiceError
from naiyue_test_agent.settings import load_settings
from .terminal_client import TerminalClient

logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=get_models_path())
    parser.add_argument('--settings', type=Path)
    args = parser.parse_args(argv)
    client = None
    try:
        settings = load_settings(args.settings) if args.settings else load_settings()
        client = TerminalClient(ServiceOptions(config_path=args.config,
            settings_path=args.settings or get_agent_dir() / 'settings.toml',
            data_dir=get_data_dir(settings.storage), frontend_dist=frontend_directory()))
        rows = client.request('GET', '/api/v1/projects')['items']
        if not rows:
            print('暂无项目')
        for row in rows:
            source = row['source']
            location = source.get('path') or source.get('url') or ''
            print(f'{row["project_id"]} {json.dumps(row["name"], ensure_ascii=False)} '
                  f'{json.dumps(location, ensure_ascii=False)} {row["availability"]}')
        return 0
    except Exception as error:
        logger.error('terminal_projects_failed error_type=%s', type(error).__name__)
        print(f'项目查询失败（{error.code if isinstance(error, ServiceError) else type(error).__name__}）')
        return 1
    finally:
        if client is not None:
            client.close()
