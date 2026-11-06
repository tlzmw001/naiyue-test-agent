"""One durable pending intent per binding; all old IDs retain replay protection."""
import json,time
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .cloud_local import _read


class QueueRepository:
    def __init__(self,database):self.database=database

    def read(self,bid,sid):
        with self.database.transaction(write=False,error_code='cloud_queue_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            cid=binding.get('queued_command_id')
            return _read(c,'naiyue_cloud_operations',cid,sid,'cloud_queue_not_found') if cid else None

    def reserve(self,bid,sid,request,run_id):
        fingerprint=digest(dict(binding_id=bid,request=request))
        with self.database.transaction(write=True,error_code='cloud_queue_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            old=c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE command_id=?',(request['command_id'],)).fetchone()
            if old:
                value=json.loads(old[0])
                if value['request_digest']!=fingerprint or value['kind']!='queued_message':raise CloudError('cloud_command_conflict')
                return value
            if not binding['retained'] or not binding['task_id']:raise CloudError('cloud_task_unavailable')
            if binding['revision']!=request['expected_binding_revision']:raise CloudError('cloud_binding_conflict')
            if binding.get('queued_command_id'):
                existing=_read(c,'naiyue_cloud_operations',binding['queued_command_id'],sid,'cloud_queue_not_found')
                if existing['phase'] not in ('sent','cancelled'):raise CloudError('cloud_queue_full')
            if not run_id:raise CloudError('cloud_queue_requires_run')
            from .cloud_local_commands import LocalTaskCommands
            if request.get('draft_revision') is not None:
                draft=LocalTaskCommands(self.database)._draft(binding)
                if (draft['draft_revision']!=request['draft_revision'] or draft['text']!=request['text']
                        or draft['model_id']!=request['model_id'] or draft['thinking_level']!=request.get('thinking_level','model_default')
                        or draft['attachment_ids']!=request.get('attachment_ids',[])):raise CloudError('cloud_draft_conflict')
            if len(set(request.get('attachment_ids',[])))!=len(request.get('attachment_ids',[])):raise CloudError('invalid_request',422)
            for aid in request.get('attachment_ids',[]):
                value=binding.get('local_attachments',{}).get(aid)
                if value is None:raise CloudError('attachment_not_found',404)
                if value['phase'] in ('cancelled','failed'):raise CloudError('attachment_not_ready')
            now=time.time();value=dict(command_id=request['command_id'],message_command_id=str(uuid4()),binding_id=bid,
                local_session_id=sid,task_id=binding['task_id'],instance_id=binding['instance_id'],kind='queued_message',
                request=request,request_digest=fingerprint,source_run_id=run_id,phase='waiting',steps={},error_code=None,created_at=now,updated_at=now)
            binding['queued_command_id']=request['command_id']
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
            c.execute('INSERT INTO naiyue_cloud_operations VALUES(?,?,?,?)',(value['command_id'],bid,sid,json.dumps(value)))
            from .cloud_local_commands import LocalTaskCommands
            LocalTaskCommands(self.database)._clear_draft(c,value)
            return value

    def update(self,cid,sid,phase,*,expected=('waiting','dispatching'),**changes):
        with self.database.transaction(write=True,error_code='cloud_queue_storage_failed') as c:
            value=_read(c,'naiyue_cloud_operations',cid,sid,'cloud_queue_not_found')
            if value['kind']!='queued_message':raise CloudError('cloud_queue_not_found',404)
            if value['phase'] not in expected:raise CloudError('cloud_queue_changed')
            if phase=='cancelled' and value.get('dispatch_request'):
                child=c.execute('SELECT body_json FROM naiyue_cloud_operations WHERE command_id=?',(value['message_command_id'],)).fetchone()
                if child is not None and json.loads(child[0])['phase'] not in ('rejected','failed'):
                    raise CloudError('cloud_queue_send_unconfirmed')
            value.update(phase=phase,updated_at=time.time(),**changes)
            c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(value),cid))
            return value

    def wait_for_mcp_refresh(self, cid, sid, message_id):
        with self.database.transaction(write=True, error_code='cloud_queue_storage_failed') as c:
            value = _read(c, 'naiyue_cloud_operations', cid, sid, 'cloud_queue_not_found')
            if (value['kind'] != 'queued_message' or value['phase'] != 'dispatching'
                    or value['message_command_id'] != message_id):
                raise CloudError('cloud_queue_changed')
            child = _read(c, 'naiyue_cloud_operations', message_id, sid, 'cloud_queue_not_found')
            primary = child['steps'].get('primary', {})
            if (child['kind'] != 'message' or child['binding_id'] != value['binding_id']
                    or child['request'] != value['dispatch_request'] or child['phase'] != 'rejected'
                    or primary.get('operation') != 'run:start' or not primary.get('sent')
                    or primary.get('rejected') != 'cloud_mcp_refresh_in_progress' or 'receipt' in primary):
                raise CloudError('cloud_queue_send_unconfirmed')
            # Keep the rejected child and its frozen request intact for reconciliation.
            value.pop('dispatch_request')
            value.update(phase='waiting', message_command_id=str(uuid4()), error_code=None, updated_at=time.time())
            c.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?', (json.dumps(value), cid))
            return value
