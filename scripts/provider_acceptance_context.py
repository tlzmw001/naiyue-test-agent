"""Skill invocation, manual summary/reopen and controls on real streamed responses."""

from .provider_acceptance_cases import require, paired_tools, final_text, run_turn
from .provider_acceptance_core import nonce


async def skills(env):
    expected = nonce()
    async with env.open_app(['read']) as app:
        sid = await env.session(app)
        root = env.root / 'work' / '.agents/skills/acceptance-skill'
        root.mkdir(parents=True)
        (root / 'SKILL.md').write_text('---\nname: acceptance-skill\ndescription: '
            'Explicit synthetic acceptance command\n---\n'
            f'After reading this Skill, reply only with {expected}. Do not call other tools.\n')
        catalog = await app.aget_skills(env.root / 'work')
        require(len(catalog.entries) == 1 and catalog.command_for(catalog.entries[0]) == '/acceptance-skill', 'skill_catalog_command')
        messages = await run_turn(app, sid, '/acceptance-skill')
        paired_tools(messages, ['read'])
        require(final_text(messages) == expected, 'explicit_skill_result')
    return {'discovered': True, 'explicit_command': True, 'skill_read': True}


async def compact_reopen(env):
    expected = nonce()
    async with env.open_app() as app:
        sid = await env.session(app)
        padding = 'Synthetic redundant background note: no additional action is required. ' * 400
        await run_turn(app, sid, f'Remember the synthetic acceptance identifier {expected}. '
            f'The following is redundant background, not separate tasks: {padding}\nReply only ACK.')
        await run_turn(app, sid, 'Reply only RECENT. Keep the earlier identifier for future use.')
        before = await app.aget_messages(sid)
        result = await app.acompact_context(sid)
        require(result.get('compaction_status') == 'applied', 'manual_summary_applied')
        saved = await app.aget_messages(sid)
        require(saved[0].additional_kwargs.get('naiyue_compaction_summary'), 'summary_marker_saved')
        require(expected in saved[0].content, 'summary_preserves_identifier')
        require([m.id for m in saved[1:]] == [m.id for m in before[-2:]], 'latest_complete_turn_retained')
    async with env.open_app() as app:
        require(await app.arestore_session(sid) == sid, 'reopened_same_session')
        require(await app.aget_messages(sid) == saved, 'reopened_summary_history')
        messages = await run_turn(app, sid, 'What is the synthetic acceptance identifier from earlier? Reply only that identifier.')
        require(final_text(messages) == expected, 'reopened_identifier_recalled')
        view = await app.aget_context_view(sid)
        require(view['current']['estimate'] is not None and view['latest_request'] is not None, 'estimate_and_vendor_request_separate')
    return {'manual_compact': True, 'retained_turn': True, 'reopen_and_continue': True,
            'context_estimate_separate': True}


async def controls(env):
    async with env.open_app() as app:
        sid = await env.session(app)
        queued = False
        def inject(event):
            nonlocal queued
            if event.event_type == 'model_call_started' and not queued:
                queued = True
                app.steer(sid, 'For this response reply only STEER_OK.')
                app.follow_up(sid, 'Now reply only FOLLOW_OK.')
        unsubscribe = app.subscribe_events(inject)
        try:
            await run_turn(app, sid, 'Reply only START_OK.')
        finally:
            unsubscribe()
        deliveries = {event.data.kind for event in env.events if event.event_type == 'control_delivered'}
        require(deliveries == {'steering', 'follow_up'}, 'both_control_deliveries')
        require(final_text(await app.aget_messages(sid)) == 'FOLLOW_OK', 'followup_result')
    return {'steering_delivered': True, 'followup_delivered': True}


async def stop_stream(env):
    async with env.open_app() as app:
        sid = await env.session(app)
        requested = False
        async for event in app.astream_run(sid, [('user', 'Count from one to two hundred, one number per line.')]):
            if event.event_type == 'message_delta' and not requested:
                app.request_stop(sid)
                requested = True
        require(requested, 'stop_after_real_delta')
        terminal = [event for event in env.events if event.event_type == 'run_finished']
        require(len(terminal) == 1 and terminal[0].data.status == 'stopped', 'stream_stop_terminal')
        require(not app._sessions.describe_session(sid)['busy'], 'stopped_owner_released')
    return {'active_stop': True, 'local_settle': True, 'remote_cancel_not_proven': True}


def context_plan():
    return [('100E', 10, [('skills', 2, skills), ('compact_reopen', 4, compact_reopen),
                         ('controls', 3, controls), ('stop_stream', 1, stop_stream)])]
