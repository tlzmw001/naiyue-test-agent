"""Explicit HTTP-shaped presentation stubs; no application dispatch or default capabilities."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit


def protocol_stub(**facts):
    def request(method, suffix, *, json=None, request_id=None):
        url = urlsplit(suffix)
        parts, query = url.path.strip('/').split('/'), parse_qs(url.query)
        if parts[0] == 'background-tasks':
            tasks = facts['tasks']
            if len(parts) == 1:
                return tasks.list(before=query.get('before', [None])[0])
            return (tasks.stop if parts[-1] == 'stop' else tasks.output)(parts[1])
        if parts[0] == 'mcp':
            return facts['mcp'].refresh()
        if parts == ['session-controls']:
            return {'tools': [{'name': spec.tool.name, 'unavailable_reason':
                spec.permissions.unavailable_reason() if hasattr(spec.permissions, 'unavailable_reason') else None}
                for spec in facts['tools'].list()]}
        if parts[0] == 'sessions':
            sid = parts[1]
            if len(parts) == 2:
                return {'active_tools': list(facts['sessions'].active_tools(sid))}
            if parts[2] == 'tools':
                return facts['sessions'].select_tools(sid, json['active_tools'])
            observation = facts['observability']
            if parts[2] == 'usage':
                return observation.usage(sid, after_model=query.get('after_model', [None])[0])
            if len(parts) == 3:
                return observation.runs(sid, before=int(query['before'][0]) if 'before' in query else None)
            return observation.events(sid, parts[3], after=int(query.get('after', ['0'])[0]))
        raise AssertionError(f'Unexpected terminal request: {method} {suffix}')
    return SimpleNamespace(request=request)
