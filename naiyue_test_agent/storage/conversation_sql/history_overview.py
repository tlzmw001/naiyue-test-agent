"""Version-bound, independently paginated saved overview projection."""
import json
from hashlib import sha256
from .history_queries import _scope,_decode,_encode,_fits,_fail,compact_summary
from naiyue_test_agent.storage.memory_sql.memory_overview_store import read_overview


def overview(connection,source,request,settings):
    target,thread,coverage=_scope(connection,source,request)
    summary=read_overview(connection,target)
    text=json.dumps(summary.get('overview'),ensure_ascii=False) if summary.get('overview') is not None else ''
    expected=dict(v=1,action='overview',source=source['session_id'],project_id=source['project_id'],
        target=target['session_id'],thread=thread,history_version=coverage['history_version'],
        summary_hash=sha256(json.dumps(summary,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    cursor=_decode(request['cursor'],expected);offset=cursor.get('offset',0) if cursor else 0
    if type(offset) is not int or not 0<=offset<=len(text):_fail('history_cursor_stale')
    size=len(text)-offset
    while True:
        end=offset+size;complete=end==len(text)
        result=dict(status='ok',items=[],coverage=coverage,overview_status=summary.get('overview_status'),
            overview=dict(content=text[offset:end],offset=offset,next_offset=end,text_chars=len(text),complete=complete),
            has_more=not complete,next_cursor=None if complete else _encode(dict(expected,offset=end)),
            overview_version=summary.get('overview_version'),overview_coverage=compact_summary(summary).get('coverage'),
            overview_updated_at=summary.get('updated_at'))
        if _fits(result,settings):return result
        if size<=1:_fail('history_output_budget')
        size//=2
