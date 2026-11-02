"""Bounded, short Coordinator archive reads; no connection escapes the owner."""
from contextlib import contextmanager
import json
import logging
import sqlite3
from time import monotonic

from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


@contextmanager
def archive_read(database,timeout,upper=None):
    with database.transaction() as c:
        deadline=monotonic()+timeout
        c.set_progress_handler(lambda:monotonic()>=deadline,100)
        try:
            meta=dict(c.execute('SELECT key,value FROM meta'));last=int(meta['last_seq'])
            if upper is not None and (type(upper) is not int or not 0<=upper<=last):
                raise CloudError('history_cursor_ahead')
            yield c,dict(epoch=meta.get('epoch'),coverage_seq=last if upper is None else upper,
                metadata_incomplete=meta.get('metadata_incomplete')=='1')
            if monotonic()>=deadline:
                logger.warning('cloud_archive_read_timeout')
                raise CloudError('history_read_timeout',503)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode==sqlite3.SQLITE_INTERRUPT:
                logger.warning('cloud_archive_read_timeout')
                raise CloudError('history_read_timeout',503) from error
            raise
        finally:c.set_progress_handler(None,0)


def archive_page(database,*,thread_id=None,limit,read_timeout,upper=None,before=None):
    with archive_read(database,read_timeout,upper) as (c,result):
        upper=result['coverage_seq']
        if thread_id is None:
            roots=c.execute('''SELECT record_json FROM history_threads WHERE seq<=? ORDER BY seq DESC''',(upper,))
            thread_id=next((t['thread_id'] for r in roots if (t:=json.loads(r[0]))['role']=='root'),None)
        row=c.execute('SELECT record_json FROM history_threads WHERE thread_id=? AND seq<=? ORDER BY seq DESC LIMIT 1',
            (thread_id,upper)).fetchone()
        result.update(thread=json.loads(row[0]) if row else None,messages=[],next_before=None)
        rows=c.execute('''SELECT message_id,revision,seq,position,record_json,preview_json,owner_input FROM history_messages AS m
            WHERE thread_id=? AND seq<=? AND (? IS NULL OR position<?)
            AND NOT EXISTS(SELECT 1 FROM history_messages AS n WHERE n.thread_id=m.thread_id
                AND n.message_id=m.message_id AND n.seq<=? AND n.revision>m.revision)
            ORDER BY position DESC LIMIT ?''',(thread_id,upper,before,before,upper,limit+1)).fetchall()
        for row in rows[:limit]:
            item=json.loads(row['record_json'])|dict(preview=json.loads(row['preview_json']),seq=row['seq'],owner_input=bool(row['owner_input']))
            reply=c.execute('''SELECT record_json FROM history_replies WHERE thread_id=? AND reply_id=? AND seq<=?
                ORDER BY seq DESC LIMIT 1''',(thread_id,item['reply_id'],upper)).fetchone()
            item['boundary']=json.loads(reply[0]) if reply else None
            result['messages'].append(item)
        if len(rows)>limit:result['next_before']=rows[limit-1]['position']
        result['messages'].reverse()
        return result


def archive_threads(database,*,limit,read_timeout,upper=None,after=''):
    with archive_read(database,read_timeout,upper) as (c,result):
        rows=c.execute('''SELECT thread_id,record_json FROM history_threads AS t WHERE thread_id>? AND seq<=?
            AND NOT EXISTS(SELECT 1 FROM history_threads AS n WHERE n.thread_id=t.thread_id AND n.seq<=? AND n.seq>t.seq)
            ORDER BY thread_id LIMIT ?''',(after,result['coverage_seq'],result['coverage_seq'],limit+1)).fetchall()
        result['threads']=[json.loads(r['record_json']) for r in rows[:limit]]
        result['next_after']=rows[limit-1]['thread_id'] if len(rows)>limit else None
        return result


def archive_message(database,*,thread_id,message_id,revision,read_timeout):
    with archive_read(database,read_timeout) as (c,_):
        row=c.execute('SELECT record_json,body_json FROM history_messages WHERE thread_id=? AND message_id=? AND revision=?',
            (thread_id,message_id,revision)).fetchone()
        if not row:raise CloudError('history_message_not_found',404)
        return json.loads(row['record_json'])|dict(body_json=row['body_json'])
