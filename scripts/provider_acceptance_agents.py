"""Real review-role responses, child isolation, independent approvals and stop."""

import asyncio
import json
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
from naiyue_test_agent.tools import SubAgentSpec
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permission_rules import PermissionSettings, AutoPermissionSettings
from .provider_acceptance_cases import require, paired_tools, final_text, run_turn, expected_calls, single_line_read
from .provider_acceptance_core import nonce


def review_settings(env):
    return AutoPermissionSettings(fast_model_id=env.model_id, detailed_model_id=env.model_id)


async def review_roles(env):
    decisions, usage = {}, []
    async with env.open_app() as app:
        model = app.host.sessions._sessions._catalog.get(env.model_id)
        reviewer = AutoPermissionReviewer(review_settings(env), model, model)
        data = {'human_messages': [{'sequence': 0, 'id': 'synthetic-human', 'content':
                'Run exactly printf REVIEW_OK in this synthetic workspace; no other changes.'}],
                'project_instructions': 'Synthetic acceptance workspace. Do not access other files.',
                'previous_calls': [], 'current_call': {'id': 'synthetic-call', 'name': 'bash',
                'args': {'command': 'printf REVIEW_OK'}}, 'subject': {'side_effects': 'unknown'}, 'reason': 'default'}
        for stage, cap in (('fast', 256), ('detailed', 2048)):
            response = await model.model_copy(update={'max_tokens': cap}).ainvoke(reviewer._messages(data, stage),
                config={'callbacks': []})
            decisions[stage] = reviewer._decision(response)
            require(decisions[stage] in ('allow', 'ask'), 'valid_review_decision')
            usage.append({'stage': stage, **{key: (response.usage_metadata or {}).get(key)
                for key in ('input_tokens', 'output_tokens', 'total_tokens')}})
    return {**decisions, 'vendor_usage': usage, 'direct_roles_only': True, 'natural_escalation': False}


async def auto_pipeline(env):
    secret, seen, diagnostics, usage = nonce(), [], [], []
    settings = AppSettings(permissions=PermissionSettings(mode='Auto', auto=review_settings(env)))
    async with env.open_app(['read', 'bash'], settings=settings) as app:
        sid = await env.session(app)
        (env.root / 'work' / 'seed.txt').write_text(secret)
        (env.root / 'work' / 'AGENTS.md').write_text('Synthetic acceptance: only run the exact requested printf command.')
        reviewer = app.host.sessions._sessions._permission_reviewer
        reviewer.models = {stage: model.model_copy(update={'max_tokens': cap}) for (stage, model), cap in
                           zip(reviewer.models.items(), (256, 2048), strict=True)}
        messages_method, diagnostic = reviewer._messages, reviewer._diagnostic
        def capture(data, stage):
            require(secret not in json.dumps(data), 'review_excludes_prior_tool_content')
            require(bool(data['human_messages']) and bool(data['project_instructions']), 'review_human_and_project')
            require(any(call['name'] == 'read' for call in data['previous_calls']), 'review_proposed_history')
            seen.append(stage)
            return messages_method(data, stage)
        def capture_diagnostic(context, stage, decision, reason, response=None, error=None):
            diagnostics.append({'stage': stage, 'decision': decision, 'reason': reason})
            if response is not None:
                usage.append({'stage': stage, **{key: (response.usage_metadata or {}).get(key)
                    for key in ('input_tokens', 'output_tokens', 'total_tokens')}})
            diagnostic(context, stage, decision, reason, response, error)
        with patch.object(reviewer, '_messages', capture), patch.object(reviewer, '_diagnostic', capture_diagnostic):
            first = await app.host.runs.start(sid, [('user', 'First call read on seed.txt. After seeing its result, '
                'call bash exactly once with command "printf AUTO_OK". This is an authorized synthetic '
                'test of that exact command, with no file or network changes. Reply exactly AUTO_OK.')])
            expected_calls(first['messages'], [('read', {'path': 'seed.txt'}), ('bash', {'command': 'printf AUTO_OK'})], env.root / 'work')
            if first.get('approval_requests'):
                # A valid Ask is a successful review contract, resolved by the harness as the human.
                first = await app.host.runs.resume_approval(sid, ['approve'])
        paired_tools(first['messages'], ['read', 'bash'])
        require(final_text(first['messages']) == 'AUTO_OK', 'auto_task_answer')
        require(bool(seen) and bool(diagnostics), 'auto_review_observed')
        require(all(row['reason'] == 'valid' for row in diagnostics), 'auto_valid_response')
    return {'input_isolation': True, 'project_instructions': True, 'reviews': diagnostics, 'vendor_usage': usage,
            'natural_escalation': any(row['stage'] == 'detailed' for row in diagnostics)}


def child_settings(env):
    return AppSettings(subagents=(SubAgentSpec('tester', 'Read the requested synthetic file and report its contents.',
        'Read exactly the path in your task once. Return only its exact file contents, excluding read pagination metadata; do not call any other tool. '
        'If refused, report the refusal and do not retry.', ('read',), model_id=env.model_id, max_model_calls=2),))


async def child_normal(env):
    expected = nonce()
    async with env.open_app(['subagent', 'read'], settings=child_settings(env), initial=['subagent']) as app:
        sid = await env.session(app)
        (env.root / 'work' / 'seed.txt').write_text(expected)
        messages = await run_turn(app, sid, 'Delegate exactly once to tester: read seed.txt once and return '
                                  'its exact contents. Reply only with that returned value.')
        result, = paired_tools(messages, ['subagent'])
        require(result.artifact['status'] == 'completed', 'child_artifact_status')
        require(final_text(messages) == expected, 'child_report_answer')
        records = await app.host.history.subagents(sid)
        require(len(records) == 1, 'one_child_record')
        child = await app.host.history.subagent(sid, records[0]['agent_id'])
        require(child['available'] and child['agent_path'].startswith('root/tester/'), 'child_history_scope')
        tool, = paired_tools(child['messages'], ['read'])
        single_line_read(tool.content, expected)
    return {'child_read': True, 'root_artifact': True, 'saved_child_history': True}


async def wait_pending(app, sid, task, minimum=1):
    # Poll application state, never assume the network/model finishes inside a small sleep.
    while not task.done():
        batches = app.host.runs.pending_subagents(sid)
        if len(batches) >= minimum:
            return batches
        await asyncio.sleep(0.05)
    await task
    require(False, 'expected_child_approval')


async def submit(app, sid, batch, expected_target):
    require(len(batch['items']) == 1 and batch['items'][0]['tool_name'] == 'read'
            and batch['items'][0]['target'] == str(expected_target), 'child_expected_approval')
    return await app.host.runs.submit_subagent_approval(sid, approval_batch_id=batch['approval_batch_id'],
        origin_run_id=batch['origin_run_id'], decisions=[{'approval_id': item['approval_id'], 'decision': 'approve'}
                                                      for item in batch['items']])


async def siblings(env):
    left, right = env.root / 'left.txt', env.root / 'right.txt'
    left.write_text('LEFT_OK')
    right.write_text('RIGHT_OK')
    async with env.open_app(['subagent', 'read'], settings=child_settings(env), initial=['subagent']) as app:
        sid = await env.session(app)
        task = asyncio.create_task(app.host.runs.start(sid, [('user', 'In ONE tool batch delegate two independent tester tasks: '
            f'first read {left} once; second read {right} once. After both reports reply exactly SIBLINGS_OK.')]))
        try:
            pending = await wait_pending(app, sid, task, minimum=2)
            require(len(pending) == 2, 'both_siblings_pending')
            require({item['target'] for batch in pending for item in batch['items']} == {str(left), str(right)}, 'sibling_expected_targets')
            parents = [m for m in await app.host.history.messages(sid) if getattr(m, 'tool_calls', None)]
            require(len(parents) == 1 and len(parents[0].tool_calls) == 2, 'one_parent_tool_batch')
            first = pending[0]
            first_target = first['items'][0]['target']
            await submit(app, sid, first, first_target)
            # Resolve the other child independently, without resuming or replaying the parent.
            while True:
                remaining = [batch for batch in await wait_pending(app, sid, task)
                             if batch['approval_batch_id'] != first['approval_batch_id']]
                if remaining:
                    break
                await asyncio.sleep(0.05)
            second = remaining[0]
            require(first['agent_path'] != second['agent_path'], 'sibling_distinct_paths')
            instance = app.host.sessions._sessions.require(sid).subagents.subagent_status(first['agent_path'])
            while not instance['settled']:
                await asyncio.sleep(0.05)
                instance = app.host.sessions._sessions.require(sid).subagents.subagent_status(first['agent_path'])
            require(instance['status'] == 'completed' and not task.done(), 'first_finishes_while_second_waits')
            require(any(batch['approval_batch_id'] == second['approval_batch_id']
                        for batch in app.host.runs.pending_subagents(sid)), 'second_remains_pending')
            await submit(app, sid, second, str(right) if first_target == str(left) else str(left))
            result = await task
            paired_tools(result['messages'], ['subagent', 'subagent'])
            require(final_text(result['messages']) == 'SIBLINGS_OK', 'sibling_reports')
            require(not app.host.runs.pending_subagents(sid), 'sibling_approvals_cleared')
            records = await app.host.history.subagents(sid)
            require(len(records) == 2 and all(row['status'] == 'completed' for row in records), 'sibling_history')
        finally:
            if not task.done():
                app.host.runs.stop(sid)
            await asyncio.gather(task, return_exceptions=True)
    return {'two_scopes': True, 'independent_approvals': True, 'completed_children': 2}


async def child_stop(env):
    outside = env.root / 'outside.txt'
    outside.write_text(nonce())
    async with env.open_app(['subagent', 'read'], settings=child_settings(env), initial=['subagent']) as app:
        sid = await env.session(app)
        task = asyncio.create_task(app.host.runs.start(sid, [('user', f'Delegate exactly once to tester: read {outside}. '
            'The task may be stopped. Do not redelegate. After it returns reply exactly CHILD_STOPPED.')]))
        try:
            batch = (await wait_pending(app, sid, task))[0]
            app.host.runs.stop(sid, agent_path=batch['agent_path'])
            result = await task
            feedback, = paired_tools(result['messages'], ['subagent'], statuses=['error'])
            require('stopped' in feedback.content.lower(), 'child_stop_feedback')
            records = await app.host.history.subagents(sid)
            require(len(records) == 1 and records[0]['status'] == 'stopped', 'child_stop_checkpoint')
            require(not app.host.runs.pending_subagents(sid), 'stopped_child_cleared')
            require(final_text(result['messages']) == 'CHILD_STOPPED', 'parent_continues_after_child_stop')
        finally:
            if not task.done():
                app.host.runs.stop(sid)
            await asyncio.gather(task, return_exceptions=True)
    return {'child_stop': True, 'parent_continues': True, 'no_external_read': True}


def agents_plan(*, diagnose=False):
    cases = [('review_roles', 2, review_roles), ('auto_pipeline', 5, auto_pipeline),
        ('child_normal', 4, child_normal), ('child_siblings', 6, siblings)]
    if diagnose:
        from .provider_acceptance_multicall import multicall_diagnostic, nonstream_tool_diagnostic
        cases.append(('multicall_diagnostic', 1, multicall_diagnostic))
        cases.append(('nonstream_tool_diagnostic', 1, nonstream_tool_diagnostic))
    return [('100D', 24, cases + [('child_stop', 3, child_stop)])]
