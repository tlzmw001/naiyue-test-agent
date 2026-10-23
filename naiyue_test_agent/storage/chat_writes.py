"""Archive SQL used by both preparation and the required run unit of work."""

import logging

from naiyue_test_agent.application.errors import SessionPersistenceError

from naiyue_test_agent.application.conversation import reply_storage


logger = logging.getLogger(__name__)


def write_chat_updates(connection, session_id, updates):
    for update in updates:
        scope = (session_id, update.thread_id, update.checkpoint_ns)
        head = connection.execute('''SELECT revision,history_version FROM naiyue_chat_sync
            WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''', scope).fetchone()
        if (head['revision'] if head else 0) != update.expected_revision:
            raise SessionPersistenceError('chat_history_stale')
        history_version = head['history_version'] if head else 0
        sequence = connection.execute('''SELECT COALESCE(MAX(sequence),0) FROM naiyue_chat_messages
            WHERE session_id=? AND thread_id=? AND checkpoint_ns=?''', scope).fetchone()[0]
        for message in update.messages:
            key = (*scope, message['message_id'])
            saved = connection.execute('''SELECT body_hash,parent_message_id FROM naiyue_chat_messages
                WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND message_id=?''', key).fetchone()
            if saved and saved['body_hash'] == message['body_hash'] and saved['parent_message_id'] == message.get('parent_message_id'):
                continue
            if saved:
                history_version += 1
                connection.execute('''UPDATE naiyue_chat_messages SET body_json=?,body_hash=?,kind=?,
                    source_checkpoint_id=?,parent_message_id=?,message_type=?,reply_id=?,occurred_at=?,revision=revision+1
                    WHERE session_id=? AND thread_id=? AND checkpoint_ns=? AND message_id=?''',
                    (message['body_json'], message['body_hash'], message['kind'], message['source_checkpoint_id'],
                     message.get('parent_message_id'), message.get('message_type'), message.get('reply_id'), message.get('occurred_at'), *key))
            else:
                sequence += 1
                connection.execute('''INSERT INTO naiyue_chat_messages
                    (session_id,thread_id,checkpoint_ns,message_id,sequence,revision,body_json,body_hash,kind,
                     source_checkpoint_id,message_type,parent_message_id,reply_id,source_thread_id,occurred_at) VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)''',
                    (*key, sequence, message['body_json'], message['body_hash'], message['kind'], message['source_checkpoint_id'],
                     message.get('message_type'), message.get('parent_message_id'), message.get('reply_id'), update.thread_id, message.get('occurred_at')))
        reply_storage.save_boundaries(connection, session_id, update)
        connection.execute('''INSERT INTO naiyue_chat_sync VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id,thread_id,checkpoint_ns) DO UPDATE SET
            checkpoint_id=excluded.checkpoint_id,revision=excluded.revision,
            history_version=excluded.history_version,stopped=excluded.stopped''',
            (*scope, update.agent_path, update.checkpoint_id, update.expected_revision + 1, history_version, update.stopped))
