"""Project-owned local receipts/materials; publication joins one SQLite transaction."""
import json,time
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_receive import ReceiveRecord,MaterialRef
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .queries import session_row

REQUIRED={'naiyue_cloud_receives':'receive_id session_id project_id request_digest body_json request_json',
    'naiyue_cloud_materials':'material_id receive_id session_id project_id body_json identity_json'}


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]>=22:return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE IF NOT EXISTS naiyue_cloud_receives(receive_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,project_id TEXT NOT NULL,request_digest TEXT NOT NULL,body_json TEXT NOT NULL,request_json TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES naiyue_sessions(session_id) ON DELETE CASCADE)''')
        connection.execute('''CREATE TABLE IF NOT EXISTS naiyue_cloud_materials(material_id TEXT PRIMARY KEY,
            receive_id TEXT NOT NULL,session_id TEXT NOT NULL,project_id TEXT NOT NULL,body_json TEXT NOT NULL,
            identity_json TEXT NOT NULL,FOREIGN KEY(receive_id) REFERENCES naiyue_cloud_receives(receive_id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES naiyue_sessions(session_id) ON DELETE CASCADE)''')
        connection.execute('CREATE INDEX IF NOT EXISTS naiyue_cloud_material_parent ON naiyue_cloud_materials(session_id)')
        connection.execute('UPDATE naiyue_store_info SET version=22')


def purge(connection,sid):
    connection.execute('DELETE FROM naiyue_cloud_materials WHERE session_id=?',(sid,))
    connection.execute('DELETE FROM naiyue_cloud_receives WHERE session_id=?',(sid,))


class ReceiveRepository:
    def __init__(self,database):self.database=database

    def _read(self,c,rid,sid):
        row=c.execute('SELECT session_id,body_json FROM naiyue_cloud_receives WHERE receive_id=?',(rid,)).fetchone()
        if row is None or row[0]!=sid:raise CloudError('cloud_receive_not_found',404)
        return ReceiveRecord.model_validate_json(row[1])

    def read(self,rid,sid):
        with self.database.transaction(write=False,error_code='cloud_receive_storage_failed') as c:return self._read(c,rid,sid)

    def reserve(self,binding,result_id,request,limits):
        sid=request['local_session_id'];rid=request['command_id'];project=binding['local_project_id']
        fingerprint=digest(dict(binding_id=binding['binding_id'],result_id=result_id,request=request))
        with self.database.transaction(write=True,error_code='cloud_receive_storage_failed') as c:
            if session_row(c,sid)['project_id']!=project:raise CloudError('cloud_project_mismatch',403)
            old=c.execute('SELECT request_digest,session_id FROM naiyue_cloud_receives WHERE receive_id=?',(rid,)).fetchone()
            if old:
                if old[0]!=fingerprint or old[1]!=sid:raise CloudError('cloud_receive_conflict')
                return self._read(c,rid,sid)
            if c.execute('SELECT COUNT(*) FROM naiyue_cloud_receives').fetchone()[0]>=limits.max_results:raise CloudError('cloud_receive_capacity',429)
            if not binding['task_id'] or not binding['retained']:raise CloudError('cloud_task_unavailable')
            from .cloud_local import _read
            operation=_read(c,'naiyue_cloud_operations',binding['create_command_id'],sid,'cloud_receive_identity_mismatch')
            if operation['binding_id']!=binding['binding_id'] or operation['kind']!='create':raise CloudError('cloud_receive_identity_mismatch',403)
            source_handle=operation['request'].get('source_handle')
            if not source_handle:raise CloudError('cloud_receive_identity_mismatch',409)
            source=_read(c,'naiyue_cloud_sources',source_handle,sid,'cloud_receive_identity_mismatch')
            if (source['local_project_id']!=project or source['local_session_id']!=sid
                    or source['captured']['input_id']!=binding['input_id']):raise CloudError('cloud_receive_input_mismatch',409)
            value=ReceiveRecord(receive_id=rid,command_id=rid,local_project_id=project,local_session_id=sid,
                binding_id=binding['binding_id'],source_handle=source_handle,instance_id=binding['instance_id'],task_id=binding['task_id'],
                result_id=result_id,input_id=binding['input_id'],phase='reserved')
            c.execute('INSERT INTO naiyue_cloud_receives VALUES(?,?,?,?,?,?)',(rid,sid,project,fingerprint,value.model_dump_json(),json.dumps(request)))
            return value

    def request(self,rid,sid):
        with self.database.transaction(write=False,error_code='cloud_receive_storage_failed') as c:
            self._read(c,rid,sid)
            return json.loads(c.execute('SELECT request_json FROM naiyue_cloud_receives WHERE receive_id=?',(rid,)).fetchone()[0])

    def save(self,value):
        value=value.model_copy(update={'active':False,'updated_at':time.time()})
        with self.database.transaction(write=True,error_code='cloud_receive_storage_failed') as c:
            if c.execute('UPDATE naiyue_cloud_receives SET body_json=? WHERE receive_id=? AND session_id=?',
                    (value.model_dump_json(),value.receive_id,value.local_session_id)).rowcount!=1:raise CloudError('cloud_receive_not_found',404)
        return value

    def assign(self,value,limits,used_bytes,additional_bytes):
        with self.database.transaction(write=True,error_code='cloud_receive_storage_failed') as c:
            current=self._read(c,value.receive_id,value.local_session_id)
            if current.result is not None:return current
            used=0
            for row in c.execute('SELECT body_json FROM naiyue_cloud_receives WHERE receive_id!=?',(value.receive_id,)):
                used+=sum(f.artifact.size for f in ReceiveRecord.model_validate_json(row[0]).files if not f.verified)
            if used_bytes+used+additional_bytes>limits.max_cache_bytes:raise CloudError('cloud_receive_capacity',429)
            c.execute('UPDATE naiyue_cloud_receives SET body_json=? WHERE receive_id=?',(value.model_dump_json(),value.receive_id))
            return value

    def publish(self,value,materials):
        ready=value.model_copy(update={'phase':'available','active':False,'error_code':None,'updated_at':time.time()})
        with self.database.transaction(write=True,error_code='cloud_receive_storage_failed') as c:
            self._read(c,value.receive_id,value.local_session_id)
            for ref,identity in materials:
                old=c.execute('SELECT body_json,identity_json FROM naiyue_cloud_materials WHERE material_id=?',(ref.material_id,)).fetchone()
                encoded=json.dumps(identity)
                if old and (MaterialRef.model_validate_json(old[0])!=ref or json.loads(old[1])!=identity):raise CloudError('cloud_material_identity_mismatch',403)
                if not old:c.execute('INSERT INTO naiyue_cloud_materials VALUES(?,?,?,?,?,?)',
                    (ref.material_id,ready.receive_id,ready.local_session_id,ready.local_project_id,ref.model_dump_json(),encoded))
            c.execute('UPDATE naiyue_cloud_receives SET body_json=? WHERE receive_id=?',(ready.model_dump_json(),ready.receive_id))
        return ready

    def material(self,mid,sid,project):
        with self.database.transaction(write=False,error_code='cloud_material_unavailable') as c:
            row=c.execute('SELECT session_id,project_id,body_json,identity_json FROM naiyue_cloud_materials WHERE material_id=?',(mid,)).fetchone()
            if row is None:raise CloudError('cloud_material_not_found',404)
            if row[0]!=sid or row[1]!=project:raise CloudError('cloud_material_identity_mismatch',403)
            return MaterialRef.model_validate_json(row[2]),json.loads(row[3])

    def has_materials(self,sid,project):
        with self.database.transaction(write=False,error_code='cloud_material_unavailable') as c:
            return c.execute('SELECT 1 FROM naiyue_cloud_materials WHERE session_id=? AND project_id=? LIMIT 1',(sid,project)).fetchone() is not None

    def materials(self,rid,sid,project):
        with self.database.transaction(write=False,error_code='cloud_material_unavailable') as c:
            self._read(c,rid,sid)
            return [MaterialRef.model_validate_json(r[0]) for r in c.execute(
                'SELECT body_json FROM naiyue_cloud_materials WHERE receive_id=? AND session_id=? AND project_id=? ORDER BY rowid',(rid,sid,project))]
