"""Responses, built-in files and root permissions against disposable fixtures."""

from uuid import uuid4

from naiyue_test_agent.application.conversation.stream_projection import StreamingMessageProjection
from naiyue_test_agent.model_access.messages import is_image_block
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permission_rules import PermissionRule, PermissionSettings
from .provider_acceptance_cases import require, paired_tools, final_text, run_turn, expected_calls, single_line_read
from .provider_acceptance_preflight import create_image_fixture


def nonce():
    return uuid4().hex[:12].upper()


async def text_stream(env):
    expected = nonce()
    async with env.open_app() as app:
        sid = await env.session(app)
        projection, events = StreamingMessageProjection(), []
        async for event in app.host.runs.stream(sid, [('user', f'Reply with exactly {expected}.')]):
            events.append(event)
            projection(event)
        messages = await app.host.history.messages(sid)
        require(final_text(messages) == expected, 'exact_stream_answer')
        deltas = [event for event in events if event.event_type == 'message_delta'
                  and event.data.message_id == messages[-1].id]
        require(bool(deltas), 'stream_deltas')
        require(''.join(event.data.text for event in deltas).strip() == expected, 'stream_delta_text')
        completed = [event for event in events if event.event_type == 'message_completed'
                     and event.data.message_id == messages[-1].id]
        require(len(completed) == 1 and completed[0].sequence > deltas[-1].sequence, 'stream_snapshot_order')
        require(events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed', 'stream_terminal')
        visible = [row for row in projection.messages if row['id'] == messages[-1].id]
        require(len(visible) == 1 and not visible[0]['provisional'], 'stream_projection')
        view = await app.host.history.context(sid)
        require(isinstance(view, dict), 'context_view_available')
    return {'exact_answer': True, 'stream_deltas': len(deltas), 'projection': True}


async def read_text(env):
    expected = nonce()
    async with env.open_app(['read']) as app:
        sid = await env.session(app)
        (env.root / 'work' / 'seed.txt').write_text(expected, encoding='utf-8')
        messages = await run_turn(app, sid, 'Call read exactly once with path seed.txt, offset=null, limit=null. '
                                  'Reply only with the exact file contents. Do not guess.')
        results = paired_tools(messages, ['read'])
        single_line_read(results[0].content, expected)
        require(final_text(messages) == expected, 'exact_read_final_answer')
    return {'paired_read': True, 'saved_result': True}


async def read_image(env):
    async with env.open_app(['read']) as app:
        sid = await env.session(app)
        image = create_image_fixture(env.root / 'work')
        messages = await run_turn(app, sid, 'Call read exactly once on vision.png with offset=null and limit=null. '
            'Read the eight capital letters/digits printed in the image. Reply only with those eight characters.')
        result, = paired_tools(messages, ['read'])
        require(isinstance(result.content, list) and any(is_image_block(part) for part in result.content), 'image_result_blocks')
        require(final_text(messages) == image.expected, 'pixel_only_answer')
    return {'image_blocks': True, 'pixel_answer': True, 'metadata_answer_absent': True}


async def files(env):
    expected = nonce()
    async with env.open_app(['ls', 'find', 'grep', 'write', 'edit', 'read']) as app:
        sid = await env.session(app)
        (env.root / 'work' / 'seed.txt').write_text('SEARCH_MARKER\n', encoding='utf-8')
        messages = await run_turn(app, sid,
            'Perform each call exactly once, in this order: '
            'ls path="."; find pattern="*.txt" path="."; grep pattern="SEARCH_MARKER" path="."; '
            'write path="nested/output.txt" content="BEFORE"; '
            f'edit path="nested/output.txt" edits=[{{"oldText":"BEFORE","newText":"{expected}"}}]; '
            'read path="nested/output.txt". Reply only with its final contents.')
        results = paired_tools(messages, ['ls', 'find', 'grep', 'write', 'edit', 'read'])
        require('seed.txt' in results[1].content and 'SEARCH_MARKER' in results[2].content, 'search_real_results')
        require((env.root / 'work' / 'nested/output.txt').read_text() == expected, 'actual_file_effect')
        single_line_read(results[-1].content, expected)
        require(final_text(messages) == expected, 'edited_read_answer')
    return {'file_tools': 6, 'actual_write_edit_read': True}


async def feedback(env):
    async with env.open_app(['read']) as app:
        sid = await env.session(app)
        messages = await run_turn(app, sid, 'Call read exactly once on absent.txt. '
                                  'After it fails, do not retry. Reply exactly ERROR_SEEN.')
        result, = paired_tools(messages, ['read'], statuses=['error'])
        require(bool(result.content) and final_text(messages) == 'ERROR_SEEN', 'error_feedback_continues')
    return {'tool_error': True, 'agent_continues': True}


async def approve(env):
    outside = env.root / 'outside.txt'
    permitted = env.root / 'allowed.txt'
    outside.write_text('APPROVED_VALUE')
    permitted.write_text('ALLOW_VALUE')
    settings = AppSettings(permissions=PermissionSettings(allow=(PermissionRule(tool='read', path=str(permitted)),)))
    async with env.open_app(['read'], settings=settings) as app:
        sid = await env.session(app)
        first = await app.host.runs.start(sid, [('user', f'In a SINGLE tool batch call read twice: first {permitted}, '
            f'then {outside}. After both results reply exactly APPROVED_VALUE. Do not repeat calls.')])
        require(bool(first.get('approval_requests')), 'root_approval_requested')
        expected_calls(first['messages'], [('read', {'path': str(permitted)}), ('read', {'path': str(outside)})], env.root / 'work')
        from langchain_core.messages import ToolMessage
        require(not any(isinstance(m, ToolMessage) for m in first['messages']), 'whole_batch_paused')
        pending = app.host.runs.pending_tools(sid)
        require(len(pending) == 1, 'only_unallowed_target_asks')
        require(pending[0]['tool_name'] == 'read' and pending[0]['target'] == str(outside), 'expected_approval_target')
        result = await app.host.runs.resume_approval(sid, ['approve'])
        approved = paired_tools(result['messages'], ['read', 'read'])
        single_line_read(approved[0].content, 'ALLOW_VALUE')
        single_line_read(approved[1].content, 'APPROVED_VALUE')
        require(final_text(result['messages']) == 'APPROVED_VALUE', 'approved_answer')
        require(not app.host.runs.pending_tools(sid), 'approval_cleared')
    return {'allow_rule': True, 'batch_pause': True, 'resume': True}


async def reject(env):
    outside = env.root / 'outside.txt'
    outside.write_text(nonce())
    async with env.open_app(['read']) as app:
        sid = await env.session(app)
        first = await app.host.runs.start(sid, [('user', f'Call read once on {outside}. If denied, do not retry. Reply DENIED.')])
        require(bool(first.get('approval_requests')), 'rejectable_approval')
        expected_calls(first['messages'], [('read', {'path': str(outside)})], env.root / 'work')
        result = await app.host.runs.resume_approval(sid, ['reject'])
        paired_tools(result['messages'], ['read'], statuses=['error'])
        require(final_text(result['messages']) == 'DENIED', 'rejected_answer')
    return {'rejected_result': True, 'no_retry': True}


async def no_ask(env):
    outside = env.root / 'outside.txt'
    outside.write_text(nonce())
    async with env.open_app(['read']) as app:
        sid = await env.session(app)
        await app.host.sessions.select_permission_mode(sid, 'NoAsk')
        messages = await run_turn(app, sid, f'Call read exactly once on {outside}. '
            'It may be refused. Do not retry. Reply exactly NOASK_DONE.')
        expected_calls(messages, [('read', {'path': str(outside)})], env.root / 'work')
        result, = paired_tools(messages, ['read'], statuses=['error'])
        require(result.additional_kwargs.get('naiyue_not_executed') == 'permission_denied'
                and '(NoAsk)' in result.content, 'noask_permission_source')
        require(not app.host.runs.pending_tools(sid), 'noask_no_prompt')
        require(final_text(messages) == 'NOASK_DONE', 'noask_feedback')
    return {'mode_switch': True, 'ask_becomes_error': True}


async def deny_rule(env):
    settings = AppSettings(permissions=PermissionSettings(mode='BypassApprovals',
        allow=(PermissionRule(tool='read', path='${workspace}/**'),),
        deny=(PermissionRule(tool='read', path='${workspace}/blocked.txt'),)))
    async with env.open_app(['read'], settings=settings) as app:
        sid = await env.session(app)
        (env.root / 'work' / 'blocked.txt').write_text(nonce())
        messages = await run_turn(app, sid, 'Call read exactly once on blocked.txt. '
            'It may be refused. Do not retry. Reply exactly DENY_DONE.')
        expected_calls(messages, [('read', {'path': 'blocked.txt'})], env.root / 'work')
        result, = paired_tools(messages, ['read'], statuses=['error'])
        require(result.additional_kwargs.get('naiyue_not_executed') == 'permission_denied'
                and '(config_deny)' in result.content, 'deny_permission_source')
        require(final_text(messages) == 'DENY_DONE', 'deny_feedback')
    return {'deny_overrides_allow_and_bypass': True}


def core_plan(*, revalidate=False):
    baseline = [('text_stream', 1, text_stream), ('read_text', 2, read_text), ('read_image', 2, read_image)]
    if revalidate:
        baseline += [('text_stream_after_fix', 1, text_stream), ('read_text_after_fix', 2, read_text)]
    return [('100B', 8, baseline),
            ('100C', 18, [('file_flow', 7, files), ('tool_feedback', 2, feedback),
                ('root_approve', 2, approve), ('root_reject', 2, reject), ('no_ask', 2, no_ask), ('deny_rule', 2, deny_rule)])]
