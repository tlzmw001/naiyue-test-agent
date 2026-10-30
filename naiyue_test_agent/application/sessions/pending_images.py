"""Session-owned bounded image custody. All callers hold the existing state lock."""
from copy import deepcopy
from ...contracts.image_content import image_metadata, input_text
from ...contracts.image_inputs import ImageInputError


def validate_release(runtime, identities,*,waiting=False):
    for key in identities:
        row = runtime.pending_images.get(key)
        allowed=(row is not None and row['status']=='queued' and row['run_id'] is None) if waiting else (
            row is not None and row['status'] in ('retained','uncertain'))
        if not allowed:
            raise ImageInputError('pending_image_invalid', status=409)


def retain(runtime, item, settings, replace_ids=()):
    if not isinstance(item.content,list) or not any(isinstance(b,dict) and b.get('type')=='image' for b in item.content):
        return
    size=sum(len(b.get('base64','')) for b in item.content if isinstance(b,dict))
    records=runtime.pending_images
    validate_release(runtime, replace_ids)
    remaining = {key: row for key, row in records.items() if key not in replace_ids}
    if len(remaining)>=settings.max_pending_image_messages or sum(r['bytes'] for r in remaining.values())+size>settings.max_pending_image_bytes:
        raise ImageInputError('pending_image_limit',status=409)
    for key in replace_ids:
        records.pop(key, None)
    records[item.message_id]={'item':item,'status':'queued',
        'run_id':runtime.run.run_id if runtime.run else None,'bytes':size}


def views(records):
    return [dict(message_id=key, text=input_text(row['item'].content),legacy_text=row['item'].text,kind=row['item'].kind,status=row['status'],
        images=image_metadata(row['item'].content, None))
        for key,row in records]


def content(runtime, message_id, index):
    row=runtime.pending_images.get(message_id)
    blocks=row['item'].content if row else []
    if not 0<=index<len(blocks) or not isinstance(blocks[index],dict) or blocks[index].get('type')!='image':
        raise ImageInputError('image_not_found',status=404)
    return deepcopy(blocks[index])
