"""Offline storage maintenance; no Agent, model catalog, or live DB required."""

import argparse
import json
import logging
from pathlib import Path

from naiyue_test_agent import config_paths
from naiyue_test_agent.storage.backup import list_backups
from naiyue_test_agent.storage.restore import REASONS, restore_backup
from naiyue_test_agent.workspaces import locate_workspace


def _confirm(preview):
    print('还原目标：' + json.dumps(preview['database_path'], ensure_ascii=True))
    print('备份ID：' + preview['backup_id'])
    print('源版本：' + str(preview['source_schema']))
    for field, label in (('created_at', '备份时间'), ('reason', '原因'), ('creator_version', '创建程序版本'),
                         ('target_schema', '备份时迁移目标版本'), ('operation_id', '继续操作')):
        if preview.get(field) is not None:
            print(label + '：' + json.dumps(preview[field], ensure_ascii=True))
    print('将舍弃该备份之后至本次还原前的数据库变更，保留原文件组；不会撤销库外文件或网络操作。')
    return input('输入完整备份ID确认（其他输入取消）：')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nyt storage', description='Offline storage maintenance')
    commands = parser.add_subparsers(dest='command', required=True)
    backups = commands.add_parser('backups', help='List validated, invalid and incomplete backups')
    restore = commands.add_parser('restore', help='Restore an offline workspace database after full-ID confirmation')
    restore.add_argument('backup_id')
    for command in (backups, restore):
        command.add_argument('--workspace', type=Path, default=Path.cwd())
        command.add_argument('--session-db', type=Path)
    args = parser.parse_args(argv)
    stage = 'settings'
    try:
        data_dir = (config_paths.get_data_dir(config_paths.load_storage_settings())
                    if args.session_db is None else None)
        stage = 'locate'
        location = locate_workspace(args.workspace, data_dir=data_dir, database_path=args.session_db)
        if args.command == 'restore':
            if location is None:
                raise ValueError('workspace_unavailable')
            stage = 'restore'
            result = restore_backup(location, args.backup_id, confirm=_confirm)
            print('\n' + json.dumps(result, ensure_ascii=True))
            return 0
        result = {'workspace_id': None, 'database_path': None, 'backups': []}
        if location is not None:
            stage = 'list'
            result = {'workspace_id': location.workspace_id, 'database_path': str(location.database_path),
                'backups': list_backups(location.database_path, workspace_id=location.workspace_id)}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning('storage_maintenance_cancelled stage=%s', stage)
        return 130
    except (OSError, ValueError) as error:
        reason = {'settings': 'storage_settings_invalid', 'locate': 'workspace_unavailable',
                  'list': 'backup_invalid', 'restore': 'restore_failed'}[stage]
        if stage == 'restore' and isinstance(error, ValueError) and str(error) in REASONS:
            reason = str(error)
        logging.getLogger(__name__).error('storage_maintenance_failed stage=%s error_type=%s', stage, type(error).__name__)
        print('\n' + json.dumps({'error': reason}))
        return 1
