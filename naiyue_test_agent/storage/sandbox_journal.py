"""Container-owned durable admission and bounded output, independent of a run."""
from contextlib import ExitStack
from datetime import datetime,timezone
import json
import logging
import os
import sqlite3
from threading import RLock

from naiyue_test_agent.contracts.sandbox import SandboxError,require_operation_id,operation_fingerprint
from .files import open_store_file,check_store_directory,acquire_store_lock,release_store_lock

logger=logging.getLogger(__name__)


class SandboxJournal:
    def __init__(self,path,identity,settings):
        self.settings=settings
        self.lock=RLock()
        self._stack=ExitStack()
        try:
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            check_store_directory(path.parent)
            fd=open_store_file(path.with_suffix('.lock'));self._stack.callback(os.close,fd)
            acquire_store_lock(fd);self._stack.callback(release_store_lock,fd)
            fd=open_store_file(path);os.close(fd)
            self.connection=sqlite3.connect(path,check_same_thread=False)
            self._stack.callback(self.connection.close)
            self.connection.row_factory=sqlite3.Row
            self.connection.execute('PRAGMA synchronous=FULL')
            with self.connection:
                tables=self.connection.execute('SELECT name FROM sqlite_master').fetchall()
                if not tables:
                    self.connection.execute('CREATE TABLE identity(value TEXT NOT NULL)')
                    self.connection.execute('INSERT INTO identity VALUES(?)',(json.dumps(identity,sort_keys=True),))
                    self.connection.execute('''CREATE TABLE operations(
                        operation_id TEXT PRIMARY KEY,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,
                        state TEXT NOT NULL,result_json TEXT,observed_bytes INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,metadata_json TEXT NOT NULL)''')
                    self.connection.execute('''CREATE TABLE chunks(
                        operation_id TEXT NOT NULL,offset INTEGER NOT NULL,data BLOB NOT NULL)''')
                    self.connection.execute('CREATE INDEX chunks_operation ON chunks(operation_id,offset)')
                    self.connection.execute('PRAGMA user_version=1')
                if self.connection.execute('PRAGMA user_version').fetchone()[0]!=1:
                    raise SandboxError('env_protocol_mismatch')
                stored=self.connection.execute('SELECT value FROM identity').fetchone()[0]
                if json.loads(stored)!=identity:raise SandboxError('env_binding_mismatch')
                self.connection.execute("UPDATE operations SET state='unknown' WHERE state IN ('starting','running')")
        except BaseException as error:
            logger.error('envd_journal_open_failed error_type=%s',type(error).__name__)
            self._stack.close();raise

    def close(self):self._stack.close()
    def __enter__(self):return self
    def __exit__(self,*_args):self.close()

    @staticmethod
    def _row(row):
        return dict(operation_id=row['operation_id'],kind=row['kind'],fingerprint=row['fingerprint'],
            state=row['state'],result=json.loads(row['result_json']) if row['result_json'] else None,
            observed_bytes=row['observed_bytes'],created_at=row['created_at'],metadata=json.loads(row['metadata_json']))

    def get(self,ident):
        require_operation_id(ident)
        with self.lock:
            row=self.connection.execute('SELECT * FROM operations WHERE operation_id=?',(ident,)).fetchone()
            if row is None:raise SandboxError('env_operation_not_found')
            return self._row(row)

    def unconfirmed_process(self):
        with self.lock:
            return self.connection.execute("SELECT 1 FROM operations WHERE kind IN ('shell','argv') AND state='unknown' LIMIT 1").fetchone() is not None

    def reserve(self,ident,kind,payload,metadata):
        require_operation_id(ident)
        fingerprint=operation_fingerprint(kind,payload)
        with self.lock,self.connection:
            existing=self.connection.execute('SELECT * FROM operations WHERE operation_id=?',(ident,)).fetchone()
            if existing is not None:
                if existing['fingerprint']!=fingerprint:raise SandboxError('env_operation_conflict')
                return self._row(existing),False
            total=self.connection.execute('SELECT count(*) FROM operations').fetchone()[0]
            active=self.connection.execute("SELECT count(*) FROM operations WHERE state IN ('starting','running')").fetchone()[0]
            if total>=self.settings.max_operation_records:raise SandboxError('env_journal_full')
            if active>=self.settings.max_active_operations:raise SandboxError('env_operation_capacity')
            self.connection.execute('''INSERT INTO operations(operation_id,kind,fingerprint,state,created_at,metadata_json)
                VALUES(?,?,?,'starting',?,?)''',(ident,kind,fingerprint,datetime.now(timezone.utc).isoformat(),json.dumps(metadata)))
        return self.get(ident),True

    def finish(self,ident,state,result=None):
        raw=json.dumps(result,ensure_ascii=True,separators=(',',':')) if result is not None else None
        if raw is not None and len(raw.encode())>self.settings.max_message_bytes//2:
            state,raw='unknown',json.dumps({'ok':False,'error':{'code':'env_operation_unknown'}})
        with self.lock,self.connection:
            self.connection.execute('UPDATE operations SET state=?,result_json=? WHERE operation_id=?',(state,raw,ident))

    def append(self,ident,data):
        with self.lock,self.connection:
            offset=self.get(ident)['observed_bytes']
            retained=min(self.settings.process_output_bytes,self.settings.total_output_bytes)
            tail=data[-retained:]
            self.connection.execute('INSERT INTO chunks VALUES(?,?,?)',(ident,offset+len(data)-len(tail),tail))
            self.connection.execute('UPDATE operations SET observed_bytes=? WHERE operation_id=?',(offset+len(data),ident))
            while self.connection.execute('SELECT coalesce(sum(length(data)),0) FROM chunks WHERE operation_id=?',(ident,)).fetchone()[0]>self.settings.process_output_bytes:
                self.connection.execute('DELETE FROM chunks WHERE rowid=(SELECT min(rowid) FROM chunks WHERE operation_id=?)',(ident,))
            while self.retained_bytes()>self.settings.total_output_bytes:
                self.connection.execute('DELETE FROM chunks WHERE rowid=(SELECT min(rowid) FROM chunks)')

    def retained_bytes(self):
        with self.lock:return self.connection.execute('SELECT coalesce(sum(length(data)),0) FROM chunks').fetchone()[0]

    def output(self,ident,offset,limit):
        if type(offset) is not int or offset<0 or type(limit) is not int or limit<=0:
            raise SandboxError('env_protocol_mismatch')
        with self.lock:
            row=self.get(ident)
            first=self.connection.execute('SELECT min(offset) FROM chunks WHERE operation_id=?',(ident,)).fetchone()[0]
            first=row['observed_bytes'] if first is None else first
            start=max(offset,first);data=bytearray()
            for chunk in self.connection.execute('SELECT offset,data FROM chunks WHERE operation_id=? AND offset+length(data)>? ORDER BY offset',(ident,start)):
                skip=max(0,start-chunk['offset'])
                data.extend(chunk['data'][skip:skip+limit-len(data)])
                if len(data)>=limit:break
            return dict(offset=start,next_offset=start+len(data),observed_bytes=row['observed_bytes'],
                        first_offset=first,gap=start>offset,data=bytes(data))
