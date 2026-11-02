"""Pure validation/projection inside the journal transaction; never reads a task DB."""
import json
from hashlib import sha256
from itertools import chain
from pydantic import TypeAdapter

from langchain_core.messages import messages_from_dict
from naiyue_test_agent.agent_runtime.message_presentation import message_view
from naiyue_test_agent.contracts.cloud_history import validated_record, MessageRecord,MessagePart,MessageCommit,Identity
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_events import canonical_json, digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .history_legacy import remember


def public_body(record,limits):
    try:
        raw=parse_object(record.message.body_json,byte_limit=limits.max_message_bytes,depth_limit=limits.body_depth)
        if (not isinstance(raw.get('data'),dict) or raw.get('type')!=record.message.message_type
                or raw['data'].get('id')!=record.message.message_id):
            raise ValueError('message_identity')
        message=messages_from_dict([raw])[0]
        if record.message.message_type=='tool':
            TypeAdapter(Identity).validate_python(message.tool_call_id)
            if message.name is not None:TypeAdapter(Identity).validate_python(message.name)
        if record.message.message_type=='ai':
            for call in message.tool_calls:
                TypeAdapter(Identity).validate_python(call['id']);TypeAdapter(Identity).validate_python(call['name'])
        public=message_view(message)
        if record.message.message_type!='tool' and record.message.reply_id!=public['reply_id']:
            raise ValueError('reply_identity')
        return public
    except (KeyError,TypeError,ValueError,AttributeError,NotImplementedError):
        raise CloudError('invalid_history_body',422) from None


def _message(c,event,payload,limits):
    record=validated_record(payload,limits);m=record.message;t=record.thread
    public=public_body(record,limits)
    last=c.execute('''SELECT revision,position,record_json FROM history_messages
        WHERE thread_id=? AND message_id=? ORDER BY revision DESC LIMIT 1''',(t.thread_id,m.message_id)).fetchone()
    legacy=c.execute('SELECT 1 FROM history_legacy WHERE thread_id=? AND message_id=?',(t.thread_id,m.message_id)).fetchone()
    if ((last and m.revision!=last['revision']+1) or (not last and not legacy and m.revision!=1)
            or (last and m.position!=last['position'])):
        raise CloudError('history_revision_conflict')
    if not last and c.execute('SELECT 1 FROM history_messages WHERE thread_id=? AND position=?',
            (t.thread_id,m.position)).fetchone():raise CloudError('history_position_conflict')
    prior=c.execute('SELECT record_json FROM history_threads WHERE thread_id=? ORDER BY seq DESC LIMIT 1',
        (t.thread_id,)).fetchone()
    if prior and any(json.loads(prior[0])[k]!=getattr(t,k) for k in ('role','agent_path','agent_id')):
        raise CloudError('history_thread_conflict')
    if t.role=='root':
        roots=c.execute('SELECT DISTINCT thread_id FROM history_threads WHERE thread_id<>?',(t.thread_id,))
        if any(json.loads(c.execute('SELECT record_json FROM history_threads WHERE thread_id=? LIMIT 1',(r[0],)).fetchone()[0])['role']=='root' for r in roots):
            raise CloudError('history_root_conflict')
    else:
        parents=c.execute('SELECT body_json FROM history_messages WHERE run_id=? AND message_type=?',
            (t.parent_run_id,'ai'))
        legacy_parents=c.execute('SELECT body_json FROM history_legacy WHERE run_id=?',(t.parent_run_id,))
        if not any(any(call.get('id')==t.parent_tool_call_id for call in json.loads(p[0])['data'].get('tool_calls',[])) for p in chain(parents,legacy_parents)):
            raise CloudError('history_child_parent_missing')
    if m.message_type=='tool':
        parent=c.execute('''SELECT body_json,record_json,position FROM history_messages WHERE thread_id=? AND message_id=?
            ORDER BY revision DESC LIMIT 1''',(t.thread_id,m.parent_message_id)).fetchone()
        if not parent:
            parent=c.execute('SELECT body_json,record_json,NULL AS position FROM history_legacy WHERE thread_id=? AND message_id=?',
                (t.thread_id,m.parent_message_id)).fetchone()
        if not parent:raise CloudError('history_parent_missing')
        raw=json.loads(parent[0])
        if (raw['type']!='ai' or (parent['position'] is not None and parent['position']>=m.position)
                or json.loads(parent['record_json']).get('reply_id')!=m.reply_id
                or not any(call.get('id')==public['tool_call_id'] for call in raw['data'].get('tool_calls',[]))):
            raise CloudError('history_parent_mismatch')
    elif m.parent_message_id is not None:raise CloudError('history_parent_mismatch')
    if last:
        previous=json.loads(last['record_json'])
        if any(previous[k]!=getattr(m,k) for k in ('message_type','reply_id','parent_message_id')):
            raise CloudError('history_message_identity_conflict')
    owner=False
    if t.role=='root' and m.message_type=='human':
        receipt=c.execute('SELECT run_id,message FROM history_owners WHERE command_id=?',(record.command_id,)).fetchone()
        claimed=c.execute('SELECT message_id FROM history_messages WHERE run_id=? AND owner_input=1 LIMIT 1',(event.run_id,)).fetchone()
        owner=bool(receipt and receipt['run_id']==event.run_id and receipt['message']==public['content']
            and (not claimed or claimed[0]==m.message_id))
    preview={k:public[k] for k in ('id','type','is_context_summary','reply_id','provisional','state')}
    preview['reply_id']=m.reply_id
    if m.message_type=='tool':preview.update({k:public[k] for k in ('tool_call_id','status','name')})
    preview.update(content=public['content'][:limits.preview_chars],
        content_truncated=len(public['content'])>limits.preview_chars,
        detail_fields=[k for k in ('content','reasoning','tool_calls','file_change','mcp_result') if k in public])
    meta=m.model_dump(exclude={'body_json'})
    c.execute('INSERT INTO history_messages VALUES(?,?,?,?,?,?,?,?,?,?,?)',(t.thread_id,m.message_id,m.revision,
        event.seq,m.position,canonical_json(meta),m.body_json,canonical_json(preview),int(owner),event.run_id,m.message_type))
    c.execute('INSERT INTO history_threads VALUES(?,?,?)',(t.thread_id,event.seq,canonical_json(t.model_dump())))
    if record.boundary:
        previous=c.execute('SELECT record_json FROM history_replies WHERE thread_id=? AND reply_id=? ORDER BY seq DESC LIMIT 1',
            (t.thread_id,record.boundary.reply_id)).fetchone()
        boundary=canonical_json(record.boundary.model_dump())
        if previous and previous[0]!=boundary:raise CloudError('history_reply_conflict')
        c.execute('INSERT INTO history_replies VALUES(?,?,?,?)',(t.thread_id,record.boundary.reply_id,event.seq,boundary))


def project(c,event,limits):
    p=event.payload;kind=p.get('record')
    if kind in ('run_finished','control_finished') and c.execute('SELECT 1 FROM history_transfers LIMIT 1').fetchone():
        raise CloudError('history_parts_pending')
    if p.get('version')!=2:
        remember(c,event,limits)
        return
    if event.kind!='history_record':raise CloudError('invalid_history_record',422)
    if kind=='message':return _message(c,event,p,limits)
    if kind=='message_part':
        try:MessagePart.model_validate(p)
        except ValueError:raise CloudError('invalid_history_part',422) from None
        expected={'record','version','transfer_id','index','text'}|({'header'} if p.get('index')==0 else set())
        if set(p)!=expected or type(p.get('index')) is not int or not isinstance(p.get('text'),str) or not p['text']:
            raise CloudError('invalid_history_part',422)
        if p['index']>=limits.max_parts:raise CloudError('history_too_many_parts',413)
        if p['index']==0:
            header=p['header']
            if not isinstance(header,dict) or digest(header)!=p['transfer_id']:raise CloudError('invalid_history_part',422)
            try:MessageRecord.model_validate(header|{'message':header.get('message',{})|{'body_json':''}})
            except (ValueError,TypeError):raise CloudError('invalid_history_part',422) from None
            if c.execute('SELECT COUNT(*) FROM history_transfers').fetchone()[0]>=limits.max_pending_messages:
                raise CloudError('history_pending_capacity',413)
            if c.execute('SELECT 1 FROM history_transfers WHERE transfer_id=?',(p['transfer_id'],)).fetchone():
                raise CloudError('history_transfer_conflict')
            c.execute('INSERT INTO history_transfers VALUES(?,?,?,0,0)',(p['transfer_id'],event.run_id,canonical_json(header)))
        transfer=c.execute('SELECT * FROM history_transfers WHERE transfer_id=?',(p['transfer_id'],)).fetchone()
        if not transfer or transfer['run_id']!=event.run_id or transfer['next_part']!=p['index']:
            raise CloudError('history_part_gap')
        size=len(p['text'].encode());total=transfer['body_bytes']+size
        pending=c.execute('SELECT COALESCE(SUM(body_bytes+length(CAST(header_json AS BLOB))),0) FROM history_transfers').fetchone()[0]
        if total>limits.max_message_bytes or pending+size>limits.max_pending_bytes:
            raise CloudError('history_pending_capacity',413)
        c.execute('INSERT INTO history_parts VALUES(?,?,?)',(p['transfer_id'],p['index'],p['text']))
        c.execute('UPDATE history_transfers SET next_part=next_part+1,body_bytes=? WHERE transfer_id=?',(total,p['transfer_id']))
        return
    if kind=='message_commit':
        try:MessageCommit.model_validate(p)
        except ValueError:raise CloudError('invalid_history_commit',422) from None
        if set(p)!={'record','version','transfer_id','parts','body_bytes','body_hash'}:raise CloudError('invalid_history_commit',422)
        transfer=c.execute('SELECT * FROM history_transfers WHERE transfer_id=?',(p['transfer_id'],)).fetchone()
        if not transfer or transfer['run_id']!=event.run_id or transfer['next_part']!=p['parts'] or transfer['body_bytes']!=p['body_bytes']:
            raise CloudError('history_parts_incomplete')
        body=''.join(r[0] for r in c.execute('SELECT text FROM history_parts WHERE transfer_id=? ORDER BY part',(p['transfer_id'],)))
        header=json.loads(transfer['header_json'])
        if sha256(body.encode()).hexdigest()!=p['body_hash'] or header['message']['body_hash']!=p['body_hash']:
            raise CloudError('history_body_hash_mismatch')
        _message(c,event,header|{'message':header['message']|{'body_json':body}},limits)
        c.execute('DELETE FROM history_transfers WHERE transfer_id=?',(p['transfer_id'],))
        return
    raise CloudError('invalid_history_record',422)
