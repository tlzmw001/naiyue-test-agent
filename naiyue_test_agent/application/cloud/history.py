"""Owner history DTOs and signed fixed-coverage cursors, without task storage access."""
import base64
import hmac
import json
import time
from hashlib import sha256

from langchain_core.messages import messages_from_dict
from pydantic import TypeAdapter,ValidationError

from naiyue_test_agent.agent_runtime.message_presentation import message_view
from naiyue_test_agent.contracts.cloud_history import Identity
from naiyue_test_agent.contracts.cloud_events import canonical_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError


class CloudArchive:
    def __init__(self,runtime):self.runtime=runtime

    def _limit(self,limit):
        maximum=self.runtime.settings.history_page_limit
        if limit is None:return maximum
        if type(limit) is not int or not 1<=limit<=maximum:raise CloudError('invalid_history_page',422)
        return limit

    def _identity(self,value):
        try:return TypeAdapter(Identity).validate_python(value)
        except ValidationError:raise CloudError('invalid_history_identity',422) from None

    def _sign(self,raw):
        key=self.runtime.settings.owner_token.get_secret_value().encode()
        return hmac.new(key,b'cloud-history-v2\0'+raw,sha256).hexdigest()

    def _cursor(self,task_id,kind,thread,result,position):
        value=dict(instance=self.runtime.instance_id,task_id=task_id,kind=kind,thread=thread,
            epoch=result['epoch'],upper=result['coverage_seq'],position=position,
            expires=time.time()+self.runtime.settings.history_limits.cursor_seconds)
        raw=canonical_json(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip('=')+'.'+self._sign(raw)

    def _decode(self,cursor,task_id,kind,thread):
        try:
            if not isinstance(cursor,str) or len(cursor)>4096:raise ValueError()
            encoded,signature=cursor.split('.')
            raw=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True)
            if not hmac.compare_digest(signature,self._sign(raw)):raise ValueError()
            value=json.loads(raw)
            if (value['instance']!=self.runtime.instance_id or value['task_id']!=task_id or value['kind']!=kind
                    or thread is not None and value['thread']!=thread or value['expires']<time.time()):raise ValueError()
            return value
        except (ValueError,KeyError,TypeError,UnicodeError):raise CloudError('invalid_history_cursor',422) from None

    def _bounded(self,result):
        if len(canonical_json(result).encode())>self.runtime.settings.max_frame_bytes:
            raise CloudError('history_view_too_large',413)
        return result

    def page(self,task_id,*,thread_id=None,limit=None,cursor=None):
        limit=self._limit(limit)
        if thread_id is not None:self._identity(thread_id)
        selected=self._decode(cursor,task_id,'messages',thread_id) if cursor else None
        if selected:thread_id=selected['thread']
        r=self.runtime
        with r.gates.hold(task_id):
            result=r._history(task_id).archive_page(thread_id=thread_id,limit=limit,
                read_timeout=r.settings.history_read_timeout_seconds,
                upper=selected['upper'] if selected else None,before=selected['position'] if selected else None)
        if selected and result['epoch']!=selected['epoch']:raise CloudError('history_epoch_mismatch')
        thread=result['thread']['thread_id'] if result['thread'] else thread_id
        raw=result.pop('messages');before=result.pop('next_before')
        messages=[]
        for item in raw:
            messages.append(item['preview']|dict(revision=item['revision'],sequence=item['position'],
                parent_message_id=item['parent_message_id'],reply_id=item['reply_id'],owner_input=item['owner_input'],
                body_sha256=item['body_hash'],occurred_at=item['occurred_at'],boundary=item['boundary'],
                detail=dict(message_id=item['message_id'],thread_id=thread,revision=item['revision'])))
        result.update(messages=messages,next_cursor=self._cursor(task_id,'messages',thread,result,before) if before is not None else None)
        while len(canonical_json(result).encode())>r.settings.max_frame_bytes and len(messages)>1:
            messages.pop(0)
            result['next_cursor']=self._cursor(task_id,'messages',thread,result,messages[0]['sequence'])
        return self._bounded(result)

    def threads(self,task_id,*,limit=None,cursor=None):
        limit=self._limit(limit);selected=self._decode(cursor,task_id,'threads',None) if cursor else None
        r=self.runtime
        with r.gates.hold(task_id):
            result=r._history(task_id).archive_threads(limit=limit,read_timeout=r.settings.history_read_timeout_seconds,
                upper=selected['upper'] if selected else None,after=selected['position'] if selected else '')
        if selected and result['epoch']!=selected['epoch']:raise CloudError('history_epoch_mismatch')
        after=result.pop('next_after')
        result['next_cursor']=self._cursor(task_id,'threads',None,result,after) if after is not None else None
        while len(canonical_json(result).encode())>r.settings.max_frame_bytes and len(result['threads'])>1:
            result['threads'].pop()
            result['next_cursor']=self._cursor(task_id,'threads',None,result,result['threads'][-1]['thread_id'])
        return self._bounded(result)

    def detail(self,task_id,message_id,*,thread_id,revision,offset=0,limit=None,field='content'):
        self._identity(thread_id);self._identity(message_id)
        maximum=self.runtime.settings.history_limits.detail_chars
        limit=maximum if limit is None else limit
        if (type(revision) is not int or not 1<=revision<=9223372036854775807 or type(offset) is not int or offset<0
                or type(limit) is not int or not 1<=limit<=maximum
                or field not in ('content','reasoning','tool_calls','file_change','mcp_result')):
            raise CloudError('invalid_history_detail',422)
        r=self.runtime
        with r.gates.hold(task_id):
            saved=r._history(task_id).archive_message(thread_id=thread_id,message_id=message_id,revision=revision,
                read_timeout=r.settings.history_read_timeout_seconds)
        view=message_view(messages_from_dict([json.loads(saved['body_json'])])[0])
        if field not in view:raise CloudError('history_field_not_found',404)
        text=view[field] if isinstance(view[field],str) else canonical_json(view[field])
        if offset>len(text):raise CloudError('history_offset_ahead',422)
        selected=text[offset:offset+limit]
        result=dict(message_id=message_id,thread_id=thread_id,revision=revision,field=field,offset=offset,
            total_chars=len(text),body_sha256=saved['body_hash'],text=selected,
            next_offset=offset+len(selected) if offset+len(selected)<len(text) else None)
        while len(canonical_json(result).encode())>r.settings.max_frame_bytes and len(selected)>1:
            selected=selected[:len(selected)//2];result.update(text=selected,next_offset=offset+len(selected))
        return self._bounded(result)
