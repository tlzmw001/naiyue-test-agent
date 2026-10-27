"""Host-only Git intent journal; no envd/process custody or long transactions."""
from datetime import datetime,timezone
import json
from naiyue_test_agent.contracts.git_delivery import GitDeliveryError


class GitDeliveries:
    def __init__(self,database):self.database=database

    def reserve(self,project,binding,result,command,settings):
        payload=command.model_dump(mode='json')
        with self.database.transaction(write=True) as conn:
            found=conn.execute('SELECT record_json FROM git_deliveries WHERE operation_id=?',(command.operation_id,)).fetchone()
            if found is not None:
                row=json.loads(found['record_json'])
                if row['project_id']!=project or row['binding_id']!=binding or row['result_id']!=result or row['command']!=payload:
                    raise GitDeliveryError('git_delivery_conflict')
                return row,False
            if conn.execute("SELECT 1 FROM git_deliveries WHERE project_id=? AND state IN ('preparing','executing','unknown') LIMIT 1",(project,)).fetchone():
                raise GitDeliveryError('git_delivery_pending')
            if conn.execute('SELECT count(*) FROM git_deliveries WHERE project_id=?',(project,)).fetchone()[0]>=settings.max_operations_per_project:
                raise GitDeliveryError('git_delivery_capacity')
            row=dict(operation_id=command.operation_id,project_id=project,binding_id=binding,result_id=result,
                command=payload,state='preparing',plan=None,result=None,error_code=None,created_at=datetime.now(timezone.utc).isoformat())
            conn.execute('INSERT INTO git_deliveries VALUES(?,?,?,?,?,?)',
                (command.operation_id,project,binding,result,row['state'],json.dumps(row)))
        return row,True

    def get(self,project,binding,ident):
        rows=self.database.read_rows('SELECT record_json FROM git_deliveries WHERE project_id=? AND binding_id=? AND operation_id=?',
            (project,binding,ident))
        if not rows:raise GitDeliveryError('git_delivery_not_found')
        return json.loads(rows[0]['record_json'])

    @staticmethod
    def _read(conn,project,binding,ident):
        row=conn.execute('SELECT record_json FROM git_deliveries WHERE project_id=? AND binding_id=? AND operation_id=?',
            (project,binding,ident)).fetchone()
        if row is None:raise GitDeliveryError('git_delivery_not_found')
        return json.loads(row['record_json'])

    @staticmethod
    def _update(conn,row,changes):
        row.update(changes)
        conn.execute('UPDATE git_deliveries SET state=?,record_json=? WHERE project_id=? AND binding_id=? AND operation_id=?',
            (row['state'],json.dumps(row),row['project_id'],row['binding_id'],row['operation_id']))
        return row

    def list(self,project,binding,result):
        rows=self.database.read_rows('SELECT record_json FROM git_deliveries WHERE project_id=? AND binding_id=? AND result_id=? ORDER BY rowid DESC LIMIT 32',
            (project,binding,result))
        return [json.loads(row['record_json']) for row in rows]

    def save(self,project,binding,ident,changes):
        with self.database.transaction(write=True) as conn:
            row=self._update(conn,self._read(conn,project,binding,ident),changes)
        return row

    def complete(self,project,binding,ident,result):
        with self.database.transaction(write=True) as conn:
            row=self._update(conn,self._read(conn,project,binding,ident),dict(state='completed',result=result,error_code=None))
            if row['command']['action']=='discard':
                changed=conn.execute("""UPDATE sandbox_operations SET record_json=json_set(record_json,
                    '$.metadata.delivery.discarded',json('true')) WHERE project_id=? AND binding_id=? AND operation_id=?""",
                    (project,binding,row['result_id']))
                if changed.rowcount!=1:raise GitDeliveryError('git_delivery_result_missing')
        return row

    def acknowledge(self,project,binding,ident):
        with self.database.transaction(write=True) as conn:
            row=self._read(conn,project,binding,ident)
            if row['state'] not in ('unknown','executing','preparing'):raise GitDeliveryError('git_delivery_not_unknown')
            return self._update(conn,row,dict(state='acknowledged',error_code='git_delivery_manually_acknowledged'))
