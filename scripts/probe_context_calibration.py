"""Bounded calibration acceptance, using the normal application runtime."""
import argparse
import asyncio
from contextlib import asynccontextmanager, ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from naiyue_test_agent import application
from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.builtin import create_builtin_tools
from .component_fixtures import open_component_application
from .context_calibration_report import Measurements, metrics, positive
from .provider_acceptance_budget import AcceptanceBudgetStop, RequestBudget, guard_catalog
from .provider_acceptance_scope import FixturePermission
from .provider_acceptance_cases import run_turn, expected_calls, paired_tools


SCENARIOS = ('C1', 'C2', 'C3', 'C4', 'C5', 'C6')
FACT = 'CALIBRATION_FACT_731'
TEXT = f'固定事实编号：{FACT}。' + '请检查接口测试失败的原因，保留请求编号、错误信息和用户约束。' * 300
JSON_TEXT = '\n'.join(json.dumps({'case_id': i, 'status': '待核验 pending', 'expected_http': 200}, ensure_ascii=False) for i in range(150))
FILE_TEXT = '\n'.join(f'record={i}; result=待检查 pending; owner=fixture' for i in range(200))


class ProbeBudget(RequestBudget):
    def __init__(self, limit, measurements):
        super().__init__(limit)
        self.measurements = measurements

    def reserve(self, model_id):
        if self.measurements.tool_deviation:
            raise AcceptanceBudgetStop('calibration_tool_deviation')
        if self.measurements.summary_failed:
            raise AcceptanceBudgetStop('calibration_summary_failed')
        super().reserve(model_id)


class NotCovered(Exception):
    pass


class ReadOnce(FixturePermission):
    def __init__(self, original, root, ledger):
        super().__init__('read', original, root)
        self.ledger = ledger

    def prepare(self, args, context):
        if self.ledger[0] or Path(args['path']).name != 'calibration.txt':
            from .provider_acceptance_scope import FixtureBoundaryStop
            raise FixtureBoundaryStop('calibration_read_deviation')
        self.ledger[0] += 1
        return super().prepare(args, context)


async def execute(config, model_id, root, budget, measurements, report, save):
    settings, reads = AppSettings(storage={'data_dir': root / 'data'}), [0]
    work = root / 'work'
    work.mkdir()
    (work / 'calibration.txt').write_text(FILE_TEXT, encoding='utf-8')
    specs = [replace(spec, permissions=ReadOnce(spec.permissions, work, reads))
        for spec in create_builtin_tools(settings.tools, process_settings=settings.process,
            shell_task_settings=settings.shell_tasks, progress_settings=settings.progress,
            retry_settings=settings.tool_retry) if spec.tool.name == 'read']

    @asynccontextmanager
    async def opened():
        catalog = load_model_catalog(config)
        try:
            model = catalog.get(model_id)
            catalog._models = {model_id: model}
            catalog.default_model_id = model_id
            guard_catalog(catalog, budget)
            report['api'] = (model.metadata or {}).get('naiyue_model_input', {}).get('api', 'adapter_default')
            report['model_metadata'] = {key: value for key, value in (model.metadata or {}).items()
                if key in ('naiyue_model_limits', 'naiyue_compaction_reserve', 'naiyue_compaction_keep_recent', 'naiyue_compaction_summary_output')}
            with patch.object(application, 'load_model_catalog', lambda _: catalog):
                async with open_component_application(config, tools=specs, initial_active_tools=[], settings=settings,
                        database_path=root / 'state.sqlite', system_prompt='Follow the fixed task. Only use read when requested. Reply ACK otherwise.',
                        response_format=None) as app:
                    unsubscribe = app.host.observability.subscribe(measurements.event)
                    try:
                        yield app
                    finally:
                        unsubscribe()
        finally:
            await catalog.aclose()

    async def scenario(name, operation):
        measurements.scenario = name
        row = report['scenarios'][SCENARIOS.index(name)]
        row['status'] = 'running'
        save()
        start = monotonic()
        try:
            await operation()
        except BaseException:
            row['status'] = 'failed'
            raise
        else:
            row['status'] = 'completed'
        finally:
            row['duration_seconds'] = monotonic() - start
            save()

    async with opened() as app:
        sid = await app.host.sessions.create_workspace(model_id, work)
        await scenario('C1', lambda: run_turn(app, sid, TEXT + '\nReply only ACK.'))
        await scenario('C2', lambda: run_turn(app, sid, JSON_TEXT + '\nReply only ACK.'))
        async def tools():
            await app.host.sessions.select_tools(sid, ['read'])
            before = await app.host.history.messages(sid)
            await run_turn(app, sid, 'Read calibration.txt exactly once, then reply only ACK.')
            messages = (await app.host.history.messages(sid))[len(before):]
            expected_calls(messages, [('read', {'path': 'calibration.txt'})], work)
            paired_tools(messages, ['read'])
        await scenario('C3', tools)
        async def compact():
            result = await app.host.runs.compact(sid)
            report['compaction_status'] = result.get('compaction_status')
            reason = measurements.operations[-1]['reason'] if measurements.operations else None
            uncovered = reason in ('no_safe_cut', 'no_safe_cut_within_send_budget',
                'no_new_history_to_summarize', 'summary_input_exceeds_budget',
                'compacted_request_over_budget', 'compaction_not_reducing', 'compaction_unavailable')
            if measurements.summary_failed or result.get('compaction_status') == 'failed' and not uncovered:
                raise RuntimeError('calibration_compaction_failed')
            if result.get('compaction_status') != 'applied':
                raise NotCovered()
            saved = await app.host.history.messages(sid)
            report['quality_checks'] = {'fixed_fact_retained': FACT in saved[0].content}
            if not report['quality_checks']['fixed_fact_retained']:
                raise AssertionError('fixed_fact_lost')
        await scenario('C4', compact)
        await scenario('C5', lambda: run_turn(app, sid, 'Reply only ACK. Do not read files.'))
        saved = await app.host.history.messages(sid)
    async with opened() as app:
        async def reopen():
            assert await app.host.sessions.restore(sid) == sid
            assert await app.host.history.messages(sid) == saved
            await run_turn(app, sid, 'Reply only ACK. Do not read files.')
        await scenario('C6', reopen)


async def run_probe(config, model_id, report_path, *, live=False, request_limit=12, total_timeout_seconds=600):
    if type(request_limit) is not int or not 1 <= request_limit <= 12:
        raise ValueError('request_limit must be an integer from 1 to 12')
    if type(total_timeout_seconds) not in (int, float) or not math.isfinite(total_timeout_seconds) or total_timeout_seconds <= 0:
        raise ValueError('timeout must be positive and finite')
    measurements = Measurements()
    budget = ProbeBudget(request_limit, measurements)
    report = {'spec': '143BL-04', 'fixture_version': 1, 'status': 'blocked', 'reason': 'live_required',
        'sdk_retries': 0, 'started': datetime.now(timezone.utc).isoformat(),
        'scenarios': [{'name': name, 'status': 'not_run'} for name in SCENARIOS],
        'fixtures': [{'name': name, 'characters': len(text), 'sha256': hashlib.sha256(text.encode()).hexdigest()}
            for name, text in [('text', TEXT), ('json', JSON_TEXT), ('file', FILE_TEXT)]]}
    with Path(report_path).open('x', encoding='utf-8') as output:
        def save():
            report.update(http=budget.snapshot(), measurements=measurements.rows + measurements.incomplete(),
                          metrics=metrics(measurements.rows), budget_checks=measurements.budget_checks,
                          operations=measurements.operations)
            output.seek(0)
            json.dump(report, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.truncate()
            output.flush()
            os.fsync(output.fileno())
        save()
        if not live:
            return 2
        # Validate the selected owned client before any application or model work.
        catalog = None
        try:
            catalog = load_model_catalog(config)
            model = catalog.get(model_id)
            catalog._models = {model_id: model}
            guard_catalog(catalog, budget)
        except Exception:
            report['reason'] = 'model_or_guard_unavailable'
            save()
            return 2
        finally:
            if catalog is not None:
                await catalog.aclose()
        report.update(status='running', reason=None, model_id=model_id,
            source_sha256=source_hash(), code_sha=subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip())
        logger = logging.getLogger('naiyue_test_agent.agent_runtime.context_compaction')
        preflight_logger = logging.getLogger('naiyue_test_agent.agent_runtime.context_observation')
        preflight_level = preflight_logger.level
        preflight_logger.addHandler(measurements)
        preflight_logger.setLevel(logging.INFO)
        logger.addHandler(measurements)
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            with measurement_timing(report), TemporaryDirectory(prefix='naiyue-calibration-') as directory:
                async with asyncio.timeout(total_timeout_seconds):
                    await execute(config, model_id, Path(directory), budget, measurements, report, save)
            if measurements.tool_deviation:
                raise RuntimeError('calibration_tool_deviation')
            if measurements.summary_failed:
                raise RuntimeError('calibration_summary_failed')
            if not calibration_contract(measurements.rows):
                report.update(status='failed', reason='calibration_contract_failed')
                return 1
            enough = (metrics(measurements.rows)['calibrated_count'] >= 3
                and all(s['status'] == 'completed' for s in report['scenarios'])
                and sum(r.get('scenario') == 'C3' and r.get('purpose') == 'agent' for r in measurements.rows) >= 2
                and not measurements.incomplete()
                and all(measurements.recovered_overflow(row) or row.get('status') == 'returned'
                    and all(positive(row.get(k)) for k in ('E', 'C', 'A')) for row in measurements.rows)
                and all(any(r.get('scenario') == name and r.get('purpose') == purpose for r in measurements.rows)
                    for name, purpose in [('C1', 'agent'), ('C2', 'agent'), ('C3', 'agent'), ('C4', 'summary'), ('C5', 'agent'), ('C6', 'agent')]))
            report.update(status='completed' if enough else 'incomplete', reason=None if enough else 'usage_insufficient')
            return 0 if enough else 3
        except asyncio.CancelledError:
            report.update(status='cancelled', reason='cancelled')
            raise
        except NotCovered:
            report.update(status='incomplete', reason='compaction_not_covered')
            return 3
        except (Exception, BaseExceptionGroup):
            report.update(status='failed', reason='execution_failed')
            return 1
        finally:
            preflight_logger.removeHandler(measurements)
            preflight_logger.setLevel(preflight_level)
            logger.removeHandler(measurements)
            logger.setLevel(old_level)
            report['ended'] = datetime.now(timezone.utc).isoformat()
            save()


def calibration_contract(rows):
    latest = {'agent': None, 'summary': None}
    for row in rows:
        if row.get('status') != 'returned' or not all(positive(row.get(k)) for k in ('E', 'C', 'A')):
            continue
        purpose = row.get('purpose')
        if purpose not in latest:
            return False
        previous = latest[purpose]
        method = 'ratio_estimate'
        if previous is None and purpose == 'summary':
            previous = latest['agent']
            method = 'inherited_agent_ratio'
        if previous is None:
            if row.get('method') != 'uncalibrated' or row['C'] != row['E']:
                return False
        elif (row.get('method') != method or row['C'] !=
              (previous['A'] * row['E'] + previous['E'] - 1) // previous['E']):
            return False
        latest[purpose] = row
    return True


from contextlib import contextmanager


@contextmanager
def measurement_timing(report):
    from naiyue_test_agent.agent_runtime import context_calibration
    original = context_calibration.measure_request
    timings = report.setdefault('measurement_seconds', [])
    def timed(*args, **kwargs):
        started = monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            timings.append(monotonic() - started)
    with ExitStack() as stack:
        for module in ('model_input', 'context_compaction', 'context_observation'):
            stack.enter_context(patch(f'naiyue_test_agent.agent_runtime.{module}.measure_request', timed))
        yield


def source_hash():
    digest = hashlib.sha256()
    for base in ('naiyue_test_agent', 'scripts'):
        for path in sorted(Path(base).rglob('*.py')):
            digest.update(str(path).encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--request-limit', type=int, default=12)
    parser.add_argument('--total-timeout-seconds', type=float, default=600)
    args = parser.parse_args()
    return asyncio.run(run_probe(args.config, args.model_id, args.report, live=args.live,
        request_limit=args.request_limit, total_timeout_seconds=args.total_timeout_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
