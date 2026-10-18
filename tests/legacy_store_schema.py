"""Remove post-v10 structures when constructing a genuine older schema fixture."""


def remove_reply_schema(connection):
    connection.executescript('''
        DROP TABLE IF EXISTS naiyue_regenerations;
        DROP TABLE IF EXISTS naiyue_reply_boundaries;
        ALTER TABLE naiyue_sessions DROP COLUMN conversation_revision;
        ALTER TABLE naiyue_chat_messages DROP COLUMN reply_id;
        ALTER TABLE naiyue_chat_messages DROP COLUMN source_thread_id;
    ''')
