"""Numeric-only evidence for Spec143BL's independent acceptance probe."""
import json
import logging
from statistics import mean, median


def positive(value):
    return type(value) is int and value > 0


class Measurements(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows, self.pending, self.finished = [], {}, set()
        self.scenario = None
        self.budget_checks = []
        self.operations = []
        self.summary_failed = False
        self.tool_deviation = False
        self.tool_calls = 0

    def summary(self, payload):
        if payload.get('event') == 'context_summary_model_finished' and payload.get('error_type') is not None:
            self.summary_failed = True
        key = ('summary', payload.get('sample_id'))
        if key[1] is None or key in self.finished:
            return
        if payload.get('event') == 'context_summary_model_started':
            self.pending[key] = {'id': key[1], 'purpose': 'summary', 'scenario': self.scenario,
                'E': payload.get('raw_input_tokens'), 'C': payload.get('calibrated_input_tokens'),
                'method': payload.get('method', 'not_reported')}
        elif payload.get('event') == 'context_summary_model_finished':
            row = self.pending.pop(key, {'id': key[1], 'purpose': 'summary', 'scenario': self.scenario})
            row.update(A=payload.get('input_tokens'), status=('returned' if payload.get('error_type') is None else 'failed') if 'E' in row and 'C' in row else 'incomplete')
            self.rows.append(row)
            self.finished.add(key)

    def emit(self, record):
        preflight = getattr(record, 'model_context_preflight', None)
        if isinstance(preflight, dict):
            self.budget_checks.append({key: preflight.get(key) for key in (
                'estimated_input_tokens', 'calibrated_input_tokens', 'trigger_threshold_tokens',
                'context_window_tokens', 'max_output_tokens', 'needs_compaction', 'method')})
        payload = getattr(record, 'context_compaction', None)
        if isinstance(payload, dict):
            self.summary(payload)
            if payload.get('event') in ('context_compaction_started', 'context_compaction_completed'):
                self.budget_checks.append({key: payload.get(key) for key in ('event', 'trigger',
                    'estimated_input_tokens', 'estimated_post_compaction_input_tokens')})

    def event(self, event):
        if event.event_type == 'tool_call_started':
            self.tool_calls += 1
            if self.scenario != 'C3' or event.data.tool_name != 'read' or self.tool_calls != 1:
                self.tool_deviation = True
        if event.event_type == 'tool_call_finished' and event.data.outcome != 'success':
            self.tool_deviation = True
        if event.event_type in ('compaction_finished', 'context_recovery_finished'):
            from naiyue_test_agent.agent_runtime.context_events import safe_context_reason
            self.operations.append({'event': event.event_type, 'status': event.data.status,
                'reason': safe_context_reason(event.data.reason) if event.data.reason else None,
                'before': event.data.before_estimate, 'after': event.data.after_estimate,
                'retry_attempts': event.data.retry_attempts,
                'model_call_id': event.data.model_call_id, 'run_id': getattr(event, 'run_id', None)})
        if event.event_type != 'model_context_snapshot':
            return
        snapshot = json.loads(event.data.snapshot_json)
        if snapshot['status'] == 'running':
            return
        key = ('agent', snapshot['model_call_id'], event.sequence)
        if key in self.finished:
            return
        estimate = snapshot['estimate']
        calibration = estimate.get('calibration', {})
        self.rows.append({'id': snapshot['model_call_id'], 'purpose': 'agent',
            'scenario': self.scenario, 'status': snapshot['status'], 'run_id': snapshot.get('run_id'),
            'E': estimate['estimated_input_tokens'], 'C': calibration.get('calibrated_input_tokens'),
            'A': (snapshot.get('usage') or {}).get('input_tokens'), 'method': calibration.get('method')})
        self.finished.add(key)

    def recovered_overflow(self, row):
        if row.get('purpose') != 'agent' or row.get('status') != 'failed':
            return False
        if not row.get('run_id') or not row.get('id'):
            return False
        attempts = [r for r in self.rows if r.get('purpose') == 'agent'
            and (r.get('run_id'), r.get('id')) == (row['run_id'], row['id'])]
        recovered = [o for o in self.operations if o['event'] == 'context_recovery_finished'
            and (o.get('run_id'), o.get('model_call_id')) == (row['run_id'], row['id'])]
        return ([r['status'] for r in attempts] == ['failed', 'returned']
            and all(positive(attempts[1].get(k)) for k in ('E', 'C', 'A'))
            and len(recovered) == 1 and recovered[0]['status'] == 'recovered'
            and recovered[0]['retry_attempts'] == 1)

    def incomplete(self):
        return [{**row, 'status': 'incomplete', 'A': None} for row in self.pending.values()]


def metrics(rows):
    valid = [row for row in rows if row.get('purpose') == 'agent' and row.get('status') == 'returned'
             and all(positive(row.get(key)) for key in ('E', 'C', 'A'))]
    calibrated = [row for row in valid if row.get('method') == 'ratio_estimate']
    result = {'valid_count': len(valid), 'calibrated_count': len(calibrated),
              'excluded_count': len(rows) - len(calibrated), 'improvement': False}
    returned = [r for r in rows if r.get('purpose') == 'agent' and r.get('status') == 'returned']
    result['usage_availability'] = sum(positive(r.get('A')) for r in returned) / len(returned) if returned else None
    result['calibration_coverage'] = len(calibrated) / len(valid) if valid else None
    result['exclusions'] = {
        'summary': sum(r.get('purpose') == 'summary' for r in rows),
        'incomplete': sum(r.get('status') == 'incomplete' for r in rows),
        'failed': sum(r.get('status') == 'failed' for r in rows),
        'invalid_usage': sum(not positive(r.get('A')) for r in returned),
        'uncalibrated': sum(r.get('method') != 'ratio_estimate' for r in valid)}
    for name, field in (('raw', 'E'), ('calibrated', 'C')):
        errors = [abs(row[field] - row['A']) / row['A'] for row in calibrated]
        result[name] = ({'mean': mean(errors), 'median': median(errors), 'max': max(errors),
            'underestimate_rate': sum(row[field] < row['A'] for row in calibrated) / len(calibrated)} if errors else None)
    if len(calibrated) >= 3:
        result['improvement'] = (result['calibrated']['median'] < result['raw']['median']
            and result['calibrated']['underestimate_rate'] <= result['raw']['underestimate_rate'])
    return result
