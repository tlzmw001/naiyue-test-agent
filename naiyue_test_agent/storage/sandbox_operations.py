"""Controller-side operation facts share the short registry transaction owner."""
from datetime import datetime,timezone
import json
from naiyue_test_agent.contracts.sandbox import SandboxError,require_operation_id,operation_fingerprint


class SandboxOperations:
    def __init__(self,database):self.database=database

    def reserve(self,binding_id,project_id,session_id,ident,kind,payload,metadata):
        require_operation_id(ident)
        fingerprint=operation_fingerprint(kind,payload)
        with self.database.transaction(write=True) as connection:
            existing=connection.execute('SELECT binding_id,record_json FROM sandbox_operations WHERE operation_id=?',(ident,)).fetchone()
            if existing is not None:
                row=json.loads(existing['record_json'])
                if existing['binding_id']!=binding_id or row['fingerprint']!=fingerprint:
                    raise SandboxError('env_operation_conflict')
                return row,False
            row=dict(operation_id=ident,binding_id=binding_id,project_id=project_id,session_id=session_id,
                kind=kind,fingerprint=fingerprint,state='submitting',result=None,metadata=metadata,
                created_at=datetime.now(timezone.utc).isoformat(),observed_bytes=0,stale=False)
            connection.execute('INSERT INTO sandbox_operations VALUES(?,?,?,?,?)',
                (ident,binding_id,project_id,session_id,json.dumps(row)))
            return row,True

    def get(self,binding_id,ident):
        require_operation_id(ident)
        rows=self.database.read_rows('SELECT record_json FROM sandbox_operations WHERE binding_id=? AND operation_id=?',(binding_id,ident))
        if not rows:raise SandboxError('env_operation_not_found')
        return json.loads(rows[0]['record_json'])

    def list(self,binding_id=None):
        query="""SELECT CASE WHEN json_extract(record_json,'$.kind') IN ('write','edit')
            THEN json_remove(record_json,'$.metadata.tail_base64','$.result')
            ELSE json_remove(record_json,'$.metadata.tail_base64') END AS record_json FROM sandbox_operations"""
        rows=self.database.read_rows(query+' ORDER BY rowid' if binding_id is None else query+' WHERE binding_id=? ORDER BY rowid',
            () if binding_id is None else (binding_id,))
        return [json.loads(row['record_json']) for row in rows]

    def save(self,binding_id,ident,changes):
        with self.database.transaction(write=True) as connection:
            stored=connection.execute('SELECT record_json FROM sandbox_operations WHERE binding_id=? AND operation_id=?',(binding_id,ident)).fetchone()
            if stored is None:raise SandboxError('env_operation_not_found')
            row=json.loads(stored['record_json'])
            row.update(changes)
            connection.execute('UPDATE sandbox_operations SET record_json=? WHERE binding_id=? AND operation_id=?',
                (json.dumps(row),binding_id,ident))
            return row

    def result_rows(self,binding_id):
        rows=self.database.read_rows("""SELECT record_json FROM sandbox_operations WHERE binding_id=?
            AND json_extract(record_json,'$.kind')='git_result' ORDER BY rowid DESC LIMIT 32""",(binding_id,))
        return [json.loads(row['record_json']) for row in rows]
