"""Resolve exact saved visible sources; models cannot supply roles or time."""

import hashlib
import json
import math
import re

from naiyue_test_agent.contracts.memory import MemoryReadError


SECRET = re.compile(r'(?i)(authorization\s*[:=]|bearer\s+\S+|(?:api[_-]?key|password|passwd|token|secret)\s*[:=]\s*\S+|https?://[^\s/@]+:[^\s/@]+@|-----BEGIN .*PRIVATE KEY-----|\bsk-[a-z0-9_-]{12,})')


def sensitive(value):
    if isinstance(value,str):
        if SECRET.search(value):raise MemoryReadError('memory_sensitive_content')
    elif isinstance(value,dict):
        for key,item in value.items():
            sensitive(key)
            sensitive(item)
    elif isinstance(value,(list,tuple)):
        for item in value:sensitive(item)


def identity(row):
    # No quote/body or password hash is retained as a suppression marker.
    value = [row[k] for k in ('source_workspace_id','session_id','thread_id','message_id','revision','conversation_revision')]
    return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()


def resolve(archive, workspace_id, caller, references, now):
    result = []
    for ref in references:
        sid = caller if ref['current_user'] else ref['session_id']
        parent = archive.parent(sid)
        if parent is None:raise MemoryReadError('memory_source_unavailable')
        binding=archive.binding()
        expected = None if workspace_id == 'chats' else workspace_id
        if parent['project_id'] != expected or binding != expected:
            raise MemoryReadError('memory_source_scope_mismatch')
        thread = parent['thread_id'] if ref['current_user'] else ref['thread_id']
        if thread!=parent['thread_id']:
            child=archive.child(sid,thread)
            if (child is None or thread!='subagent:'+child['agent_id']
                    or child['agent_path']!=f"root/{child['agent']}/{child['agent_id']}"):
                raise MemoryReadError('memory_source_thread_unavailable')
        saved = archive.saved(sid,thread,ref['message_id'],current_user=ref['current_user'])
        if (saved is None or not ref['current_user'] and (saved['revision'] != ref['revision']
                or parent['conversation_revision'] != ref['conversation_revision'])):
            raise MemoryReadError('memory_source_changed')
        visible = archive.visible(sid,thread,saved['sequence'],offset=ref['offset'],chars=len(ref['quote']))
        at = saved['occurred_at']
        if (visible is None or visible['content'] != ref['quote'] or at is None
                or not math.isfinite(at) or at > now + 1):
            raise MemoryReadError('memory_source_not_supported')
        # Root human provenance is assigned only by application input admission.
        data = json.loads(saved['body_json'])['data']
        human = (saved['message_type']=='human' and thread==parent['thread_id']
            and data.get('additional_kwargs',{}).get('naiyue_human_input') is True)
        result.append(dict(source_workspace_id=workspace_id,session_id=sid,thread_id=thread,
            message_id=saved['message_id'],revision=saved['revision'],conversation_revision=parent['conversation_revision'],
            reply_id=visible['reply_id'],tool_call_id=visible['tool_call_id'],role=visible['message_type'],
            occurred_at=at,quote=ref['quote'],start_offset=ref['offset'],end_offset=ref['offset']+len(ref['quote']),
            user_origin=human,tool_success=visible.get('tool_status')=='success'))
    sensitive([r['quote'] for r in result])
    return list({identity(row):row for row in result}.values())
