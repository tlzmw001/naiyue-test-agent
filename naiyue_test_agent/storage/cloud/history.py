"""One trusted history journal; event and confirmed cursor share a transaction."""
import json
import logging
import sqlite3
import time
from time import monotonic

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import canonical_json, digest
from naiyue_test_agent.contracts.cloud_events import CloudEvent
from naiyue_test_agent.contracts.cloud_history import HistoryLimits
from .history_projection import project
from .history_reads import archive_page,archive_threads,archive_message
from .usage import CloudUsage

logger = logging.getLogger(__name__)


class CloudHistory:
    def __init__(self, database, task_id, *, initialize, limits=None,read_timeout=5.0):
        self.database = database
        self.limits=limits or HistoryLimits()
        with database.transaction(write=initialize) as connection:
            meta = dict(connection.execute('SELECT key,value FROM meta'))
            if meta.get('task_id', task_id) != task_id:
                raise CloudError('history_identity_mismatch', 503)
            if not initialize and ('task_id' not in meta or 'last_seq' not in meta):
                raise CloudError('history_metadata_missing', 503)
            upper = connection.execute('SELECT MAX(seq),COUNT(*) FROM events').fetchone()
            last = meta.get('last_seq', '0')
            if (not last.isdecimal() or int(last) != (upper[0] or 0) or upper[1] != int(last)
                    or (int(last) > 0 and 'epoch' not in meta)):
                raise CloudError('history_metadata_invalid', 503)
            if initialize:
                connection.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('task_id',?)", (task_id,))
                connection.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('last_seq','0')")
        with database.transaction() as connection:
            projected=connection.execute("SELECT value FROM meta WHERE key='projected_seq'").fetchone()
        if projected is None:self.rebuild()
        elif projected[0]!=last:raise CloudError('history_projection_stale',503)
        self.usage=CloudUsage(database)
        self.usage_reopen_incomplete=False
        try:self.usage.reopen(read_timeout=read_timeout)
        except (OSError,sqlite3.Error,CloudError,ValueError) as error:
            self.usage_reopen_incomplete=True
            logger.warning('cloud_usage_reopen_incomplete error_type=%s',type(error).__name__)

    def append(self, batch, *, owner_inputs=None):
        with self.database.transaction(write=True) as connection:
            meta = dict(connection.execute('SELECT key,value FROM meta'))
            last = int(meta['last_seq'])
            confirmed = last
            if meta.get('epoch') not in (None, batch.epoch):
                raise CloudError('history_epoch_mismatch', last_seq=last)
            for command_id,receipt in (owner_inputs or {}).items():
                old=connection.execute('SELECT run_id,message FROM history_owners WHERE command_id=?',(command_id,)).fetchone()
                if old and (old['run_id'],old['message'])!=(receipt['run_id'],receipt['message']):
                    raise CloudError('history_owner_conflict')
                connection.execute('INSERT OR IGNORE INTO history_owners VALUES(?,?,?)',
                    (command_id,receipt['run_id'],receipt['message']))
            for event in batch.events:
                if event.source_generation > batch.generation:
                    raise CloudError('event_generation_mismatch')
                fingerprint = digest(event.model_dump())
                existing = connection.execute('SELECT digest FROM events WHERE seq=?', (event.seq,)).fetchone()
                if existing is not None:
                    if existing[0] != fingerprint:
                        raise CloudError('history_conflict', last_seq=confirmed)
                    continue
                if event.seq != last + 1:
                    raise CloudError('history_gap', last_seq=confirmed)
                connection.execute('''INSERT INTO events(seq,source_generation,run_id,kind,payload_json,digest,received_at)
                    VALUES(?,?,?,?,?,?,?)''', (event.seq, event.source_generation, event.run_id, event.kind,
                    canonical_json(event.payload), fingerprint, time.time()))
                project(connection,event,self.limits)
                last = event.seq
            connection.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('epoch',?)", (batch.epoch,))
            connection.execute("UPDATE meta SET value=? WHERE key='last_seq'", (str(last),))
            connection.execute("INSERT OR REPLACE INTO meta VALUES('projected_seq',?)",(str(last),))
        return {'epoch': batch.epoch, 'last_seq': last}

    def rebuild(self):
        with self.database.transaction(write=True) as c:
            for table in ('history_messages','history_threads','history_replies','history_parts','history_transfers','history_legacy'):
                # Fixed internal table names; never user input.
                c.execute({'history_messages':'DELETE FROM history_messages','history_threads':'DELETE FROM history_threads',
                    'history_replies':'DELETE FROM history_replies','history_parts':'DELETE FROM history_parts',
                    'history_transfers':'DELETE FROM history_transfers','history_legacy':'DELETE FROM history_legacy'}[table])
            c.execute("DELETE FROM meta WHERE key='metadata_incomplete'")
            for row in c.execute('SELECT * FROM events ORDER BY seq'):
                event=CloudEvent(seq=row['seq'],source_generation=row['source_generation'],run_id=row['run_id'],
                    kind=row['kind'],payload=json.loads(row['payload_json']))
                if digest(event.model_dump())!=row['digest']:raise CloudError('history_journal_corrupt',503)
                project(c,event,self.limits)
            last=c.execute("SELECT value FROM meta WHERE key='last_seq'").fetchone()[0]
            c.execute("INSERT OR REPLACE INTO meta VALUES('projected_seq',?)",(last,))
        return dict(last_seq=int(last))

    def archive_page(self,**kwargs):return archive_page(self.database,**kwargs)
    def archive_threads(self,**kwargs):return archive_threads(self.database,**kwargs)
    def archive_message(self,**kwargs):return archive_message(self.database,**kwargs)

    def confirmations(self,identities,*,upper=None,read_timeout):
        from .history_reads import archive_read
        from naiyue_test_agent.agent_runtime.message_presentation import message_view,message_content_id
        from langchain_core.messages import messages_from_dict
        result={}
        with archive_read(self.database,read_timeout,upper) as (c,coverage):
            for thread,message in identities:
                row=c.execute('''SELECT run_id,revision,body_json FROM history_messages
                    WHERE thread_id=? AND message_id=? AND seq<=? ORDER BY revision DESC LIMIT 1''',
                    (thread,message,coverage['coverage_seq'])).fetchone()
                if row:
                    view=message_view(messages_from_dict([json.loads(row['body_json'])])[0])
                    result[(thread,message)]=dict(run_id=row['run_id'],revision=row['revision'],content_id=message_content_id(view))
        return result

    def page(self, *, epoch=None, after_seq=0, limit, byte_limit, read_timeout):
        with self.database.transaction() as connection:
            deadline = monotonic() + read_timeout
            connection.set_progress_handler(lambda: monotonic() >= deadline, 100)
            try:
                meta = dict(connection.execute('SELECT key,value FROM meta'))
                current = meta.get('epoch')
                last = int(meta['last_seq'])
                if epoch is not None and epoch != current:
                    raise CloudError('history_epoch_mismatch', last_seq=last)
                if after_seq > last:
                    raise CloudError('history_cursor_ahead', last_seq=last)
                cursor = connection.execute('''SELECT seq,source_generation,run_id,kind,payload_json FROM events
                    WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?''', (after_seq, last, limit))
                result = {'epoch': current, 'last_seq': last, 'events': [], 'next_seq': after_seq}
                used = len(canonical_json(result).encode())
                for row in cursor:
                    event = dict(seq=row['seq'], source_generation=row['source_generation'], run_id=row['run_id'],
                        kind=row['kind'], payload=json.loads(row['payload_json']))
                    size = len(canonical_json(event).encode()) + (1 if result['events'] else 0)
                    growth = len(str(row['seq'])) - len(str(result['next_seq']))
                    if used + size + growth > byte_limit:
                        if not result['events']:
                            raise CloudError('history_event_too_large', 413, last_seq=last)
                        break
                    used += size + growth
                    result['events'].append(event)
                    result['next_seq'] = row['seq']
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                    logger.warning('cloud_history_read_timeout')
                    raise CloudError('history_read_timeout', 503) from error
                raise
            finally:
                connection.set_progress_handler(None, 0)
        return result

    def event(self,seq):
        with self.database.transaction() as connection:
            row=connection.execute('SELECT source_generation,run_id,kind,payload_json FROM events WHERE seq=?',(seq,)).fetchone()
            epoch=connection.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
            if row is None or epoch is None:return None
            return dict(epoch=epoch[0],source_generation=row['source_generation'],run_id=row['run_id'],
                kind=row['kind'],payload=json.loads(row['payload_json']))
