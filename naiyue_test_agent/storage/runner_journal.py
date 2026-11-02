"""Runner-owned command journal/outbox; finish joins the session's required transaction."""
import json

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import CloudEvent, canonical_json, digest
from .runner_history import append_messages


class RunnerJournal:
    def __init__(self,database,bootstrap,*,initialize):
        self.database,self.bootstrap=database,bootstrap
        identity={k:getattr(bootstrap,k) for k in ('task_id','project_id','input_id','instance_id','state_epoch')}
        with database.transaction(write=True) as c:
            if initialize:
                c.execute('CREATE TABLE IF NOT EXISTS nyt_runner_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                c.execute('CREATE TABLE IF NOT EXISTS nyt_runner_commands(command_id TEXT PRIMARY KEY,run_id TEXT UNIQUE NOT NULL,digest TEXT NOT NULL,record_json TEXT NOT NULL)')
                c.execute('CREATE TABLE IF NOT EXISTS nyt_runner_outbox(seq INTEGER PRIMARY KEY,event_json TEXT NOT NULL)')
                c.execute('INSERT OR IGNORE INTO nyt_runner_meta VALUES(?,?)',('identity',canonical_json(identity)))
                c.execute('INSERT OR IGNORE INTO nyt_runner_meta VALUES(?,?)',('acked','0'))
            c.execute('CREATE TABLE IF NOT EXISTS nyt_runner_controls(command_id TEXT PRIMARY KEY,digest TEXT NOT NULL,record_json TEXT NOT NULL)')
            c.execute('INSERT OR IGNORE INTO nyt_runner_meta VALUES(?,?)',('activity',canonical_json(dict(dirty=False,generation=bootstrap.generation))))
            for row in c.execute('SELECT command_id,record_json FROM nyt_runner_controls').fetchall():
                value=json.loads(row['record_json'])
                if value['status']=='running':
                    value['status']='unknown'
                    c.execute('UPDATE nyt_runner_controls SET record_json=? WHERE command_id=?',(canonical_json(value),row['command_id']))
            stored=c.execute('SELECT value FROM nyt_runner_meta WHERE key=?',('identity',)).fetchone()
            if stored is None or json.loads(stored[0])!=identity:raise CloudError('runner_state_identity_mismatch')
            # Interrupted execution has unknown side effects. Opening never replays it.
            for row in c.execute('SELECT command_id,record_json FROM nyt_runner_commands').fetchall():
                value=json.loads(row['record_json'])
                if value['status']=='running':
                    value['status']='unknown'
                    c.execute('UPDATE nyt_runner_commands SET record_json=? WHERE command_id=?',(canonical_json(value),row['command_id']))

    def lookup(self,command_id):
        with self.database.transaction() as c:
            row=c.execute('SELECT record_json FROM nyt_runner_commands WHERE command_id=?',(command_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def has_unsettled(self):
        with self.database.transaction() as c:
            return any(json.loads(r[0])['status'] in ('running','unknown','interrupted')
                for r in c.execute('SELECT record_json FROM nyt_runner_commands')) or any(
                json.loads(r[0])['status'] in ('running','unknown') for r in c.execute('SELECT record_json FROM nyt_runner_controls'))

    def admit(self,command):
        fingerprint=digest(command.model_dump())
        with self.database.transaction(write=True) as c:
            row=c.execute('SELECT digest,record_json FROM nyt_runner_commands WHERE command_id=?',(command.command_id,)).fetchone()
            if row:
                if row['digest']!=fingerprint:raise CloudError('command_id_conflict')
                return json.loads(row['record_json']),False
            for row in c.execute('SELECT record_json FROM nyt_runner_commands'):
                if json.loads(row[0])['status'] in ('running','unknown','interrupted'):raise CloudError('runner_busy')
            if c.execute('SELECT 1 FROM nyt_runner_controls WHERE command_id=?',(command.command_id,)).fetchone():raise CloudError('command_id_conflict')
            self._activity(c,True)
            value=dict(command_id=command.command_id,run_id=command.run_id,status='running',terminal_seq=None)
            c.execute('INSERT INTO nyt_runner_commands VALUES(?,?,?,?)',(command.command_id,command.run_id,fingerprint,canonical_json(value)))
            return value,True

    def unknown(self,command_id):
        with self.database.transaction(write=True) as c:
            row=c.execute('SELECT record_json FROM nyt_runner_commands WHERE command_id=?',(command_id,)).fetchone()
            value=json.loads(row[0])
            if value['status']=='running':
                value['status']='unknown'
                c.execute('UPDATE nyt_runner_commands SET record_json=? WHERE command_id=?',(canonical_json(value),command_id))

    def latest(self):
        with self.database.transaction() as c:
            row=c.execute('SELECT record_json FROM nyt_runner_commands ORDER BY rowid DESC LIMIT 1').fetchone()
            return json.loads(row[0]) if row else None

    def activity(self):
        with self.database.transaction() as c:
            return json.loads(c.execute('SELECT value FROM nyt_runner_meta WHERE key=?',('activity',)).fetchone()[0])

    def _activity(self,c,dirty):
        c.execute('UPDATE nyt_runner_meta SET value=? WHERE key=?',
            (canonical_json(dict(dirty=dirty,generation=self.bootstrap.generation)),'activity'))

    def mark_activity(self,dirty):
        with self.database.transaction(write=True) as c:self._activity(c,dirty)

    def control_lookup(self,command_id):
        with self.database.transaction() as c:
            row=c.execute('SELECT record_json FROM nyt_runner_controls WHERE command_id=?',(command_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def admit_control(self,command):
        fingerprint=digest(command.model_dump())
        with self.database.transaction(write=True) as c:
            row=c.execute('SELECT digest,record_json FROM nyt_runner_controls WHERE command_id=?',(command.command_id,)).fetchone()
            if row:
                if row['digest']!=fingerprint:raise CloudError('command_id_conflict')
                return json.loads(row['record_json']),False
            if c.execute('SELECT 1 FROM nyt_runner_commands WHERE command_id=?',(command.command_id,)).fetchone():raise CloudError('command_id_conflict')
            for row in c.execute('SELECT command_id,record_json FROM nyt_runner_controls').fetchall():
                previous=json.loads(row['record_json'])
                if previous['status'] not in ('running','unknown'):continue
                if command.kind!='recover' or previous.get('generation',self.bootstrap.generation)>=self.bootstrap.generation:
                    raise CloudError('runner_control_busy')
                previous.update(status='failed',error_code='control_environment_stopped')
                c.execute('UPDATE nyt_runner_controls SET record_json=? WHERE command_id=?',(canonical_json(previous),row['command_id']))
            value=dict(command_id=command.command_id,command=command.model_dump(),generation=self.bootstrap.generation,status='running',terminal_seq=None)
            c.execute('INSERT INTO nyt_runner_controls VALUES(?,?,?)',(command.command_id,fingerprint,canonical_json(value)))
            return value,True

    def unknown_control(self,command_id):
        with self.database.transaction(write=True) as c:
            row=c.execute('SELECT record_json FROM nyt_runner_controls WHERE command_id=?',(command_id,)).fetchone()
            value=json.loads(row[0])
            if value['status']=='running':
                value['status']='unknown'
                c.execute('UPDATE nyt_runner_controls SET record_json=? WHERE command_id=?',(canonical_json(value),command_id))

    def _append(self,c,run_id,payload):
        seq=c.execute('SELECT COALESCE(MAX(seq),0) FROM nyt_runner_outbox').fetchone()[0]+1
        event=CloudEvent(seq=seq,source_generation=self.bootstrap.generation,run_id=run_id,kind='history_record',payload=payload)
        raw=event.model_dump_json()
        if len(raw.encode())+1024>self.bootstrap.max_frame_bytes:raise CloudError('history_event_too_large',413)
        c.execute('INSERT INTO nyt_runner_outbox VALUES(?,?)',(seq,raw))
        return seq

    def _messages(self,c,session_id,run_id,command_id,updates):
        append_messages(self,c,session_id,run_id,command_id,updates)

    def _finish_control(self,c,command_id,*,phase,quiescent,status='completed',error_code=None,recovery=None):
        row=c.execute('SELECT record_json FROM nyt_runner_controls WHERE command_id=?',(command_id,)).fetchone()
        value=json.loads(row[0])
        result=dict(command_id=command_id,status=status,phase=phase,quiescent=quiescent,error_code=error_code,recovery=recovery)
        seq=self._append(c,value['command']['run_id'] or command_id.replace('-',''),dict(record='control_finished',**result))
        value.update(result,terminal_seq=seq)
        c.execute('UPDATE nyt_runner_controls SET record_json=? WHERE command_id=?',(canonical_json(value),command_id))
        return value

    def finish_control(self,command_id,**result):
        with self.database.transaction(write=True) as c:return self._finish_control(c,command_id,**result)

    def recover_ready(self,command_id):
        with self.database.transaction(write=True) as c:
            row=json.loads(c.execute('SELECT record_json FROM nyt_runner_controls WHERE command_id=?',(command_id,)).fetchone()[0])
            target=row['command']['target_command_id']
            original=json.loads(c.execute('SELECT record_json FROM nyt_runner_commands WHERE command_id=?',(target,)).fetchone()[0])
            dirty=json.loads(c.execute('SELECT value FROM nyt_runner_meta WHERE key=?',('activity',)).fetchone()[0])['dirty']
            if original['status'] in ('completed','stopped') and dirty:
                self._append(c,original['run_id'],dict(record='background_unconfirmed',command_id=command_id,
                    reason='previous_runner_output_unconfirmed'))
            if original['status'] not in ('completed','stopped'):
                original['status']='recovered'
                c.execute('UPDATE nyt_runner_commands SET record_json=? WHERE command_id=?',(canonical_json(original),target))
            self._activity(c,False)
            return self._finish_control(c,command_id,phase='ready',quiescent=True)

    def finish(self,connection,session_id,run_id,state,status,updates):
        if session_id!=self.bootstrap.task_id:raise CloudError('runner_session_identity_mismatch')
        if status=='recovered':
            controls=[json.loads(r[0]) for r in connection.execute('SELECT record_json FROM nyt_runner_controls')]
            selected=[r for r in controls if r['status'] in ('running','unknown') and r['command']['kind']=='recover']
            if len(selected)!=1:raise CloudError('recovery_command_missing')
            control=selected[0];target=control['command']['target_command_id']
            self._messages(connection,session_id,control['command']['run_id'],target,updates)
            original=json.loads(connection.execute('SELECT record_json FROM nyt_runner_commands WHERE command_id=?',(target,)).fetchone()[0])
            original['status']='recovered'
            connection.execute('UPDATE nyt_runner_commands SET record_json=? WHERE command_id=?',(canonical_json(original),target))
            self._activity(connection,False)
            self._finish_control(connection,control['command_id'],phase='ready',quiescent=True)
            return
        row=connection.execute('SELECT command_id,record_json FROM nyt_runner_commands WHERE run_id=?',(run_id,)).fetchone()
        if row is None:raise CloudError('runner_command_missing')
        outcome=(status if state=='ready' and status in ('completed','stopped') else 'failed' if state=='failed' else 'interrupted')
        self._messages(connection,session_id,run_id,row['command_id'],updates)
        seq=self._append(connection,run_id,dict(record='run_finished',command_id=row['command_id'],status=outcome,session_state=state))
        value=json.loads(row['record_json'])|dict(status=outcome,terminal_seq=seq)
        connection.execute('UPDATE nyt_runner_commands SET record_json=? WHERE command_id=?',(canonical_json(value),row['command_id']))

    def pending(self):
        with self.database.transaction() as c:
            ack=int(c.execute('SELECT value FROM nyt_runner_meta WHERE key=?',('acked',)).fetchone()[0])
            rows=c.execute('SELECT event_json FROM nyt_runner_outbox WHERE seq>? ORDER BY seq LIMIT ?',(ack,self.bootstrap.batch_events))
            events=[];size=1024
            for row in rows:
                size+=len(row[0].encode())+1
                if size>self.bootstrap.max_frame_bytes:break
                events.append(CloudEvent.model_validate_json(row[0]))
            return events

    def ack(self,seq):
        with self.database.transaction(write=True) as c:
            last=c.execute('SELECT COALESCE(MAX(seq),0) FROM nyt_runner_outbox').fetchone()[0]
            old=int(c.execute('SELECT value FROM nyt_runner_meta WHERE key=?',('acked',)).fetchone()[0])
            if type(seq) is not int or not old<=seq<=last:raise CloudError('history_ack_invalid')
            c.execute('UPDATE nyt_runner_meta SET value=? WHERE key=?',(str(seq),'acked'))
