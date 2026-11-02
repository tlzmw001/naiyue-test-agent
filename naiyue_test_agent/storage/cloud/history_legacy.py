"""Evidence from our own confirmed v1 journal; never invent a missing old revision."""
from hashlib import sha256

from pydantic import TypeAdapter
from langchain_core.messages import messages_from_dict
from naiyue_test_agent.contracts.cloud_history import Identity
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_events import canonical_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def remember(c,event,limits):
    p=event.payload
    if event.kind!='history_record' or p.get('record')!='message':return
    c.execute("INSERT OR REPLACE INTO meta VALUES('metadata_incomplete','1')")
    # The old journal allowed opaque diagnostic records. Invalid legacy payloads
    # remain there, but cannot prove a message identity or tool parent.
    try:
        thread=TypeAdapter(Identity).validate_python(p['thread_id']);m=p['message']
        identity=TypeAdapter(Identity).validate_python(m['message_id'])
        raw=parse_object(m['body_json'],byte_limit=limits.max_message_bytes,depth_limit=limits.body_depth)
        if (not isinstance(raw.get('data'),dict) or raw.get('type') not in ('human','ai','tool')
                or raw['data'].get('id')!=identity or sha256(m['body_json'].encode()).hexdigest()!=m['body_hash']):return
        messages_from_dict([raw])
        if raw['type']=='ai' and any(not isinstance(call,dict) or not isinstance(call.get('id'),str)
                for call in raw['data'].get('tool_calls',[])):return
    except (ValueError,KeyError,TypeError,AttributeError,NotImplementedError,CloudError):return
    c.execute('INSERT OR REPLACE INTO history_legacy VALUES(?,?,?,?,?,?)',
        (thread,identity,event.seq,event.run_id,canonical_json(m),m['body_json']))
