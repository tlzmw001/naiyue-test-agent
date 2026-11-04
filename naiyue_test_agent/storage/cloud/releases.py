"""Release and maintenance facts share the owned registry's short transactions."""
import json
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt


def meta(db,key,default=None):
    row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db,key,value):
    db.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key,json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)))


def require_available(db):
    if meta(db,'maintenance',False):raise CloudError('cloud_maintenance',503)


def active_template(db,template_id):
    active=meta(db,'release-active:'+template_id)
    if active is None:return None
    record=meta(db,'release:'+active['release_id']+':'+template_id)
    if record is None:raise CloudError('release_registry_invalid',503)
    return record['template']


class ReleaseRepository:
    def __init__(self,database,max_receipts):
        self.database,self.max_receipts=database,max_receipts

    def read(self,key,default=None):
        with self.database.transaction() as db:return meta(db,key,default)

    def active(self,template_id):
        with self.database.transaction() as db:
            value=meta(db,'release-active:'+template_id,dict(revision=0,release_id=None))
            revision=meta(db,'release-inventory-revision',0)
            if type(revision) is not int or revision<0:raise CloudError('release_registry_invalid')
            return value|dict(inventory_revision=revision)

    def list(self):
        with self.database.transaction() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT value FROM meta WHERE key LIKE 'release:%' ORDER BY key")]

    def active_records(self):
        with self.database.transaction() as db:
            records=[]
            for row in db.execute("SELECT value FROM meta WHERE key LIKE 'release-active:%'"):
                active=json.loads(row[0])
                record=meta(db,'release:'+active['release_id']+':'+active['template_id'])
                if record is None:raise CloudError('release_registry_invalid',503)
                records.append(record)
            return records

    def transact(self,command_id,fingerprint,action):
        with self.database.transaction(write=True) as db:
            row=db.execute('SELECT record_json FROM commands WHERE command_id=?',(command_id,)).fetchone()
            if row:
                old=CommandReceipt.model_validate_json(row[0])
                if old.request_digest!=fingerprint:raise CloudError('command_id_conflict')
                return old.result
            if db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]>=self.max_receipts:raise CloudError('command_capacity')
            receipt=action(db)
            db.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                (command_id,fingerprint,receipt.resource_id,receipt.model_dump_json()))
            return receipt.result


    def retire_process_io(self):
        # Only called while CloudOwner exclusively owns startup, before any IO.
        with self.database.transaction(write=True) as db:
            db.execute("DELETE FROM meta WHERE key LIKE 'maintenance-lease:%'")

    def begin_io(self,continuation=None):
        from uuid import uuid4
        from .maintenance import continuing_cleanup
        ident=str(uuid4())
        with self.database.transaction(write=True) as db:
            allowed=continuation is not None and continuing_cleanup(db,continuation)
            if not allowed:require_available(db)
            put(db,'maintenance-lease:'+ident,dict(id=ident))
        return ident

    def end_io(self,ident):
        with self.database.transaction(write=True) as db:
            db.execute('DELETE FROM meta WHERE key=?',('maintenance-lease:'+ident,))
