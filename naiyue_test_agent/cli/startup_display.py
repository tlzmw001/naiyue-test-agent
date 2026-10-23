"""Safe terminal startup details shared by Web and chat."""

import json

from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.application.errors import StorageStartupError


def startup_messages(error, config_path):
    if isinstance(error, ModelConfigError) and not config_path.expanduser().is_file():
        return [f'未找到模型配置：{config_path}；请创建该文件或使用 --config 指定配置。配置格式见 README。']
    code = str(error)
    if code == 'workspace_id_conflict':
        return ['检测到工作区副本；请使用 --new-workspace 为副本创建独立历史']
    if code == 'storage_in_use':
        return ['该工作区数据库已被其他服务占用，请先关闭该服务']
    reasons = {
        'unsupported_store_schema': '当前程序不支持该会话库版本或版本信息无法确认。',
        'backup_failed': '迁移前备份未完成，已停止迁移并保留当前现场。',
        'migration_failed': '会话库迁移失败，已停止后续步骤并保留原始备份与当前现场。',
        'restore_incomplete': '检测到未完成或无法确认的还原维护现场，已阻止读取或创建会话库。',
        'storage_unavailable': '会话库无法安全打开，请保留现场并检查存储。',
    }
    if code not in reasons:
        return []
    lines = [f'存储启动失败：{code}。{reasons[code]}']
    if isinstance(error, StorageStartupError):
        lines.append(f'数据库：{json.dumps(str(error.database_path), ensure_ascii=True)}')
        lines.append(f'阶段：{error.phase}')
        fields = [('源版本', error.source_schema), ('当前版本', error.current_schema), ('目标版本', error.target_schema)]
        lines.append('；'.join(f'{name}={value}' for name, value in fields if value is not None))
        if error.backup_id is not None:
            lines.append(f'迁移前备份：{error.backup_id}')
    lines.append('可用 nyt storage backups 指定同一 --workspace 查看备份；自定义数据库补充 --session-db。请保留现场，不要删除数据库或维护标记。')
    if code == 'restore_incomplete':
        lines.append('核对现场后，可用 nyt storage restore BACKUP_ID 指定同一 --workspace（及 --session-db），输入完整备份ID继续收尾；不要绕过维护标记启动。')
    return lines
