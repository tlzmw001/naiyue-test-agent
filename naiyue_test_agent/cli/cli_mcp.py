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
        options = app.request('GET', '/session-controls')
        specs = {row['name']: row for row in options['tools']}
        groups = {f"mcp:{row['server_id']}": row['enabled'] for row in options.get('mcp_servers', [])}
        selected = app.request('GET', f'/sessions/{session_id}')['active_tools']

        def choice(name):
            source = specs.get(name, {}).get('mcp_source')
            if source:
                group = 'mcp:' + source['server_id']
                if group in groups:
                    return group
            if name.startswith('mcp__') and name not in specs:
                parts = name.split('__')
                if (len(parts) == 3 and not parts[1].endswith('_') and not parts[2].startswith('_')
                        and 'mcp:' + parts[1] in groups):
                    return 'mcp:' + parts[1]
            return name

        choices = {name: not spec.get('unavailable_reason') for name, spec in specs.items() if choice(name) == name} | groups
        if len(parts) == 1:
            current = {choice(name) for name in selected}
            for name, available in choices.items():
                state = '不可用' if not available else '已选用' if name in current else '未选用'
                write(f'{name}：{state}')
            write('用法：:tools +工具名 -工具名 或 :tools +mcp:服务ID；下一次模型请求生效')
            return
        for token in parts[1:]:
            name = choice(token[1:])
            if token[:1] not in ('+', '-') or name not in choices:
                write('工具选择无效；用 :tools 查看名称，再用 +名称 或 -名称 修改')
                return
            if token[0] == '+' and not choices[name]:
                write('该 MCP 工具当前不可用，请检查服务配置并刷新')
                return
            selected = [value for value in selected if choice(value) != name]
            if token[0] == '+':
                selected.append(name)
        app.request('PATCH', f'/sessions/{session_id}/tools', json={'active_tools': selected})
        write('工具选择已保存，下一次模型请求生效。')
    except Exception as error:
        logger.warning('cli_mcp_command_failed error_type=%s', type(error).__name__)
        write('MCP 配置或工具选择未更新，请检查配置后重试。')
