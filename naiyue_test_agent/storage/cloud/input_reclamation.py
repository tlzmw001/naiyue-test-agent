"""Atomic state transitions for one deletion receipt and one package generation."""
import time

from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt, CloudError
from .input_lifecycle import blocker, package_state, put, read
from .releases import require_available


class InputReclamation:
    def __init__(self,database):
        self.database=database

    def _load(self,c,cid,index):
        receipt=CommandReceipt.model_validate_json(c.execute(
            'SELECT record_json FROM commands WHERE command_id=?',(cid,)).fetchone()[0])
        cleanup=receipt.result['input_cleanup']
        item=cleanup['candidates'][index]
        if receipt.status!='succeeded' or receipt.operation!='task:delete':
            raise CloudError('input_cleanup_task_not_deleted')
        return receipt,cleanup,item

    def _save(self,c,receipt,cleanup):
        states=[i['state'] for i in cleanup['candidates']]
        if 'pending' in states or 'retiring' in states:
            cleanup['state']='running'
        elif 'unknown' in states:
            cleanup['state']='unknown'
        elif 'failed' in states:
            cleanup['state']='partial' if 'reclaimed' in states else 'failed'
        else:
            cleanup['state']='completed'
        updated=receipt.model_copy(update=dict(result=receipt.result|{'input_cleanup':cleanup},updated_at=max(time.time(),receipt.updated_at+0.000001)))
        c.execute('UPDATE commands SET record_json=? WHERE command_id=?',(updated.model_dump_json(),receipt.command_id))
        return updated

    def empty(self,receipt):
        with self.database.transaction(write=True) as c:
            cleanup=receipt.result.get('input_cleanup') or dict(schema=1,candidates=[],released_quota_bytes=0,
                verified_deleted_bytes=0,bytes_complete=True,reason='legacy_candidates_unavailable')
            return self._save(c,receipt,cleanup)

    def mark(self,cid,index,state,reason):
        with self.database.transaction(write=True) as c:
            receipt,cleanup,item=self._load(c,cid,index)
            if item['state']=='reclaimed':return receipt
            item.update(state=state,reason=reason)
            return self._save(c,receipt,cleanup)

    def claim(self,cid,index):
        with self.database.transaction(write=True) as c:
            receipt,cleanup,item=self._load(c,cid,index)
            ident=item['input_id']; state=package_state(c,ident)
            package=read(c,'input:'+ident)
            if item['state']=='reclaimed':return None
            if not item['incarnation_id']:
                reason='input_identity_unavailable'
            elif not state or state['incarnation_id']!=item['incarnation_id'] or state['state']=='deleted':
                reason='input_generation_changed'
            elif state['state']=='retiring':
                if state['retiring_command_id']==cid:return package
                reason='input_retirement_owned'
            elif state.get('legacy_unverified') and not read(c,'input-legacy-cutover'):
                reason='input_legacy_use_unverified'
            elif not package or digest(package)!=state['package_digest']:
                reason='input_identity_changed'
            else:
                reason=blocker(c,ident)
            if reason:
                item.update(state='retained',reason=reason)
                self._save(c,receipt,cleanup)
                return None
            require_available(c)
            put(c,'input-state:'+ident,state|dict(state='retiring',retiring_command_id=cid))
            item.update(state='retiring',reason=None)
            self._save(c,receipt,cleanup)
            return package

    def evidence(self,cid,index,identity):
        with self.database.transaction(write=True) as c:
            receipt,cleanup,item=self._load(c,cid,index)
            item['identity']=identity
            self._save(c,receipt,cleanup)

    def finish(self,cid,index,removed,complete):
        with self.database.transaction(write=True) as c:
            receipt,cleanup,item=self._load(c,cid,index)
            ident=item['input_id']; state=package_state(c,ident)
            if (not state or state['state']!='retiring' or state['incarnation_id']!=item['incarnation_id']
                    or state['retiring_command_id']!=cid):
                raise CloudError('input_retirement_identity_changed')
            if c.execute('DELETE FROM meta WHERE key=?',('input:'+ident,)).rowcount!=1:
                raise CloudError('input_retirement_identity_changed')
            put(c,'input-state:'+ident,state|dict(state='deleted'))
            item.update(state='reclaimed',reason=None)
            cleanup['released_quota_bytes']+=item['accounted_bytes']
            cleanup['verified_deleted_bytes']+=removed
            cleanup['bytes_complete']=cleanup['bytes_complete'] and complete
            return self._save(c,receipt,cleanup)
