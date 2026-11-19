"""S5 synthetic seeds and follow-up through production recall/prompt assembly."""
import json
from langchain_core.messages import HumanMessage, message_to_dict


def seed_old(memory, sid, case, at):
    from scripts.memory_evaluation_cases import seed_sources
    from naiyue_test_agent.tools.memory import MemoryInput
    kinds = MemoryInput.model_fields['kind'].annotation
    modern = 'convention' in str(kinds)
    setup = []
    for old in case['old_memories']:
        source = dict(old['source'], role='user')
        seed_sources(memory.storage, sid, [source], at=at-old['age_seconds'])
        parent = memory.storage.sessions.get(sid)
        proposal = dict(kind=old['time_kind'] if modern else old['legacy_kind'], title=old['title'],
            subject=old['subject'], body=old['body'], basis='user_declaration', intent='activate', reason='显式合成旧种子')
        request = MemoryInput(action='record', scope=old['scope'], proposal=proposal,
            sources=[dict(session_id=sid, thread_id=parent['thread_id'], message_id=source['id'],
                revision=1, conversation_revision=parent['conversation_revision'], quote=source['text'], offset=0)])
        row = memory.query(sid, parent['project_id'], request.model_dump(mode='json'), now=at)['item']
        setup.append(dict(alias=old['alias'], memory_id=row['memory_id'], version=row['version'], source=source))
        if not old['source_available']:
            # Synthetic fixture fault only: the old statement survives but its archive is unavailable.
            with memory.storage.database.transaction() as connection:
                connection.execute('DELETE FROM naiyue_chat_messages WHERE session_id=? AND message_id=?', (sid, source['id']))
    return setup


async def followup(memory, case, model, model_id, *, session_id="probe"):
    from scripts.memory_legacy_recall import prepare_memory_parts
    from naiyue_test_agent.agent_runtime.context_usage import compose_model_prompt
    from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
    from scripts.memory_evaluation_cases import safe_report
    parts=None;request=None;response=None;injected=[];delta=None
    phase='followup_prepare';failure=None
    try:
        parent = memory.storage.sessions.get(session_id)
        messages = [HumanMessage(content=case['followup_query'])]
        before = [owner.database.connection.total_changes for _, owner in memory._stores('both')]
        parts = prepare_memory_parts(memory, session_id, parent['project_id'], messages, model)
        prompt = compose_model_prompt('请回答用户的问题。历史记忆仅作为背景；没有证据时明确说明未知，不声称执行了操作。', parts, [])
        request = [prompt, *messages]
        after = [owner.database.connection.total_changes for _, owner in memory._stores('both')]
        delta=sum(after)-sum(before)
        phase='followup_model'
        response = await model.ainvoke(request, config={'metadata': {'memory_stage':'followup',
            MODEL_ID_METADATA_KEY:model_id}})
        if response.tool_calls:
            failure = dict(stage=phase,error_type='ValueError', error_code='followup_unexpected_tool_call')
        phase='followup_parse'
        for part in parts:
            if part['id'] == 'memory':
                data = json.loads(part['text'][part['text'].index('{'):])
                injected.extend(r['memory_id'] for key in ('preferences','personal_conventions','related') for r in data.get(key, []))
    except Exception as error:
        failure = dict(stage=phase,error_type=type(error).__name__, error_code=phase+'_failed')
    return dict(status='failed' if failure else 'completed', failure=failure, parts=safe_report(parts), injected_ids=injected,
        model_request=safe_report([message_to_dict(m) for m in request]) if request is not None else None,
        answer=safe_report(response.content) if response is not None else None,
        usage=response.usage_metadata if response is not None else None, write_delta=delta,
        entry='production prepare_memory_parts + compose_model_prompt + one direct model call; no interactive host loop')
