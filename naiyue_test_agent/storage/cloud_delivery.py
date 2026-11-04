"""Durable local delivery intent and private continuation metadata."""
import json
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord,DeliveryRequest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .queries import session_row
from .errors import SessionPersistenceError

REQUIRED={'naiyue_cloud_deliveries':'command_id session_id project_id request_digest body_json metadata_json'}


def initialize(connection):
    if connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]>=23:return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''CREATE TABLE IF NOT EXISTS naiyue_cloud_deliveries(command_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,project_id TEXT NOT NULL,request_digest TEXT NOT NULL,body_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,FOREIGN KEY(session_id) REFERENCES naiyue_sessions(session_id) ON DELETE CASCADE)''')
        connection.execute('UPDATE naiyue_store_info SET version=23')


def purge(connection,sid):connection.execute('DELETE FROM naiyue_cloud_deliveries WHERE session_id=?',(sid,))


def check_deletion(connection,sid=None):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='naiyue_cloud_deliveries'").fetchone():return
    if connection.execute("""SELECT 1 FROM naiyue_cloud_deliveries
            WHERE json_extract(body_json,'$.phase') IN ('preparing','executing')
            AND (? IS NULL OR session_id=?) LIMIT 1""",(sid,sid)).fetchone():
        raise SessionPersistenceError('session_cloud_delivery_active')


class DeliveryRepository:
    def __init__(self,database):self.database=database

    def _read(self,c,cid,sid):
        row=c.execute('SELECT session_id,body_json,metadata_json FROM naiyue_cloud_deliveries WHERE command_id=?',(cid,)).fetchone()
        if row is None or row[0]!=sid:raise CloudError('cloud_delivery_not_found',404)
        return DeliveryRecord.model_validate_json(row[1]),json.loads(row[2])

    def read(self,cid,sid):
        with self.database.transaction(write=False,error_code='cloud_delivery_storage_failed') as c:return self._read(c,cid,sid)

    def reserve(self,request,record,metadata,maximum):
        fingerprint=digest(request.model_dump(mode='json'))
        with self.database.transaction(write=True,error_code='cloud_delivery_storage_failed') as c:
            if session_row(c,record.local_session_id)['project_id']!=record.local_project_id:raise CloudError('cloud_project_mismatch',403)
            old=c.execute('SELECT request_digest,session_id FROM naiyue_cloud_deliveries WHERE command_id=?',(record.command_id,)).fetchone()
            if old:
                current,meta=self._read(c,record.command_id,record.local_session_id)
                same=DeliveryRequest.model_validate(meta['request'])==request
                if old[1]!=record.local_session_id or old[0]!=fingerprint and not same:raise CloudError('cloud_delivery_request_conflict')
                return current
            if c.execute('SELECT COUNT(*) FROM naiyue_cloud_deliveries').fetchone()[0]>=maximum:raise CloudError('cloud_delivery_capacity',429)
            metadata=metadata|dict(request=request.model_dump(mode='json'))
            c.execute('INSERT INTO naiyue_cloud_deliveries VALUES(?,?,?,?,?,?)',(record.command_id,record.local_session_id,
                record.local_project_id,fingerprint,record.model_dump_json(),json.dumps(metadata)))
            return record

    def save(self,record,metadata=None):
        with self.database.transaction(write=True,error_code='cloud_delivery_storage_failed') as c:
            self._read(c,record.command_id,record.local_session_id)
            c.execute('UPDATE naiyue_cloud_deliveries SET body_json=? WHERE command_id=?',(record.model_dump_json(),record.command_id))
            if metadata is not None:c.execute('UPDATE naiyue_cloud_deliveries SET metadata_json=? WHERE command_id=?',
                (json.dumps(metadata),record.command_id))
        return record

    def _baseline(self,c,key):
        row=c.execute("""SELECT command_id,metadata_json FROM naiyue_cloud_deliveries
            WHERE json_extract(body_json,'$.phase')='completed' AND json_extract(body_json,'$.action')='apply'
            AND json_extract(metadata_json,'$.baseline_key')=?
            ORDER BY CAST(json_extract(metadata_json,'$.applied_order') AS INTEGER) DESC LIMIT 1""",(key,)).fetchone()
        return (row[0],json.loads(row[1])) if row else (None,{})

    def baseline(self,key):
        with self.database.transaction(write=False,error_code='cloud_delivery_storage_failed') as c:return self._baseline(c,key)

    def complete(self,record,metadata):
        # Receipt and the next baseline become visible together, or neither does.
        with self.database.transaction(write=True,error_code='cloud_delivery_storage_failed') as c:
            current,_=self._read(c,record.command_id,record.local_session_id)
            if current.phase=='completed':return current
            if record.action=='apply':
                latest,_=self._baseline(c,metadata['baseline_key'])
                if latest!=metadata.get('base_command_id'):raise CloudError('cloud_delivery_baseline_changed')
                order=c.execute("SELECT COALESCE(MAX(CAST(json_extract(metadata_json,'$.applied_order') AS INTEGER)),0)+1 FROM naiyue_cloud_deliveries").fetchone()[0]
                metadata=metadata|dict(applied_order=order)
            c.execute('UPDATE naiyue_cloud_deliveries SET body_json=?,metadata_json=? WHERE command_id=?',
                (record.model_dump_json(),json.dumps(metadata),record.command_id))
        return record
