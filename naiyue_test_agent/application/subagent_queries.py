"""Read-only continuation hints and complete-JSON budgets, never execution admission."""

import json
from langchain_core.tools import ToolException
from .subagent_records import role_fingerprint


def annotate(records, owned, roles, catalog, active_run_id=None):
    specs = {spec.name: spec for spec in roles}
    rows = []
    for row in records:
        record, spec = owned.get(row['agent_id']), specs.get(row['agent'])
        reason = None
        if record is None:
            reason = 'legacy_instance'
        elif record['state'] not in ('ready', 'pending_parent'):
            reason = record['state']
        elif record['state'] == 'pending_parent' and record['last_parent_run_id'] != active_run_id:
            reason = 'pending_parent'
        elif spec is None:
            reason = 'role_unavailable'
        elif role_fingerprint(spec) != record['role_fingerprint']:
            reason = 'role_changed'
        else:
            try:
                catalog.get(record['model_id'])
            except ValueError:
                reason = 'model_unavailable'
        rows.append({**row, 'resume_available': reason is None, 'resume_reason': reason})
    return rows


def bounded_list(records, owned, settings, *, limit=None, before=None):
    limit = settings.max_list_items if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= settings.max_list_items:
        raise ToolException('Subagent list limit exceeds its configured range.')
    # Unregistered legacy records have no creation timestamp; preserve archive order.
    legacy = [row for row in records if row['agent_id'] not in owned]
    registered = sorted((row for row in records if row['agent_id'] in owned),
        key=lambda row: (owned[row['agent_id']]['created_at'], row['agent_id']))
    ordered = list(reversed([*legacy, *registered]))
    if before is not None:
        index = next((i for i, row in enumerate(ordered) if row['agent_id'] == before), None)
        if index is None:
            raise ToolException('Subagent list cursor is unavailable in this session.')
        ordered = ordered[index + 1:]
    def encode(items, more):
        return json.dumps(dict(items=items, next_before=items[-1]['agent_id'] if more and items else None,
            truncated=more), ensure_ascii=False, separators=(',', ':'))
    items = []
    for index, row in enumerate(ordered[:limit]):
        item = {key: row[key] for key in ('agent_id', 'agent', 'task', 'status', 'resume_available', 'resume_reason')}
        item['task'] = item['task'][:settings.task_preview_chars]
        more = index + 1 < len(ordered)
        if len(encode([*items, item], more).encode()) > settings.max_list_bytes:
            if items:
                break
            preview = item['task']
            low, high = 0, len(preview)
            item['task'] = ''
            if len(encode([item], more).encode()) > settings.max_list_bytes:
                raise ToolException('Subagent list budget cannot hold one item and pagination metadata.')
            while low < high:
                mid = (low + high + 1) // 2
                item['task'] = preview[:mid]
                if len(encode([item], more).encode()) <= settings.max_list_bytes:
                    low = mid
                else:
                    high = mid - 1
            item['task'] = preview[:low]
        items.append(item)
    result = encode(items, len(items) < len(ordered))
    if len(result.encode()) > settings.max_list_bytes:
        raise ToolException('Subagent list budget cannot hold pagination metadata.')
    return result
