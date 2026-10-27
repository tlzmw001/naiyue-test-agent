"""Bounded versioned JSONL; no executable object deserialization."""
import base64
import json
from dataclasses import asdict,is_dataclass
from naiyue_test_agent.contracts.sandbox import SandboxError, PROTOCOL_VERSION


def encode(value,limit):
    raw=json.dumps(value,ensure_ascii=True,separators=(',',':')).encode()+b'\n'
    if len(raw)>limit: raise SandboxError('env_message_too_large')
    return raw


def receive(stream,limit):
    raw=stream.readline(limit+1)
    if not raw or len(raw)>limit or not raw.endswith(b'\n'):
        raise SandboxError('env_protocol_mismatch')
    try:
        result=json.loads(raw)
    except (ValueError,UnicodeError) as error:
        raise SandboxError('env_protocol_mismatch') from error
    if not isinstance(result,dict): raise SandboxError('env_protocol_mismatch')
    return result


def pack(value):
    if isinstance(value,bytes):return {'bytes_base64':base64.b64encode(value).decode()}
    if is_dataclass(value):return asdict(value)
    return value


def check_request(request,environment,session_id):
    if (set(request)!={'protocol','request_id','environment','session_id','operation','params'}
            or request['protocol']!=PROTOCOL_VERSION
            or request['environment']!=environment.public() or request['session_id']!=session_id
            or not isinstance(request['request_id'],str) or not 1<=len(request['request_id'])<=64
            or not isinstance(request['operation'],str) or not isinstance(request['params'],dict)):
        raise SandboxError('env_binding_mismatch')
