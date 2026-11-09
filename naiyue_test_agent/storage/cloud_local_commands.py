"""Durable task commands and draft CAS on the existing controlled project DB."""
import json
import time
from pydantic import ValidationError
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .cloud_local import _read
from .queries import session_row

TERMINAL={'succeeded','failed','rejected','cancelled'}
RETIRED={'cloud_task_deleted','cloud_task_removed'}


class LocalTaskCommands:
    def __init__(self,database):self.database=database

    def _save(self,c,op):
        op['updated_at']=time.time()
        c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(op),op['command_id']))
        return op

    def _binding(self,c,bid,sid):return _read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')

    def index(self,bid,sid,*,offset=0,limit=50):
        if not 0<=offset<=1000000 or not 1<=limit<=100:raise CloudError('invalid_cloud_page',422)
        with self.database.transaction(write=False,error_code='cloud_local_read_failed') as c:
            self._binding(c,bid,sid)
            rows=c.execute('''SELECT command_id FROM naiyue_cloud_operations WHERE binding_id=? AND session_id=?
                ORDER BY json_extract(body_json,'$.created_at') DESC,command_id LIMIT ? OFFSET ?''',(bid,sid,limit,offset)).fetchall()
            return [row[0] for row in rows]

    def draft(self,bid,sid):
        with self.database.transaction(write=False,error_code='cloud_local_read_failed') as c:
            binding=self._binding(c,bid,sid)
            return self._draft(binding)

    def _draft(self,binding):
        return dict(binding_id=binding['binding_id'],local_session_id=binding['local_session_id'],
            draft_revision=binding.get('draft_revision',0),**(dict(text='',model_id=None,thinking_level='model_default',attachment_ids=[])| (binding.get('draft') or {})))

    def save_draft(self,bid,sid,request):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            binding=self._binding(c,bid,sid)
            if not binding['retained']:raise CloudError('cloud_task_unavailable')
            if binding.get('draft_revision',0)!=request['expected_draft_revision']:raise CloudError('cloud_draft_conflict')
            binding['draft']=dict(text=request['text'],model_id=request['model_id'],thinking_level=request.get('thinking_level','model_default'),attachment_ids=request.get('attachment_ids',[]))
            binding['draft_revision']=request['expected_draft_revision']+1
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
            return self._draft(binding)

    def reserve(self,bid,sid,kind,request):
        if kind not in ('message','stop','recover','start','release','delete','result_retry','result_abandon'):raise CloudError('invalid_request',422)
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            session_row(c,sid);binding=self._binding(c,bid,sid)
            fingerprint=digest(dict(binding_id=bid,instance_id=binding['instance_id'],kind=kind,request=request))
            old=c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE command_id=?',(request['command_id'],)).fetchone()
            if old:
                value=json.loads(old[0])
                if value['request_digest']!=fingerprint:raise CloudError('cloud_command_conflict')
                return value
            if not binding['retained'] or binding['task_id'] is None:raise CloudError('cloud_task_unavailable')
            if binding['revision']!=request['expected_binding_revision']:raise CloudError('cloud_binding_conflict')
            self._check_pending(c,bid,sid,kind,request)
            if kind=='message' and request.get('draft_revision') is not None:
                draft=self._draft(binding)
                if (draft['draft_revision']!=request['draft_revision'] or draft['text']!=request['text']
                        or draft['model_id']!=request['model_id'] or draft['thinking_level']!=request.get('thinking_level','model_default')
                        or draft['attachment_ids']!=request.get('attachment_ids',[])):raise CloudError('cloud_draft_conflict')
            attachments=[]
            if kind=='message':
                ids=request.get('attachment_ids',[])
                if len(set(ids))!=len(ids):raise CloudError('invalid_request',422)
                for aid in ids:
                    item=binding.get('local_attachments',{}).get(aid)
                    if item is None:raise CloudError('attachment_not_found',404)
                    if item['phase'] not in ('ready','attached'):raise CloudError('attachment_not_ready')
                    attachments.append({key:item[key] for key in ('attachment_id','input_id','display_name','size')})
                for aid in ids:binding['local_attachments'][aid]['phase']='attached'
                if ids:c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
            now=time.time()
            value=dict(command_id=request['command_id'],binding_id=bid,local_session_id=sid,kind=kind,request=request,
                request_digest=fingerprint,instance_id=binding['instance_id'],task_id=binding['task_id'],phase='reserved',
                steps={'primary':{'command_id':request['command_id']}},error_code=None,run_id=None,created_at=now,updated_at=now)
            if kind=='message':
                value['steps']['environment']={'command_id':str(uuid4())}
                if attachments:value['attachments']=attachments
            if kind=='release' and request['acknowledge_unknown']:
                cloud=binding['cloud']
                if cloud is None:raise CloudError('cloud_confirmation_changed')
                value['confirmation']={k:cloud[k] for k in ('revision','generation','active_run_id')}
            c.execute('INSERT INTO naiyue_cloud_operations VALUES(?,?,?,?)',
                (value['command_id'],bid,sid,json.dumps(value)))
            return value

    def _check_pending(self,c,bid,sid,kind,request):
        if kind=='delete':return  # Remote preview/CAS and explicit unknown acknowledgement remain authoritative.
        rows=c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE binding_id=? AND session_id=?',(bid,sid)).fetchall()
        for row in rows:
            old=json.loads(row[0])
            if old['kind'] in ('create','queued_message') or old['phase'] in TERMINAL:continue
            pending=old['phase']!='accepted'
            if kind in ('stop','recover'):
                if old['kind']=='message' and request.get('run_id') is not None:continue
                pending=pending or old['kind'] in ('stop','recover')
            if pending:raise CloudError('cloud_command_pending')

    def fix(self,cid,sid,step,payload,path,operation,fingerprint):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=self._writable(c,cid,sid);item=op['steps'][step]
            fixed=dict(request=payload,path=path,operation=operation,request_digest=fingerprint)
            if 'request' in item:
                if any(item.get(k)!=v for k,v in fixed.items()):raise CloudError('cloud_command_conflict')
            else:item.update(fixed,sent=False)
            op['phase']='fixed';return self._save(c,op)

    def dispatch(self,cid,sid,step):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=self._writable(c,cid,sid)
            op['steps'][step]['sent']=True;op['phase']='dispatching';return self._save(c,op)

    def reject(self,cid,sid,step,code):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=self._writable(c,cid,sid)
            op['steps'][step]['rejected']=code
            op.update(phase='rejected',error_code=code);return self._save(c,op)

    def _writable(self,c,cid,sid):
        op=_read(c,'naiyue_cloud_operations',cid,sid,'cloud_operation_not_found')
        if op.get('error_code') in RETIRED:raise CloudError('cloud_task_unavailable')
        return op

    def record(self,cid,sid,step,receipt):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=_read(c,'naiyue_cloud_operations',cid,sid,'cloud_operation_not_found')
            if op.get('error_code') in RETIRED:return op
            item=op['steps'][step]
            old=item.get('receipt')
            if old and (old['status'] in ('succeeded','failed')
                    or old['status']=='unknown' and receipt['status'] in ('accepted','running')):return op
            item['receipt']=receipt
            if step=='primary':
                if op['kind']=='delete' and receipt['status']=='succeeded':self._deleted(c,op,receipt)
                op['phase']='accepted' if receipt['status'] in ('accepted','running') else receipt['status']
                op['run_id']=receipt['result'].get('command',{}).get('run_id')
                if op['kind']=='message' and receipt['status']!='unknown':self._clear_draft(c,op)
            elif 'receipt' not in op['steps']['primary']:
                op['phase']='waiting_runner' if receipt['status']=='succeeded' else 'unknown'
            op['error_code']=receipt.get('error_code');return self._save(c,op)

    def _deleted(self,c,op,receipt):
        from naiyue_test_agent.contracts.cloud_delete import DeleteTombstone
        binding=self._binding(c,op['binding_id'],op['local_session_id']);item=op['steps']['primary']
        try:tombstone=DeleteTombstone.model_validate(receipt.get('result',{}).get('tombstone')).model_dump()
        except ValidationError:raise CloudError('cloud_delete_receipt_mismatch',502) from None
        expected=dict(instance_id=binding['instance_id'],task_id=binding['task_id'],
            delete_command_id=op['command_id'],generation=op['request']['expected_generation'])
        if (any(tombstone.get(k)!=v for k,v in expected.items()) or receipt.get('operation')!='task:delete'
                or receipt.get('command_id')!=op['command_id'] or receipt.get('resource_id')!=binding['task_id']
                or receipt.get('request_digest')!=item.get('request_digest')
                or receipt.get('expected_generation')!=expected['generation']
                or receipt.get('result',{}).get('phase')!='finished'):
            raise CloudError('cloud_delete_receipt_mismatch',502)
        binding['deletion']=tombstone
        self._retire(c,binding,op['command_id'],'cloud_task_deleted')

    def _retire(self,c,binding,command_id,code):
        binding.update(retained=False,cloud=None,draft=None,local_attachments={},queued_command_id=None,
            draft_revision=binding.get('draft_revision',0)+1,revision=binding['revision']+1)
        c.execute('UPDATE naiyue_cloud_bindings SET retained=0,body_json=? WHERE binding_id=?',
            (json.dumps(binding),binding['binding_id']))
        for row in c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE binding_id=? AND command_id!=?',
                (binding['binding_id'],command_id)).fetchall():
            old=json.loads(row[0])
            cancelled=old['kind']=='create' or code=='cloud_task_removed' and old['kind']=='queued_message'
            old.update(phase='cancelled' if cancelled else 'failed',error_code=code,
                request={'command_id':old['command_id']},steps={},updated_at=time.time())
            old.pop('dispatch_request',None);old.pop('attachments',None)
            if old['kind']=='create':old.update(cancel_requested=True,may_have_task=False)
            c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(old),old['command_id']))

    def forget(self,bid,sid,request):
        if request.get('acknowledge_unmanaged') is not True:raise CloudError('invalid_request',422)
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            binding=self._binding(c,bid,sid)
            if not binding['retained']:return binding
            if binding['revision']!=request['expected_binding_revision']:raise CloudError('cloud_binding_conflict')
            if c.execute('SELECT 1 FROM naiyue_cloud_operations WHERE command_id=?',(request['command_id'],)).fetchone():
                raise CloudError('cloud_command_conflict')
            binding['removal']=dict(command_id=request['command_id'],removed_at=time.time())
            self._retire(c,binding,request['command_id'],'cloud_task_removed')
            return self._binding(c,bid,sid)

    def adopt_deleted(self,bid,sid,task,raw):
        from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt
        from naiyue_test_agent.contracts.cloud_local_tasks import checked_task
        try:receipt=CommandReceipt.model_validate(raw).model_dump(mode='json')
        except ValidationError:raise CloudError('cloud_delete_receipt_mismatch',502) from None
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            binding=self._binding(c,bid,sid)
            if not binding['retained']:return binding
            checked=checked_task(task,binding)
            if (checked.lifecycle!='deleted' or checked.delete_command_id!=receipt['command_id']
                    or receipt['status']!='succeeded' or receipt['expected_generation']!=checked.generation):
                raise CloudError('cloud_delete_receipt_mismatch',502)
            cid=receipt['command_id'];now=time.time()
            old=c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE command_id=?',(cid,)).fetchone()
            if old:
                op=json.loads(old[0])
                if op['binding_id']!=bid or op['local_session_id']!=sid or op['kind']!='delete':
                    raise CloudError('cloud_command_conflict')
            else:
                op=dict(command_id=cid,binding_id=bid,local_session_id=sid,kind='delete',instance_id=binding['instance_id'],
                    task_id=binding['task_id'],request_digest=receipt['request_digest'],phase='reserved',error_code=None,
                    request=dict(command_id=cid,expected_generation=checked.generation),run_id=None,created_at=now,updated_at=now,
                    steps={'primary':dict(command_id=cid,operation='task:delete',request_digest=receipt['request_digest'])})
                c.execute('INSERT INTO naiyue_cloud_operations VALUES(?,?,?,?)',(cid,bid,sid,json.dumps(op)))
            self._deleted(c,op,receipt)
            op['steps']['primary']['receipt']=receipt;op.update(phase='succeeded',error_code=None)
            self._save(c,op)
            return self._binding(c,bid,sid)

    def _clear_draft(self,c,op):
        request=op['request'];binding=self._binding(c,op['binding_id'],op['local_session_id'])
        if request.get('draft_revision') is None:return
        draft=self._draft(binding)
        if (draft['draft_revision']==request['draft_revision'] and draft['text']==request['text']
                and draft['model_id']==request['model_id'] and draft['thinking_level']==request.get('thinking_level','model_default')
                and draft['attachment_ids']==request.get('attachment_ids',[])):
            binding['draft']=None;binding['draft_revision']=draft['draft_revision']+1
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),binding['binding_id']))

    def fail(self,cid,sid,phase,code):
        with self.database.transaction(write=True,error_code='cloud_local_save_failed') as c:
            op=_read(c,'naiyue_cloud_operations',cid,sid,'cloud_operation_not_found')
            if op.get('error_code') in RETIRED:return op
            if op['phase'] in ('accepted','succeeded','failed') and op['steps']['primary'].get('receipt'):return op
            op.update(phase=phase,error_code=code);return self._save(c,op)
