"""Bounded text/JSON projections using the existing owned output archive."""

import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from ..tools.output import ShellOutputArchive


def error_result(content, *, runtime, server_id, tool, transport, outcome):
    return ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name=tool.registered_name,
        status='error', artifact={'type': 'mcp_result', 'version': 1, 'outcome': outcome,
            'transport': transport, 'source': {'server_id': server_id, 'remote_tool': tool.remote_name}})


def format_result(result, *, runtime, server, server_id, tool):
    from mcp.types import CallToolResult
    def unsupported(content):
        return error_result(content, runtime=runtime, server_id=server_id, tool=tool,
                            transport=server.transport, outcome='unsupported_result')
    if not isinstance(result, CallToolResult) or result.result_type != 'complete':
        return unsupported('MCP interaction or extension result is unsupported; no follow-up request was sent.')
    if any(block.type != 'text' for block in result.content):
        return unsupported('MCP non-text content is unsupported; no media or resource URI was accessed.')
    value = {'content': [block.text for block in result.content], 'structured_content': result.structured_content,
             'is_error': result.is_error}
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        return unsupported('MCP returned an unsupported JSON result.')
    truncated = len(raw) > server.max_result_bytes
    artifact = {'type': 'mcp_result', 'version': 1, 'transport': server.transport,
                'outcome': 'tool_error' if result.is_error else 'success',
                'source': {'server_id': server_id, 'remote_tool': tool.remote_name},
                'truncated': truncated, 'result_bytes': len(raw), 'is_error': result.is_error,
                'archive_complete': False, 'archive_paths': {}}
    if truncated:
        root = (runtime.context or {}).get('workspace_root')
        if isinstance(root, Path) and root.is_absolute():
            archive = ShellOutputArchive(root, SimpleNamespace(max_archive_bytes=server.max_response_bytes))
            archive.record(raw, activate=True)
            saved = archive.finish()
            artifact.update(archive_complete=saved.complete, archive_paths=saved.paths, archive_reason=saved.reason)
        else:
            artifact['archive_reason'] = 'workspace_unavailable'
        archive_status = {key: artifact[key] for key in ('archive_complete', 'archive_paths', 'archive_reason')}
        notice = ('[MCP result truncated; archive status: '
                  + json.dumps(archive_status, ensure_ascii=False) + ']\n').encode('utf-8')
        # Even a budget smaller than the notice is respected, with no broken UTF-8.
        visible = (notice + raw[:max(0, server.max_result_bytes - len(notice))])[:server.max_result_bytes]
        content = visible.decode('utf-8', errors='ignore')
    else:
        content = raw.decode('utf-8')
        artifact['structured_content'] = result.structured_content
    return ToolMessage(content=content, artifact=artifact, tool_call_id=runtime.tool_call_id,
                       name=tool.registered_name, status='error' if result.is_error else 'success')
