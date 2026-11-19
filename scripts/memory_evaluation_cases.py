"""Frozen engineering cases for the S0/S2 milestone; no model configuration access."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/data/memory_optimization/evidence.json'


def load_cases():
    raw=FIXTURES.read_bytes()
    protocol=json.loads(FIXTURES.with_name('protocol.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=protocol['fixtures_sha256']:raise ValueError('fixture_changed')
    if hashlib.sha256(FIXTURES.with_name('rubric.md').read_bytes()).hexdigest()!=protocol['rubric_sha256']:raise ValueError('rubric_changed')
    data=json.loads(raw)
    if data['synthetic'] is not True:raise ValueError('synthetic_required')
    return data['cases'],hashlib.sha256(raw).hexdigest()


def write_report(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n')


def load_suite(fixtures=None,protocol=None):
    fixtures=Path(fixtures or FIXTURES.with_name('fixtures.json'))
    protocol=Path(protocol or FIXTURES.with_name('protocol.json'))
    settings=json.loads(protocol.read_text())
    if hashlib.sha256(fixtures.read_bytes()).hexdigest()!=settings['suite_fixtures_sha256']:
        raise ValueError('fixture_changed')
    if hashlib.sha256(protocol.with_name('rubric.md').read_bytes()).hexdigest()!=settings['rubric_sha256']:
        raise ValueError('rubric_changed')
    data=json.loads(fixtures.read_text())
    if data.get('format') == 2:
        from scripts.memory_space_time_labels import validate
        return validate(data, settings)
    if data.get('format')!=1 or data.get('synthetic') is not True:raise ValueError('fixture_invalid')
    cases=data['cases'];seen=set();families={}
    for c in cases:
        if c['case_id'] in seen or families.setdefault(c['family_id'],c['split'])!=c['split']:
            raise ValueError('fixture_identity_or_split_leakage')
        seen.add(c['case_id'])
        if not c['expected_facts'] or not c['sources']:raise ValueError('fixture_labels_missing')
        for fact in c['expected_facts']:
            if fact['must_activate'] and fact['must_not_activate']:raise ValueError('fixture_labels_conflict')
    for suite in ('input','comparison','recall','semantic'):
        for split in ('calibration','validation','holdout'):
            if sum(c['suite']==suite and c['split']==split for c in cases)!=8:raise ValueError('fixture_balance')
    return cases,settings


def planned_calls(count,repeat,calls_per_case,maximum=24):
    if any(type(n) is not int or n<1 for n in (count,repeat,calls_per_case,maximum)):raise ValueError('budget_invalid')
    if count*repeat*calls_per_case>maximum:raise ValueError('budget_exceeded')
    return count*repeat*calls_per_case


def load_engineering_matrix(name,split):
    if name not in ('worker','recall') or split not in ('calibration','validation','holdout'):
        raise ValueError('engineering_split_invalid')
    path=FIXTURES.with_name(name+'_engineering_v1.json')
    protocol_path=path.with_name(name+'_engineering_v1_protocol.json')
    raw=path.read_bytes();protocol=json.loads(protocol_path.read_text());data=json.loads(raw)
    checksum=hashlib.sha256(raw).hexdigest()
    if checksum!=protocol['fixtures_sha256']:raise ValueError('fixture_changed')
    if data.get('synthetic') is not True or data.get('contract')!='engineering_matrix_v1':raise ValueError('fixture_invalid')
    cases=data['cases'];ids=[c.get('id',c.get('case_id')) for c in cases]
    if len(cases)!=24 or len(set(ids))!=24 or len({c['family_id'] for c in cases})!=24:
        raise ValueError('fixture_identity_invalid')
    if any(sum(c['split']==s for c in cases)!=8 for s in ('calibration','validation','holdout')):
        raise ValueError('fixture_balance')
    for c in cases:
        if name=='recall':
            aliases=[v['alias'] for v in c['seeds']]
            if len(set(aliases))!=len(aliases) or not set(c['expected_aliases'])<=set(aliases):raise ValueError('fixture_seed_invalid')
    return [c for c in cases if c['split']==split],checksum,hashlib.sha256(protocol_path.read_bytes()).hexdigest()


def seed_sources(storage, sid, sources, *, at):
    """Save synthetic visible facts using the existing admission/archive formats."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, message_to_dict
    from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
    from naiyue_test_agent.application.sessions.session_controls import mark_human_messages

    row, messages, facts, boundaries = storage.sessions.get(sid), [], [], []
    inputs = list(sources)
    setup = []
    if not inputs or inputs[0]['role'] != 'user':
        admission = dict(id='probe-admission-' + uuid4().hex, role='user',
            text='请整理下面这一段已经结束的工作记录。')
        inputs.insert(0, admission)
        setup.append(dict(kind='synthetic_root_admission', source=admission))
    reply_id = None
    for source in inputs:
        extra = {'naiyue_reply_id': reply_id} if reply_id else {}
        if source['role'] == 'user':
            message, = mark_human_messages([HumanMessage(content=source['text'], id=source['id'])])
            reply_id = message.additional_kwargs['naiyue_reply_id']
            boundaries.append(dict(reply_id=reply_id, input_message_id=message.id, source_checkpoint_id='probe-checkpoint'))
        elif source['role'] == 'assistant':
            message = AIMessage(content=source['text'], id=source['id'], additional_kwargs=extra)
        elif source['role'] == 'tool':
            owner = AIMessage(content='', id=source['id'] + '-owner', additional_kwargs=extra,
                tool_calls=[dict(id=source['tool_call_id'], name='synthetic_observation', args={})])
            messages.append((owner, reply_id))
            message = ToolMessage(content=source['text'], id=source['id'], tool_call_id=source['tool_call_id'],
                name='synthetic_observation', status='error' if source['id'] == 'e06-t1' else 'success')
        else:
            raise ValueError('semantic_source_role_invalid')
        messages.append((message, reply_id))
    for index, (message, message_reply) in enumerate(messages):
        body = json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True)
        facts.append(dict(message_id=message.id, body_json=body, body_hash=hashlib.sha256(body.encode()).hexdigest(),
            kind='message', source_checkpoint_id='probe-checkpoint', message_type=message.type,
            parent_message_id=message.id + '-owner' if message.type == 'tool' else None,
            reply_id=message_reply, occurred_at=at + index / 100))
    head = next((r for r in storage.conversation.chat_heads(sid) if r['thread_id'] == row['thread_id']), {})
    storage.conversation.save_chat_updates(sid, [ChatUpdate(row['thread_id'], '', 'root', head.get('revision', 0),
        uuid4().hex, tuple(facts), boundaries=tuple(boundaries))])
    upper = storage.database.read_rows('SELECT MAX(sequence) AS n FROM naiyue_chat_messages WHERE session_id=?', (sid,))[0]['n']
    return dict(upper=upper, source_texts={s['id']: s['text'] for s in inputs}, setup=setup)


def safe_report(value):
    from naiyue_test_agent.application.memory.memory_sources import SECRET
    if isinstance(value, str):
        return '[redacted_sensitive_text]' if SECRET.search(value) else value
    if isinstance(value, dict):
        if value.get('type') in ('reasoning','thinking','redacted_thinking'):return None
        return {key: safe_report(item) for key, item in value.items()
            if key not in ('encrypted_content','signature','thinking','reasoning')}
    if isinstance(value, (tuple, list)):
        return [safe_report(item) for item in value if not isinstance(item,dict)
            or item.get('type') not in ('reasoning','thinking','redacted_thinking')]
    return value


def evaluation_model_identity(model,settings):
    from naiyue_test_agent.model_access.calibration_identity import KEY, fingerprint
    identity=(getattr(model,'metadata',None) or {}).get(KEY) or {}
    names=('temperature','top_p','seed','max_tokens','num_predict','max_retries',
        'reasoning','reasoning_effort','thinking','think','model_kwargs')
    effective={name:getattr(model,name,None) for name in names}
    return dict(identity_status='available' if identity else 'partial',
        adapter_identity={k:identity[k] for k in ('version','model_id','request_model','kind','api',
            'route_fingerprint','thinking_fingerprint','input_profile_fingerprint','adapter_fingerprint') if k in identity},
        settings_sha256=fingerprint(effective),
        memory_settings_sha256=fingerprint(settings.model_dump(mode='json')),
        memory_output=(getattr(model,'profile',None) or {}).get('memory_output','text'),
        sdk_max_retries=getattr(model,'max_retries',None))
