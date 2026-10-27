"""Authenticated image custody; payloads never enter command receipts or SSE."""
import base64
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from .dependencies import get_service
from .errors import public_error, WebError
from .serialization import pending_input_views
from .schemas import CompactInput

router=APIRouter()


@router.get('/sessions/{session_id}/pending-inputs')
async def pending(session_id:str,service=Depends(get_service)):
    await service._session_status(session_id)
    try:
        return {'inputs':pending_input_views(service.application.runs.pending_inputs(session_id),service.settings)}
    except KeyError:
        return {'inputs':[]}


@router.get('/sessions/{session_id}/pending-inputs/{message_id}/text')
async def text(session_id:str,message_id:str,offset:int=Query(default=0,ge=0),service=Depends(get_service)):
    await service._session_status(session_id)
    try:
        value=service.application.runs.pending_text(session_id,message_id)
    except Exception as error:
        raise public_error(error) from None
    if offset>len(value):
        raise WebError('invalid_request',422)
    chars=min(service.settings.history_detail_chars,max(1,service.settings.max_frame_bytes//12))
    chunk=value[offset:offset+chars]
    return {'text':chunk,'next_offset':offset+len(chunk),'complete':offset+len(chunk)>=len(value)}


@router.get('/sessions/{session_id}/pending-inputs/{message_id}/images/{index}')
async def image(session_id:str,message_id:str,index:int,service=Depends(get_service)):
    await service._session_status(session_id)
    try:
        block=service.application.runs.pending_image(session_id,message_id,index)
        data=base64.b64decode(block['base64'],validate=True)
    except Exception as error:
        raise public_error(error) from None
    return Response(data,media_type=block['mime_type'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.delete('/sessions/{session_id}/pending-inputs/{message_id}')
async def remove(session_id:str,message_id:str,body:CompactInput,service=Depends(get_service)):
    await service._session_status(session_id)
    try:
        service.application.runs.remove_pending(session_id,message_id)
    except Exception as error:
        raise public_error(error) from None
    return {'removed':True}
