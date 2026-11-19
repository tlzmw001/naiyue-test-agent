"""Per-binding file custody in the existing session-owned binding transaction."""
import json,time
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .cloud_local import _read


def pending_input_releases(binding):
    return {aid:row['input_id'] for aid,row in binding.get('local_attachments',{}).items()
            if row.get('input_id') and row['phase'] in ('uploading','failed','cancelling','cancelled')
            and not row.get('input_use_released')}


def public(value):
    return {key:value.get(key) for key in ('attachment_id','binding_id','display_name','input_id','size','phase','error_code','created_at','uploaded_bytes','transfer_bytes')}


class AttachmentRepository:
    def __init__(self,database):self.database=database

    def read(self,bid,sid,aid=None):
        with self.database.transaction(write=False,error_code='cloud_attachment_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            rows=binding.get('local_attachments',{})
            if aid is None:return list(rows.values())
            if aid not in rows:raise CloudError('attachment_not_found',404)
            return rows[aid]

    def reserve(self,bid,sid,root,request):
        with self.database.transaction(write=True,error_code='cloud_attachment_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            if not binding['retained'] or not binding['task_id']:raise CloudError('cloud_task_unavailable')
            rows=binding.setdefault('local_attachments',{});aid=request['attachment_id']
            fingerprint=digest(dict(request=request,root=str(root),binding_id=bid,session_id=sid))
            if aid in rows:
                if rows[aid]['request_digest']!=fingerprint:raise CloudError('attachment_identity_conflict')
                return rows[aid]
            if len(rows)>=1024:raise CloudError('attachment_capacity')
            value=dict(**request,binding_id=bid,local_session_id=sid,root=str(root),request_digest=fingerprint,
                input_id=None,size=0,phase='uploading',error_code=None,created_at=time.time(),source_handle=None)
            rows[aid]=value
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
            return value

    def update(self,bid,sid,aid,**changes):
        with self.database.transaction(write=True,error_code='cloud_attachment_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            rows=binding.get('local_attachments',{})
            if aid not in rows:raise CloudError('attachment_not_found',404)
            if not binding['retained'] or rows[aid]['phase'] in ('cancelling','cancelled'):
                raise CloudError('attachment_cancelled')
            if rows[aid]['phase']=='attached':raise CloudError('attachment_already_sent')
            rows[aid].update(changes)
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
            return rows[aid]

    def cancel(self,bid,sid,aid,*,released=False,error_code=None):
        with self.database.transaction(write=True,error_code='cloud_attachment_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            row=binding.get('local_attachments',{}).get(aid)
            if row is None:raise CloudError('attachment_not_found',404)
            if row['phase']=='attached':raise CloudError('attachment_already_sent')
            if row.get('input_use_released'):return row
            row.update(phase='cancelled' if released else 'cancelling',error_code=error_code,
                       input_use_released=released)
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
            return row

    def release_retired(self,bid,sid,aid,ident):
        with self.database.transaction(write=True,error_code='cloud_attachment_storage_failed') as c:
            binding=_read(c,'naiyue_cloud_bindings',bid,sid,'cloud_binding_not_found')
            pending=binding.get('pending_attachment_releases',{})
            if binding['retained'] or pending.get(aid)!=ident:return
            del pending[aid]
            if not pending:
                binding.pop('pending_attachment_releases')
                if binding.get('deletion'):
                    command_id=binding['deletion']['delete_command_id']
                    c.execute("""UPDATE naiyue_cloud_operations
                        SET body_json=json_remove(body_json,'$.attachment_release_pending')
                        WHERE command_id=? AND session_id=? AND binding_id=?""",(command_id,sid,bid))
            c.execute('UPDATE naiyue_cloud_bindings SET body_json=? WHERE binding_id=?',(json.dumps(binding),bid))
