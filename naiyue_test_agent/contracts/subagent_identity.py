"""Stable delegation identity and saved ownership matching."""

import hashlib
import json
import re


def subagent_identity(session_id, batch_id, tool_call_id):
    return hashlib.sha256(json.dumps([session_id, batch_id, tool_call_id]).encode()).hexdigest()

def subagent_target(session_id, batch_id, tool_call_id, agent_id, *, agent, owned_instances=None):
    if agent_id is None:
        return subagent_identity(session_id, batch_id, tool_call_id)
    if not isinstance(agent_id, str) or not re.fullmatch('[0-9a-f]{64}', agent_id):
        return None
    record = (owned_instances or {}).get(agent_id)
    if (record is None or record['session_id'] != session_id or record['agent'] != agent
            or record['agent_path'] != f'root/{agent}/{agent_id}'
            or record['child_thread_id'] != f'subagent:{agent_id}'):
        return None
    return agent_id
