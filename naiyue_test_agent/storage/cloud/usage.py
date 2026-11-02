"""Optional finite invocation facts. Every SQL operation stays in the DB owner."""
import json
from time import monotonic
from datetime import datetime,timezone

from naiyue_test_agent.contracts.cloud_events import canonical_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.observability_sql.model_usage_storage import _usage_bucket,_add_usage,_usage_view
from .history_reads import archive_read

METRICS=('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')


class CloudUsage:
    def __init__(self,database):self.database=database

    def reopen(self,*,read_timeout=5.0):
        with self.database.transaction(write=True) as c:
            deadline=monotonic()+read_timeout
            c.set_progress_handler(lambda:monotonic()>=deadline,100)
            try:
                if c.execute('SELECT 1 FROM history_usage_runs WHERE closed=0 LIMIT 1').fetchone():
                    c.execute("INSERT OR REPLACE INTO meta VALUES('usage_incomplete','1')")
                    c.execute("UPDATE history_usage_runs SET capture_incomplete=1,reason=COALESCE(reason,'coordinator_restart') WHERE closed=0")
                    for row in c.execute('SELECT invocation_id,record_json FROM history_usage WHERE finished=0'):
                        if monotonic()>=deadline:raise CloudError('usage_reopen_timeout',503)
                        value=json.loads(row['record_json']);value['status']='unconfirmed'
                        c.execute('UPDATE history_usage SET record_json=? WHERE invocation_id=?',(canonical_json(value),row['invocation_id']))
            finally:c.set_progress_handler(None,0)

    def save(self,batch):
        with self.database.transaction(write=True) as c:
            cursor=c.execute("SELECT value FROM meta WHERE key='usage_cursor'").fetchone()
            previous=json.loads(cursor[0]) if cursor else None
            end=batch.first_seq+len(batch.records)-1
            if previous and previous['epoch']==batch.epoch and end<=previous['last_seq']:return
            gap=bool(batch.dropped or batch.first_seq!=(previous['last_seq']+1 if previous and previous['epoch']==batch.epoch else 1))
            if gap:c.execute("INSERT OR REPLACE INTO meta VALUES('usage_incomplete','1')")
            total=c.execute("SELECT value FROM meta WHERE key='usage_dropped'").fetchone()
            dropped=min(9223372036854775807,int(total[0] if total else 0)+batch.dropped)
            c.execute("INSERT OR REPLACE INTO meta VALUES('usage_dropped',?)",(str(dropped),))
            for record in batch.records:
                c.execute('INSERT OR IGNORE INTO history_usage_runs VALUES(?,?,?,0,?)',
                    (record.run_id,int(gap),batch.dropped,'observation_gap' if gap else None))
                if gap:c.execute("UPDATE history_usage_runs SET capture_incomplete=1,reason=COALESCE(reason,'observation_gap') WHERE run_id=?",(record.run_id,))
                if record.event_type in ('model_call_started','model_call_finished'):self._invocation(c,record)
                elif record.event_type=='run_finished' and record.source is None:
                    pending=c.execute('SELECT invocation_id,record_json FROM history_usage WHERE run_id=? AND finished=0',(record.run_id,)).fetchall()
                    for row in pending:
                        value=json.loads(row['record_json']);value['status']='unconfirmed'
                        c.execute('UPDATE history_usage SET record_json=? WHERE invocation_id=?',(canonical_json(value),row['invocation_id']))
                    c.execute('''UPDATE history_usage_runs SET closed=1,capture_incomplete=MAX(capture_incomplete,?),
                        reason=COALESCE(reason,?) WHERE run_id=?''',(int(bool(pending)),'unfinished_invocation' if pending else None,record.run_id))
            c.execute("INSERT OR REPLACE INTO meta VALUES('usage_cursor',?)",(canonical_json(dict(epoch=batch.epoch,last_seq=end)),))

    def _invocation(self,c,event):
        data=event.data;source=canonical_json(event.source.model_dump() if event.source else None)
        old=c.execute('SELECT * FROM history_usage WHERE invocation_id=?',(data.invocation_id,)).fetchone()
        if old and (old['run_id'],old['thread_id'],old['source_json'])!=(event.run_id,event.thread_id,source):
            raise CloudError('usage_identity_conflict')
        value=json.loads(old['record_json']) if old else dict(model_id=None,purpose=None,status='started',usage_status='unavailable',
            duration_ms=None,error_type=None,**dict.fromkeys(METRICS))
        started=old['started'] if old else 0;finished=old['finished'] if old else 0
        if event.event_type=='model_call_started':
            if started and (value['model_id'],value['purpose'])!=(data.model_id,data.purpose):raise CloudError('usage_identity_conflict')
            value.update(model_id=data.model_id,purpose=data.purpose);started=1
        else:
            terminal=data.model_dump(exclude={'invocation_id'})
            if finished and any(value[k]!=v for k,v in terminal.items()):raise CloudError('usage_terminal_conflict')
            value.update(terminal);finished=1
            if not started:c.execute("UPDATE history_usage_runs SET capture_incomplete=1,reason=COALESCE(reason,'missing_start') WHERE run_id=?",(event.run_id,))
        c.execute('INSERT OR REPLACE INTO history_usage VALUES(?,?,?,?,?,?,?)',
            (data.invocation_id,event.run_id,event.thread_id,source,canonical_json(value),started,finished))

    def read(self,*,limit,after_model=None,read_timeout):
        with archive_read(self.database,read_timeout) as (c,coverage):
            names=[r[0] for r in c.execute('''SELECT DISTINCT json_extract(record_json,'$.model_id') AS model
                FROM history_usage WHERE model IS NOT NULL AND (? IS NULL OR model>?) ORDER BY model LIMIT ?''',(after_model,after_model,limit+1))]
            models={name:_usage_bucket() for name in names[:limit]};total=_usage_bucket();unknown=_usage_bucket()
            scopes={name:_usage_bucket() for name in ('root','child')};activities={}
            for row in c.execute('SELECT source_json,record_json FROM history_usage'):
                value=json.loads(row['record_json']);scope='child' if json.loads(row['source_json']) else 'root'
                activity=scope+'_agent' if value['purpose']=='agent' else value['purpose'] or 'unattributed'
                bucket=activities.setdefault(activity,_usage_bucket())
                for target in (total,scopes[scope],bucket):_add_usage(target,value)
                if value['model_id'] is None:_add_usage(unknown,value)
                elif value['model_id'] in models:_add_usage(models[value['model_id']],value)
            runs=c.execute('SELECT COUNT(*),COALESCE(SUM(capture_incomplete),0),COALESCE(SUM(closed=0),0) FROM history_usage_runs').fetchone()
            meta=dict(c.execute("SELECT key,value FROM meta WHERE key IN ('usage_incomplete','usage_dropped','usage_cursor')"))
            return dict(as_of=datetime.now(timezone.utc).isoformat(),**_usage_view(total),
                models=dict(items=[dict(model_id=name,**_usage_view(bucket)) for name,bucket in models.items()],
                    next_after=names[limit-1] if len(names)>limit else None,unattributed=_usage_view(unknown)),
                scopes=[dict(id=name,**_usage_view(bucket)) for name,bucket in scopes.items() if bucket['recorded_calls']],
                activities=[dict(id=name,**_usage_view(bucket)) for name,bucket in activities.items()],
                capture=dict(recorded_runs=runs[0],incomplete_runs=runs[1],open_runs=runs[2],capture_incomplete=meta.get('usage_incomplete')=='1' or bool(runs[1]),
                    dropped_events=meta.get('usage_dropped','0'),source=json.loads(meta['usage_cursor']) if 'usage_cursor' in meta else None),
                history_coverage=coverage)
