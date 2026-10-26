"""Content-free statistics for one framework input; never a provider token bill."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from copy import deepcopy

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.model_access import MODEL_LIMITS_METADATA_KEY, COMPACTION_RESERVE_METADATA_KEY
from naiyue_test_agent.model_access.messages import is_image_block
from naiyue_test_agent.model_access.usage import normalize_model_usage


logger = logging.getLogger(__name__)
SNAPSHOT_KEY = 'naiyue_context_snapshot'
PARTS_KEY = 'naiyue_prompt_parts'
GROUPS = ('system', 'tool_definitions', 'user', 'assistant', 'tool_results', 'summary', 'other')
PARTS = ('base', 'project', 'skills', 'tool_guidance', 'subagent_roles', 'background_tasks', 'memory', 'session_directory')


def compose_model_prompt(base, project_parts, specs, roles=()):
    """One assembler for execution and preview, retaining lengths instead of extra text."""
    parts = [{'id': 'base', 'text': base}, *project_parts]
    if specs:
        summaries = [f'{s.tool.name}: {s.prompt_snippet or s.tool.description}' for s in specs]
        guidelines = dict.fromkeys(line for s in specs for line in s.prompt_guidelines if line)
        parts.append({'id': 'tool_guidance', 'text': '\n\nAvailable tools:\n' + '\n'.join(summaries) + (
            '\n\nTool guidelines:\n' + '\n'.join(guidelines) if guidelines else ''), 'count': len(specs)})
    if roles:
        parts.append({'id': 'subagent_roles', 'count': len(roles), 'text':
            '\n\nAvailable subagent roles (agent parameter):\n'
            + '\n'.join(f'{name}: {description}' for name, description in roles)
            + '\nEach delegation starts a new foreground task with independent history. '
              'The parent waits for its report; delegation does not grant permission to internal actions.'})
    descriptors = [{key: part[key] for key in ('id', 'source', 'count') if key in part}
                   | {'chars': len(part['text'])} for part in parts]
    return SystemMessage(content=''.join(p['text'] for p in parts), response_metadata={PARTS_KEY: descriptors})


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _source(value):
    return value if value in ('config', 'default') else 'unknown'


def _budget(model):
    metadata = getattr(model, 'metadata', None) or {}
    limits = metadata.get(MODEL_LIMITS_METADATA_KEY) or {}
    reserve = metadata.get(COMPACTION_RESERVE_METADATA_KEY) or {}
    window, output, headroom = (_number(limits.get('context_window_tokens')),
        _number(limits.get('max_output_tokens')), _number(reserve.get('tokens')))
    window, output = window or None, output or None
    return {'context_window_tokens': window, 'max_output_tokens': output,
        'context_window_source': _source(limits.get('context_window_source')),
        'max_output_source': _source(limits.get('max_output_source')),
        'compaction_reserve_tokens': headroom, 'compaction_reserve_source': _source(reserve.get('source')),
        'trigger_threshold_tokens': max(0, window - max(output, headroom))
            if window is not None and output is not None and headroom is not None else None}


def _system_children(message, total):
    content = message.content
    parts = message.response_metadata.get(PARTS_KEY)
    if (not isinstance(content, str) or not isinstance(parts, list) or not parts
            or any(p.get('id') not in PARTS or type(p.get('chars')) is not int or p['chars'] < 0 for p in parts)
            or sum(p['chars'] for p in parts) != len(content)):
        return []
    rows, end, previous = [], 0, 0
    for part in parts:
        end += part['chars']
        count = count_tokens_approximately([SystemMessage(content=content[:end])])
        row = {'id': part['id'], 'tokens': count - previous}
        if part.get('source') in ('AGENTS.md', 'CLAUDE.md'):
            row['source'] = part['source']
        if _number(part.get('count')) is not None:
            row['count'] = part['count']
        rows.append(row)
        previous = count
    return rows if previous == total else []


def estimate_context(model, system_message, messages, tools):
    result = {'status': 'unavailable', 'groups_status': 'unavailable',
        'estimated_input_tokens': None, 'groups': [],
        'image_count': sum(is_image_block(block) for message in messages
            if isinstance(message.content, list) for block in message.content),
        'estimator': 'framework_approximate', **_budget(model)}
    try:
        total = count_tokens_approximately([*([system_message] if system_message else []), *messages], tools=tools)
    except Exception as error:
        logger.warning('context_total_estimate_unavailable error_type=%s', type(error).__name__)
        return result
    result.update(status='available', estimated_input_tokens=total)
    try:
        rows = {name: {'id': name, 'tokens': 0, 'count': 0} for name in GROUPS}
        if system_message is not None:
            row = rows['system']
            row.update(tokens=count_tokens_approximately([system_message]), count=1)
            row['children'] = _system_children(system_message, row['tokens'])
        rows['tool_definitions'].update(tokens=count_tokens_approximately([], tools=tools), count=len(tools))
        for message in messages:
            if message.additional_kwargs.get('naiyue_compaction_summary'):
                group = 'summary'
            elif message.additional_kwargs.get('naiyue_image_carrier'):
                group = 'tool_results'
            else:
                group = {'human': 'user', 'ai': 'assistant', 'tool': 'tool_results'}.get(message.type, 'other')
            rows[group]['tokens'] += count_tokens_approximately([message])
            rows[group]['count'] += 1
        if sum(row['tokens'] for row in rows.values()) != total:
            raise ValueError('context_estimate_not_additive')
        result.update(groups_status='available',
            groups=[row for row in rows.values() if row['count'] or row['tokens']])
    except Exception as error:
        logger.warning('context_composition_unavailable error_type=%s', type(error).__name__)
    return result


def request_snapshot(context, estimate):
    return {'version': 1, 'model_call_id': context.invocation.model_call.call_id,
        'model_id': context.invocation.model_call.model_id, 'run_id': context.scope.identity.run_id,
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
        'estimate': estimate, 'usage': None}


def finish_snapshot(snapshot, response=None):
    message = next((m for m in response.result if isinstance(m, AIMessage)), None) if response else None
    result = {**snapshot, 'status': 'returned' if response is not None else 'failed'}
    usage = message.usage_metadata if message is not None else None
    if usage:
        result['usage'] = normalize_model_usage(usage)
    if message is not None:
        message.response_metadata[SNAPSHOT_KEY] = result
    return result


def _valid_group(row, *, child=False):
    allowed = {'id', 'tokens', 'count', 'source'} if child else {'id', 'tokens', 'count', 'children'}
    if (not isinstance(row, dict) or set(row) - allowed or row.get('id') not in (PARTS if child else GROUPS)
            or _number(row.get('tokens')) is None):
        return False
    if 'count' in row and _number(row['count']) is None:
        return False
    if 'source' in row and row['source'] not in ('AGENTS.md', 'CLAUDE.md'):
        return False
    if 'children' in row:
        children = row['children']
        if (row['id'] != 'system' or not isinstance(children, list) or len(children) > len(PARTS)
                or not all(_valid_group(part, child=True) for part in children)
                or children and sum(part['tokens'] for part in children) != row['tokens']):
            return False
    return True


def read_snapshot(value):
    """Validate finite saved metadata before exposing it through the public query."""
    try:
        if not isinstance(value, dict) or set(value) != {
                'version', 'model_call_id', 'model_id', 'run_id', 'recorded_at', 'status', 'estimate', 'usage'}:
            raise ValueError('invalid_snapshot_fields')
        if value['version'] != 1 or value['status'] not in ('running', 'returned', 'failed'):
            raise ValueError('invalid_snapshot_state')
        for name in ('model_call_id', 'model_id', 'run_id'):
            label = value[name]
            if label is None and name != 'model_call_id':
                continue
            if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,128}', label):
                raise ValueError('invalid_snapshot_identity')
        if not isinstance(value['recorded_at'], str) or len(value['recorded_at']) > 40:
            raise ValueError('invalid_snapshot_time')
        datetime.fromisoformat(value['recorded_at'])
        estimate = value['estimate']
        counts = ('estimated_input_tokens', 'image_count', 'context_window_tokens',
            'max_output_tokens', 'compaction_reserve_tokens', 'trigger_threshold_tokens')
        sources = ('context_window_source', 'max_output_source', 'compaction_reserve_source')
        fields = {*counts, *sources, 'status', 'groups', 'estimator'}
        if (not isinstance(estimate, dict) or set(estimate) not in (fields, fields | {'groups_status'})
                or estimate['estimator'] != 'framework_approximate'
                or any(estimate[key] is not None and _number(estimate[key]) is None for key in counts)
                or any(estimate[key] not in ('config', 'default', 'unknown') for key in sources)):
            raise ValueError('invalid_estimate_fields')
        groups = estimate['groups']
        if (not isinstance(groups, list) or len(groups) > len(GROUPS)
                or not all(_valid_group(row) for row in groups)
                or len({row['id'] for row in groups}) != len(groups)):
            raise ValueError('invalid_estimate_groups')
        groups_status = estimate.get('groups_status', estimate['status'])
        if groups_status not in ('available', 'unavailable'):
            raise ValueError('invalid_composition_status')
        if estimate['status'] == 'available':
            if (_number(estimate['estimated_input_tokens']) is None
                    or groups_status == 'available' and estimate['estimated_input_tokens'] != sum(row['tokens'] for row in groups)
                    or groups_status == 'unavailable' and groups):
                raise ValueError('invalid_estimate_total')
        elif (estimate['status'] != 'unavailable' or groups or estimate['estimated_input_tokens'] is not None
                or groups_status != 'unavailable'):
            raise ValueError('invalid_estimate_status')
        usage = value['usage']
        if usage is not None and (not isinstance(usage, dict)
                or not {'input_tokens', 'output_tokens', 'cache_read_tokens'} <= set(usage)
                or set(usage) - {'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'}
                or any(number is not None and _number(number) is None for number in usage.values())):
            raise ValueError('invalid_snapshot_usage')
        if len(json.dumps(value, allow_nan=False).encode('utf-8')) > 16384:
            raise ValueError('invalid_snapshot_size')
        result = deepcopy(value)
        if usage is not None:
            result['usage'].setdefault('cache_write_tokens', None)
        return result
    except (TypeError, ValueError, KeyError):
        logger.warning('saved_context_snapshot_invalid')
        return None


@dataclass(frozen=True)
class ModelContextSnapshotData:
    model_call_id: str
    snapshot_json: str | None

    def __post_init__(self):
        if not isinstance(self.model_call_id, str) or not self.model_call_id or len(self.model_call_id) > 128:
            raise ValueError('Invalid context call identity')
        if self.snapshot_json is None:  # Bounded Web projection may omit this optional payload.
            return
        if not isinstance(self.snapshot_json, str) or len(self.snapshot_json.encode('utf-8')) > 16384:
            raise ValueError('Invalid context snapshot size')
        value = json.loads(self.snapshot_json)
        if read_snapshot(value) is None or value.get('model_call_id') != self.model_call_id:
            raise ValueError('Invalid context snapshot identity')
