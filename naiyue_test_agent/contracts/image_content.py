"""Canonical image metadata and source revision shared by history and events."""
import hashlib
import json
from langchain_core.messages import message_to_dict


def source_revision(message):
    body = json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def image_metadata(content, revision):
    result = []
    for index, block in enumerate(content if isinstance(content, list) else []):
        if not isinstance(block, dict) or block.get('type') != 'image' or not isinstance(block.get('base64'), str):
            continue
        extra = block.get('extras', {})
        if not extra.get('client_id'):
            continue
        encoded = block['base64']
        value = dict(first_frame_only=extra.get('source_format') == 'GIF',index=index, name=extra['name'], mime_type=block['mime_type'],
            width=extra['width'], height=extra['height'], bytes=len(encoded)*3//4-len(encoded)+len(encoded.rstrip('=')),
            image_digest=extra['image_digest'])
        if revision is not None:
            value['source_revision'] = revision
        result.append(value)
    return result


def message_images(message):
    if not has_user_images(message.content):
        return []
    return image_metadata(message.content, source_revision(message))


def input_text(content):
    if isinstance(content, str):
        return content
    return ''.join(block['text'] for block in content if isinstance(block, dict)
        and block.get('type') == 'text' and not block.get('extras', {}).get('naiyue_image_description'))


def has_user_images(content):
    return isinstance(content, list) and any(isinstance(block, dict) and block.get('type') == 'image'
        and isinstance(block.get('extras'),dict) and isinstance(block['extras'].get('client_id'),str) for block in content)
