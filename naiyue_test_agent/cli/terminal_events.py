"""Decode immutable wire facts for the existing terminal renderer."""
from pydantic import TypeAdapter
from naiyue_test_agent.agent_runtime.events import RuntimeEvent, AgentEventSource, _PAYLOADS
from naiyue_test_agent.service.settings import ServiceError

_ADAPTERS = {kind: TypeAdapter(payload) for kind, payload in _PAYLOADS.items()}
_SOURCE = TypeAdapter(AgentEventSource)


def event_fact(value, session_id, run_id, after):
    if not isinstance(value, dict):
        raise ServiceError('service_event_invalid')
    if (value.get('version') != 1 or value.get('session_id') != session_id
            or value.get('run_id') != run_id or type(value.get('sequence')) is not int
            or value['sequence'] <= after or value.get('event_type') not in _ADAPTERS):
        raise ServiceError('service_event_invalid')
    kind = value['event_type']
    return RuntimeEvent(kind, session_id, run_id, value['sequence'], value['timestamp'],
        _ADAPTERS[kind].validate_python(value['data']),
        _SOURCE.validate_python(value['source']) if value.get('source') else None)
