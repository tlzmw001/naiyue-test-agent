"""Coordinator-owned short result leases; history receipts remain immutable in identity."""
import json
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt


class ResultRetentionRepository:
    def __init__(self,database):self.database=database

    @staticmethod
    def prefix(task_id,result_id):return 'result-lease:'+task_id+':'+result_id+':'

    def live(self,task_id,result_id,now):
        with self.database.transaction(write=True) as db:
            values=[]
            for row in db.execute('SELECT key,value FROM meta WHERE key LIKE ?',(self.prefix(task_id,result_id)+'%',)).fetchall():
                value=json.loads(row[1])
                if value['expires_at']<=now:db.execute('DELETE FROM meta WHERE key=?',(row[0],))
                else:values.append(value)
            return values

    def lease(self,task_id,result_id,lease_id,now,limits):
        self.live(task_id,result_id,now)
        key=self.prefix(task_id,result_id)+lease_id
        value=dict(lease_id=lease_id,result_id=result_id,expires_at=now+limits.result_lease_seconds)
        with self.database.transaction(write=True) as db:
            if not db.execute('SELECT 1 FROM meta WHERE key=?',(key,)).fetchone():
                count=0
                for row in db.execute('SELECT value FROM meta WHERE key LIKE ?',('result-lease:%',)):
                    if json.loads(row[0])['expires_at']>now:count+=1
                if count>=limits.max_result_leases:raise CloudError('result_lease_capacity',429)
            db.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key,json.dumps(value)))
        return value

    def release(self,task_id,result_id,lease_id):
        with self.database.transaction(write=True) as db:
            db.execute('DELETE FROM meta WHERE key=?',(self.prefix(task_id,result_id)+lease_id,))

    def candidates(self,task_id):
        with self.database.transaction() as db:
            return [CommandReceipt.model_validate_json(row[0]) for row in db.execute('''SELECT record_json FROM commands
                WHERE resource_id=? AND json_extract(record_json,'$.operation')=?
                AND json_extract(record_json,'$.result.phase') IN ('available','abandoned','deleting')
                ORDER BY json_extract(record_json,'$.created_at') DESC,command_id DESC''',(task_id,'result:capture'))]
