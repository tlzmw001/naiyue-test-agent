"""SQL allowlist for model-visible text; raw message JSON never leaves SQLite."""

import json
import string


def _json_text(encoded):
    value = json.loads(encoded)[0]
    return value if isinstance(value, str) else None


def configure_connection(connection):
    # Legacy JSON1 scalar extraction and text functions can stop at NUL.
    ascii_lower = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
    connection.create_function('naiyue_visible_json_text', 1, _json_text, deterministic=True)
    connection.create_function('naiyue_visible_length', 1, len, deterministic=True)
    connection.create_function('naiyue_visible_slice', 3,
        lambda text, start, count: text[start:start + count], deterministic=True)
    connection.create_function('naiyue_visible_lower', 1,
        lambda text: text.translate(ascii_lower), deterministic=True)


VISIBLE_TEXT = """CASE json_type(m.body_json,'$.data.content')
    WHEN 'text' THEN naiyue_visible_json_text(json_extract(m.body_json,'$.data.content','$.data.content'))
    WHEN 'array' THEN COALESCE((SELECT group_concat(
        CASE WHEN b.type='text' THEN naiyue_visible_json_text(json_extract(m.body_json,b.fullkey,b.fullkey))
             WHEN b.type='object' AND naiyue_visible_json_text(json_extract(b.value,'$.type','$.type'))='text'
                  AND json_type(b.value,'$.text')='text'
                  THEN naiyue_visible_json_text(json_extract(b.value,'$.text','$.text'))
             ELSE '' END,'') FROM json_each(m.body_json,'$.data.content') b),'')
    ELSE '' END"""

VISIBLE_WHERE = """m.session_id=? AND m.thread_id=? AND m.checkpoint_ns=''
    AND m.kind='message' AND m.message_type IN ('human','ai','tool')
    AND COALESCE(json_extract(m.body_json,'$.data.additional_kwargs.naiyue_image_carrier'),0)!=1"""

FIELDS = """m.message_id,m.sequence,m.revision,m.message_type,m.parent_message_id,m.reply_id,
    CASE WHEN m.message_type='tool' THEN naiyue_visible_json_text(
        json_extract(m.body_json,'$.data.tool_call_id','$.data.tool_call_id')) END AS tool_call_id,
    CASE WHEN m.message_type='tool' THEN naiyue_visible_json_text(
        json_extract(m.body_json,'$.data.name','$.data.name')) END AS name,
    CASE WHEN m.message_type='tool' THEN naiyue_visible_json_text(
        json_extract(m.body_json,'$.data.status','$.data.status')) END AS tool_status"""


def page(connection, session_id, thread_id, *, upper, before, count, chars, terms=()):
    # Expressions are fixed source constants. All identities, terms and limits are bound values.
    sql = f"""WITH source AS (SELECT {FIELDS},{VISIBLE_TEXT} AS text FROM naiyue_chat_messages m
        WHERE {VISIBLE_WHERE} AND m.sequence<=? AND m.sequence<? ORDER BY m.sequence DESC LIMIT ?),
        matched AS (SELECT *,NOT EXISTS(SELECT 1 FROM json_each(?) q
            WHERE instr(naiyue_visible_lower(text),naiyue_visible_lower(
                naiyue_visible_json_text(json_extract(?,q.fullkey,q.fullkey))))=0) AS matched,
            CASE WHEN ?!='' THEN max(0,instr(naiyue_visible_lower(text),naiyue_visible_lower(?))-1-?) ELSE 0 END AS start FROM source)
        SELECT message_id,sequence,revision,message_type,parent_message_id,reply_id,tool_call_id,name,tool_status,
            naiyue_visible_length(text) AS text_chars,matched,start AS offset,
            CASE WHEN matched THEN naiyue_visible_slice(text,start,?) ELSE '' END AS content FROM matched
        ORDER BY sequence DESC"""
    encoded_terms = json.dumps(terms, ensure_ascii=False)
    return [dict(r) for r in connection.execute(sql, (session_id, thread_id, upper, before, count,
        encoded_terms, encoded_terms, terms[0] if terms else '', terms[0] if terms else '',
        chars // 3, chars))]


def message(connection, session_id, thread_id, sequence, *, offset, chars):
    sql = f"""WITH source AS (SELECT {FIELDS},{VISIBLE_TEXT} AS text FROM naiyue_chat_messages m
        WHERE {VISIBLE_WHERE} AND m.sequence=?)
        SELECT message_id,sequence,revision,message_type,parent_message_id,reply_id,tool_call_id,name,tool_status,
            naiyue_visible_length(text) AS text_chars,? AS offset,naiyue_visible_slice(text,?,?) AS content FROM source"""
    row = connection.execute(sql, (session_id, thread_id, sequence, offset, offset, chars)).fetchone()
    return dict(row) if row else None


def public_record(record, conversation_revision):
    result = {k: record[k] for k in ('message_id', 'sequence', 'revision', 'parent_message_id', 'reply_id')}
    result.update(type=record['message_type'], conversation_revision=conversation_revision,
        content=record['content'], offset=record['offset'], text_chars=record['text_chars'])
    if record['message_type'] == 'tool':
        result.update(tool_call_id=record['tool_call_id'], name=record['name'], tool_status=record['tool_status'])
    finish_record(result)
    return result


def finish_record(record):
    record['next_offset'] = record['offset'] + len(record['content'])
    record['complete'] = record['next_offset'] >= record['text_chars']
