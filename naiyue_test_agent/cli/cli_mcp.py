"""MCP refresh and tool choices on the already running terminal application."""

import json
import logging




logger = logging.getLogger(__name__)


def handle_mcp_command(app, session_id, line, write):
    parts = line.split()
    try:
        if parts[0] == ':mcp-refresh':
            if len(parts) != 1:
                write('用法：:mcp-refresh；只读取用户级 settings.toml 的 MCP 配置')
                return
            write(json.dumps(app.request('POST', '/mcp/refresh'), ensure_ascii=False))
            return
        specs = {row['name']: row for row in app.request('GET', '/session-controls')['tools']}
        selected = app.request('GET', f'/sessions/{session_id}')['active_tools']
        if len(parts) == 1:
            for name, spec in specs.items():
                reason = spec.get('unavailable_reason')
                state = '不可用' if reason else '已选用' if name in selected else '未选用'
                write(f'{name}：{state}')
            write('用法：:tools +工具名 -工具名；只修改当前主 Agent 下一次模型请求的工具选择')
            return
        for token in parts[1:]:
            name = token[1:]
            if token[:1] not in ('+', '-') or name not in specs:
                write('工具选择无效；用 :tools 查看名称，再用 +名称 或 -名称 修改')
                return
            permission = specs[name]
            if token[0] == '+' and permission.get('unavailable_reason'):
                write('该 MCP 工具当前不可用，请检查服务配置并刷新')
                return
            if token[0] == '+' and name not in selected:
                selected.append(name)
            elif token[0] == '-' and name in selected:
                selected.remove(name)
        app.request('PATCH', f'/sessions/{session_id}/tools', json={'active_tools': selected})
        write('工具选择已保存，下一次模型请求生效。')
    except Exception as error:
        logger.warning('cli_mcp_command_failed error_type=%s', type(error).__name__)
        write('MCP 配置或工具选择未更新，请检查配置后重试。')
