"""Safe MCP display facts shared by live snapshots, history and tool selection."""

import re

from langchain_core.messages import ToolMessage


OUTCOMES = ('success', 'tool_error', 'not_executed', 'outcome_unknown', 'unsupported_result')
ARCHIVE_REASONS = ('command_limit', 'workspace_limit', 'directory_unavailable', 'write_error',
                   'workspace_unavailable')


def source_projection(value):
    if not isinstance(value, dict):
        return None
    server, remote = value.get('server_id'), value.get('remote_tool')
    if (not isinstance(server, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,16}', server)
            or not isinstance(remote, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', remote)
            or len(f'mcp__{server}__{remote}') > 64):
        return None
    result = {'server_id': server, 'remote_tool': remote}
    if value.get('transport') in ('stdio', 'streamable_http'):
        result['transport'] = value['transport']
    return result


def permission_source(permission):
    from .tools import MCPPermission
    if isinstance(permission, MCPPermission):
        return source_projection({'server_id': permission.server_id, 'remote_tool': permission.remote_tool,
                                  'transport': permission.transport})
    return None


def complete_result(message, permission):
    """Tag shared authorization/error feedback only for a genuine selected MCP spec."""
    source = permission_source(permission)
    if source is None or not isinstance(message, ToolMessage):
        return message
    artifact = dict(message.artifact) if isinstance(message.artifact, dict) else {}
    if message.additional_kwargs.get('naiyue_not_executed'):
        outcome = 'not_executed'
    elif artifact.get('outcome') in OUTCOMES:
        outcome = artifact['outcome']
    elif message.status == 'error':
        outcome = 'outcome_unknown'
    else:
        outcome = 'success'
    artifact.update(type='mcp_result', version=1, outcome=outcome, transport=permission.transport,
                    source={key: source[key] for key in ('server_id', 'remote_tool')})
    return message.model_copy(update={'artifact': artifact})


def result_projection(artifact, *, name):
    if not isinstance(artifact, dict):
        return None
    source = source_projection(artifact.get('source'))
    if source is None or name != f'mcp__{source["server_id"]}__{source["remote_tool"]}':
        return None
    if 'type' in artifact:
        if (artifact['type'] != 'mcp_result' or type(artifact.get('version')) is not int
                or artifact['version'] != 1 or artifact.get('outcome') not in OUTCOMES):
            return None
        outcome = artifact['outcome']
    elif type(artifact.get('is_error')) is bool and type(artifact.get('truncated')) is bool:
        outcome = 'tool_error' if artifact['is_error'] else 'success'
    else:
        return None
    if artifact.get('transport') in ('stdio', 'streamable_http'):
        source['transport'] = artifact['transport']
    result = {'version': 1, 'source': source, 'outcome': outcome}
    for key in ('truncated', 'archive_complete'):
        if type(artifact.get(key)) is bool:
            result[key] = artifact[key]
    if type(artifact.get('result_bytes')) is int and 0 <= artifact['result_bytes'] <= 2**63 - 1:
        result['result_bytes'] = artifact['result_bytes']
    if artifact.get('archive_reason') in ARCHIVE_REASONS:
        result['archive_reason'] = artifact['archive_reason']
    paths = artifact.get('archive_paths')
    if isinstance(paths, dict):
        result['archive_paths'] = {key: paths[key] for key in ('raw', 'text') if isinstance(paths.get(key), str)
            and re.fullmatch(r'naiyue-agent-output/[0-9a-f]{32}-' + key + r'\.log', paths[key])}
    return result
