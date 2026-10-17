"""Bounded, redacted comparison of a fixed fact-recall task with compaction."""

import argparse

from naiyue_test_agent.config_paths import get_models_path
import json
import logging
import os
import subprocess
import sys
import time

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent import application
from naiyue_test_agent.model_access import (
    COMPACTION_KEEP_RECENT_METADATA_KEY, COMPACTION_RESERVE_METADATA_KEY,
    COMPACTION_SUMMARY_OUTPUT_METADATA_KEY, MODEL_LIMITS_METADATA_KEY,
)


EXPECTED_FACTS = {
    'endpoint': 'GET /api/v1/coupons/{user_id}',
    'priority': 'blacklist_before_stock',
    'status': 403,
    'case_id': 'CASE-ALPHA-17',
}
EVAL_SYSTEM = 'Return only a JSON object with the four keys requested by the user.'
REPORTED_EVENTS = {
    'context_compaction_started', 'context_compaction_completed',
    'context_compaction_failed', 'context_summary_model_started',
    'context_summary_model_finished',
}


def case_messages() -> list:
    """One fixed synthetic history; neither variant needs warm-up model calls."""
    first = (
        'Synthetic test contract. Endpoint: GET /api/v1/coupons/{user_id}. '
        'Precedence: blacklist_before_stock. Expected HTTP status: 403. '
        'Case ID: CASE-ALPHA-17. All four values must be retained exactly.\n'
        + ('Archived unrelated diagnostic line.\n' * 500)
    )
    later = 'Later work did not change that contract.\n' + (
        'Unrelated progress note.\n' * 140
    )
    question = (
        'From the earliest test contract, return JSON with keys endpoint, priority, '
        'status, case_id. Use the exact original values; status must be a number. '
        'Do not guess missing values.'
    )
    return [HumanMessage(content=first), AIMessage(content='Contract noted.'),
            HumanMessage(content=later), AIMessage(content='Progress noted.'),
            HumanMessage(content=question)]


def score_answer(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    parse_ok = isinstance(parsed, dict)
    matches = {key: parse_ok and parsed.get(key) == value
               for key, value in EXPECTED_FACTS.items()}
    return {'parse_ok': parse_ok, 'correct_count': sum(matches.values()),
            'matches': matches}


def _last_event(output: str | bytes | None) -> str | None:
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    last = None
    for line in (output or '').splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get('event'), str):
            last = item['event']
    return last


def run_bounded_worker(command: list[str], timeout_seconds: float, *, popen=None) -> dict:
    """Bound a child process even if a synchronous SDK call does not return."""
    launch = popen or subprocess.Popen
    worker = launch(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True)
    try:
        output, _ = worker.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        partial = error.output or b''
        worker.terminate()
        try:
            remaining, _ = worker.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            worker.kill()
            remaining, _ = worker.communicate()
        return {'status': 'timeout',
                'last_event': _last_event(remaining) or _last_event(partial)}
    lines = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get('event'), str):
            lines.append(item)
    result = next((item for item in reversed(lines) if item['event'] in
                   ('case_result', 'case_failed')), None)
    if result is None:
        return {'status': 'failed', 'last_event': _last_event(output),
                'error_type': 'MissingCaseResult'}
    return {'status': ('returned' if worker.returncode == 0 else 'failed'),
            'last_event': _last_event(output), 'result': result}


class _SafeEventHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.compaction_events: list[dict] = []

    def emit(self, record):
        compaction = getattr(record, 'context_compaction', None)
        if isinstance(compaction, dict) and compaction.get('event') in REPORTED_EVENTS:
            event = compaction['event']
            self.compaction_events.append(compaction)
            safe = {'event': event}
            for key in ('outcome', 'reason', 'duration_ms', 'input_tokens',
                        'output_tokens', 'summary_input_tokens_estimate'):
                if key in compaction:
                    safe[key] = compaction[key]
            print(json.dumps(safe, sort_keys=True), flush=True)
        elif hasattr(record, 'model_context_preflight'):
            print(json.dumps({'event': 'answer_model_started'}), flush=True)
        elif hasattr(record, 'model_request_context'):
            print(json.dumps({'event': 'answer_model_finished'}), flush=True)


def _run_case(args) -> int:
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    handler = _SafeEventHandler()
    runtime_logger = logging.getLogger('naiyue_test_agent.agent_runtime')
    runtime_logger.addHandler(handler)
    runtime_logger.setLevel(logging.INFO)
    print(json.dumps({'event': 'case_started', 'mode': args.worker}), flush=True)
    context_operations = []
    try:
        app = application.build_local_application(
            args.config, tools=[], system_prompt=EVAL_SYSTEM, response_format=None,
        )
        def observe_context(event):
            if event.event_type in ('compaction_started', 'compaction_finished',
                                    'context_recovery_started', 'context_recovery_finished'):
                context_operations.append(event)
                print(json.dumps({'event': event.event_type,
                                  'status': getattr(event.data, 'status', None),
                                  'visibility': getattr(event.data, 'visibility', None)}, sort_keys=True), flush=True)

        app.subscribe_events(observe_context)
        model_id = args.model_id or app.default_model_id
        model = app._sessions._catalog.get(model_id)
        metadata = dict(model.metadata or {})
        limits = dict(metadata[MODEL_LIMITS_METADATA_KEY])
        output_cap = limits['max_output_tokens']
        if args.summary_output_tokens > output_cap:
            raise ValueError('Summary cap exceeds the configured model output limit')
        messages = case_messages()
        estimated = count_tokens_approximately(
            [HumanMessage(content=EVAL_SYSTEM), *messages], tools=[],
        )
        if args.worker == 'compacted':
            limits['context_window_tokens'] = max(
                output_cap + args.summary_output_tokens + 1024,
                estimated + args.summary_output_tokens + 1024,
            )
            metadata[COMPACTION_KEEP_RECENT_METADATA_KEY] = {
                'tokens': 500, 'source': 'probe',
            }
            metadata[COMPACTION_RESERVE_METADATA_KEY] = {
                'tokens': max(16384, args.summary_output_tokens), 'source': 'probe',
            }
        else:
            limits['context_window_tokens'] = max(
                limits['context_window_tokens'], estimated + output_cap + 1024,
            )
        limits['context_window_source'] = 'probe'
        metadata[MODEL_LIMITS_METADATA_KEY] = limits
        metadata[COMPACTION_SUMMARY_OUTPUT_METADATA_KEY] = {
            'tokens': args.summary_output_tokens, 'source': 'probe',
        }
        model.metadata = metadata

        session = app.create_session(model_id)
        started = time.monotonic()
        result = app.run(session, messages)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        answers = [message for message in result['messages']
                   if isinstance(message, AIMessage)]
        if not answers:
            raise RuntimeError('No answer AIMessage')
        summary_events = [event for event in handler.compaction_events
                          if event['event'] == 'context_summary_model_finished']
        summary_returned = any(event.get('outcome') == 'returned'
                               for event in summary_events)
        if summary_returned != (args.worker == 'compacted'):
            raise RuntimeError('Unexpected compaction event sequence')
        state_confirmed = False
        if args.worker == 'compacted':
            if ([e.event_type for e in context_operations] != ['compaction_started', 'compaction_finished']
                    or context_operations[-1].data.status != 'applied'
                    or context_operations[-1].data.summary_message_id != result['messages'][0].id
                    or context_operations[-1].data.visibility != 'graph_values'):
                raise RuntimeError('Compaction state confirmation missing')
            state_confirmed = True
        elif context_operations:
            raise RuntimeError('Unexpected baseline context operations')
        usage = answers[-1].usage_metadata or {}
        summary_usage = next((event for event in summary_events
                              if event.get('outcome') == 'returned'), {})
        score = score_answer(answers[-1].text)
        print(json.dumps({
            'event': 'case_result', 'mode': args.worker,
            'elapsed_ms': elapsed_ms, 'summary_returned': summary_returned,
            'compaction_state_confirmed': state_confirmed,
            'summary_duration_ms': summary_usage.get('duration_ms'),
            'summary_input_tokens': summary_usage.get('input_tokens'),
            'summary_output_tokens': summary_usage.get('output_tokens'),
            'answer_input_tokens': usage.get('input_tokens'),
            'answer_output_tokens': usage.get('output_tokens'),
            **score,
        }, sort_keys=True), flush=True)
        return 0 if score['correct_count'] == len(EXPECTED_FACTS) else 1
    except Exception as error:
        context_failure_reason = next((event.data.reason for event in reversed(context_operations)
                                       if event.event_type == 'compaction_finished'
                                       and event.data.status == 'failed'), None)
        print(json.dumps({'event': 'case_failed', 'mode': args.worker,
                          'error_type': type(error).__name__,
                          'context_failure_reason': context_failure_reason}), flush=True)
        return 1
    finally:
        runtime_logger.removeHandler(handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id')
    parser.add_argument('--summary-output-tokens', type=int, default=2048)
    parser.add_argument('--total-timeout-seconds', type=float, default=120)
    parser.add_argument('--worker', choices=('compacted', 'baseline'),
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.summary_output_tokens <= 0 or args.total_timeout_seconds <= 0:
        parser.error('summary output and timeout must be positive')
    if args.worker:
        return _run_case(args)

    deadline = time.monotonic() + args.total_timeout_seconds
    all_passed = True
    for mode in ('compacted', 'baseline'):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(json.dumps({'mode': mode, 'status': 'deadline_exhausted'}))
            return 1
        command = [sys.executable, '-m', 'scripts.probe_compaction_quality',
                   '--worker', mode, '--config', args.config,
                   '--summary-output-tokens', str(args.summary_output_tokens)]
        if args.model_id:
            command.extend(['--model-id', args.model_id])
        result = run_bounded_worker(command,
                                    min(remaining, args.total_timeout_seconds / 2))
        print(json.dumps({'mode': mode, **result}, sort_keys=True), flush=True)
        if result['status'] != 'returned':
            all_passed = False
    return 0 if all_passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
