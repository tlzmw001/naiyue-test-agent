"""Versioned required archive records; frame splitting never truncates a body."""
from hashlib import sha256
from typing import Literal, Annotated

from pydantic import Field, ValidationError, StringConstraints, model_validator

from .cloud_tasks import CloudRecord, CloudError, ContentId, UUIDText, TaskId
from .cloud_events import canonical_json, digest

Identity = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class HistoryLimits(CloudRecord):
    max_message_bytes: int = Field(default=16*1024*1024,ge=4096,le=64*1024*1024)
    max_pending_bytes: int = Field(default=32*1024*1024,ge=4096,le=256*1024*1024)
    max_pending_messages: int = Field(default=16,ge=1,le=128)
    max_parts: int = Field(default=16384,ge=1,le=65536)
    preview_chars: int = Field(default=512,ge=0,le=4096)
    detail_chars: int = Field(default=16384,ge=1,le=65536)
    cursor_seconds: int = Field(default=3600,ge=1,le=86400)
    body_depth: int = Field(default=64,ge=8,le=128)


class HistoryThread(CloudRecord):
    thread_id: Identity
    agent_path: Identity
    role: Literal['root','child']
    agent_id: Identity | None = None
    parent_run_id: TaskId | None = None
    parent_tool_call_id: Identity | None = None
    parent_reply_id: Identity | None = None

    @model_validator(mode='after')
    def relationship(self):
        if self.role=='root':
            if self.agent_path!='root' or any((self.agent_id,self.parent_run_id,self.parent_tool_call_id,self.parent_reply_id)):
                raise ValueError('invalid_root')
        elif not all((self.agent_id,self.parent_run_id,self.parent_tool_call_id)):
            raise ValueError('missing_child_relationship')
        return self


class ReplyBoundary(CloudRecord):
    reply_id: Identity
    input_message_id: Identity
    source_checkpoint_id: Identity | None = None


class HistoryMessage(CloudRecord):
    message_id: Identity
    body_json: str
    body_hash: ContentId
    message_type: Literal['human','ai','tool']
    parent_message_id: Identity | None = None
    reply_id: Identity | None = None
    kind: Literal['message','context_summary']
    source_checkpoint_id: Identity
    occurred_at: float | None = Field(default=None,ge=0)
    revision: int = Field(ge=1,le=9223372036854775807)
    position: int = Field(ge=1,le=9223372036854775807)


class MessageRecord(CloudRecord):
    record: Literal['message']
    version: Literal[2]
    command_id: UUIDText
    thread_id: Identity
    thread: HistoryThread
    checkpoint_id: Identity
    boundary: ReplyBoundary | None = None
    message: HistoryMessage

    @model_validator(mode='after')
    def scope(self):
        if self.thread_id!=self.thread.thread_id:raise ValueError('thread_mismatch')
        if self.boundary and (self.message.reply_id!=self.boundary.reply_id
                or self.message.message_id!=self.boundary.input_message_id or self.message.message_type!='human'):
            raise ValueError('boundary_mismatch')
        return self


class MessagePart(CloudRecord):
    record: Literal['message_part']
    version: Literal[2]
    transfer_id: ContentId
    index: int = Field(ge=0,le=65536)
    text: str = Field(min_length=1)
    header: dict | None = None


class MessageCommit(CloudRecord):
    record: Literal['message_commit']
    version: Literal[2]
    transfer_id: ContentId
    parts: int = Field(ge=1,le=65536)
    body_bytes: int = Field(ge=1,le=64*1024*1024)
    body_hash: ContentId


def validated_record(payload, limits):
    try:
        record=MessageRecord.model_validate(payload)
        raw=record.message.body_json.encode('utf-8')
        if len(raw)>limits.max_message_bytes:raise CloudError('history_message_too_large',413)
        if sha256(raw).hexdigest()!=record.message.body_hash:raise ValueError('body_hash')
        return record
    except (ValidationError,ValueError,UnicodeError):
        raise CloudError('invalid_history_record',422) from None


def message_frames(payload, *, frame_bytes, limits):
    """Reserve envelope space and account for JSON escaping, including Unicode."""
    record=validated_record(payload,limits)
    if len(canonical_json(payload).encode())<=frame_bytes-1536:
        yield payload
        return
    header=record.model_dump();body=header['message'].pop('body_json')
    transfer=digest(header)
    index=0;offset=0
    while offset<len(body):
        size=max(1,(frame_bytes-2048)//6)
        while True:
            part=dict(record='message_part',version=2,transfer_id=transfer,index=index,text=body[offset:offset+size])
            if index==0:part['header']=header
            if len(canonical_json(part).encode())<=frame_bytes-1536:break
            if size==1:raise CloudError('history_header_too_large',413)
            size=max(1,size//2)
        if index>=limits.max_parts:raise CloudError('history_too_many_parts',413)
        yield part
        offset+=size;index+=1
    yield dict(record='message_commit',version=2,transfer_id=transfer,parts=index,
        body_bytes=len(body.encode()),body_hash=record.message.body_hash)
