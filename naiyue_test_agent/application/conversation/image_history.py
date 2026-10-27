"""Authenticated adapters call this ownership and canonical revision boundary."""
import base64
import json
from langchain_core.messages import messages_from_dict
from ...agent_runtime.async_utils import run_blocking
from ...contracts.image_content import source_revision as message_revision
from ...contracts.image_inputs import ImageInputError


async def read_image(api, row, session_id, message_id, index, *, source_revision, conversation_revision):
    if row.get('conversation_revision', 0) != conversation_revision:
        raise ImageInputError('image_revision_changed', status=409)
    limits = api._sessions.image_input_settings
    records = await run_blocking(api._conversation.image_message, session_id, row['thread_id'],
        message_id, limits.max_total_base64_bytes + 32768*4 + 65536)
    message = None
    if records:
        record = records[0]
        if record['body_hash'] != source_revision:
            raise ImageInputError('image_revision_changed', status=409)
        if record['body_json'] is not None:
            message = messages_from_dict([json.loads(record['body_json'])])[0]
    elif api._sessions.has_session(session_id):
        message = next((m for m in await api.messages(session_id) if m.id == message_id), None)
    if message is None:
        raise ImageInputError('image_not_ready' if row.get('busy') else 'image_not_found', status=409 if row.get('busy') else 404)
    if message_revision(message) != source_revision:
        raise ImageInputError('image_revision_changed', status=409)
    content = message.content
    block = content[index] if isinstance(content,list) and 0 <= index < len(content) else None
    if not isinstance(block,dict) or block.get('type') != 'image' or not block.get('extras',{}).get('client_id'):
        raise ImageInputError('image_not_found', status=404)
    if len(block.get('base64','')) > limits.max_base64_bytes:
        raise ImageInputError('image_input_too_large', status=413)
    return base64.b64decode(block['base64'],validate=True), block['mime_type']
