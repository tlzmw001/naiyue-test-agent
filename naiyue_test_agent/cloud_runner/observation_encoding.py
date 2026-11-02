"""Finite public facts from the existing runtime event contract."""
from dataclasses import asdict

from naiyue_test_agent.model_access.messages import content_text
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.agent_runtime.message_presentation import message_content_id


def encode_event(event,thread,limits):
    kind=event.event_type;d=event.data
    if kind=='message_snapshot_unavailable':raise ValueError('runtime_snapshot_unavailable')
    if kind in ('message_delta','message_reasoning_delta'):
        if len(d.text)>limits.text_chars:raise ValueError('delta_budget')
        data=dict(message_id=d.message_id,text=d.text,index=d.index,end_index=d.index,reply_id=d.reply_id)
    elif kind in ('message_completed','message_revised'):
        row=parse_object(d.snapshot_json,byte_limit=limits.record_bytes*8,depth_limit=64)
        if row.get('type') not in ('human','ai','tool'):return None
        text=content_text(row.get('content',''));reason=row.get('reasoning')
        data=dict(id=row['id'],type=row['type'],content=text[:limits.text_chars],content_id=message_content_id(row),
            reply_id=row.get('reply_id'),is_context_summary=bool(row.get('is_context_summary')),
            content_truncated=len(text)>limits.text_chars)
        if isinstance(reason,str):data.update(reasoning=reason[:limits.text_chars],reasoning_truncated=len(reason)>limits.text_chars)
        if row['type']=='ai':
            calls=row.get('tool_calls',[])
            data.update(tool_calls=[dict(id=c['id'],name=c['name'],args={}) for c in calls[:128]],
                tool_calls_truncated=len(calls)>128,arguments_truncated=any(c.get('args') for c in calls))
        if row['type']=='tool':data.update({k:row.get(k) for k in ('tool_call_id','name','status')})
    elif kind in ('model_call_started','model_call_finished'):
        data=asdict(d);data.pop('http_capture_status',None)
    elif kind in ('tool_call_started','tool_call_finished'):
        data={k:getattr(d,k) for k in ('tool_call_id','tool_name','boundary')}
        if kind=='tool_call_finished':
            data.update(outcome=d.outcome,reason=d.reason,duration_ms=d.duration_ms,
                error_type=d.failure.error_type if d.failure else None)
    elif kind=='tool_progress':
        data={k:getattr(d,k) for k in ('tool_call_id','tool_name','activity','phase')}
        data['text']=d.text[-limits.text_chars:]
    elif kind in ('run_started','run_finished'):
        data=dict(status='running' if kind=='run_started' else d.status,
            error_type=d.failure.error_type if kind=='run_finished' and d.failure else None)
    else:return None
    return dict(event_type=kind,run_id=event.run_id,thread_id=thread,
        source=asdict(event.source) if event.source else None,data=data)
