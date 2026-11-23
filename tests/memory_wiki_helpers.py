"""Disposable Wiki plans shared by storage, migration, and query tests."""
from uuid import uuid4
from naiyue_test_agent.storage.memory_sql.writes import FIELDS, SOURCE_FIELDS

NOW = 1000.


def evidence(*, personal=False, number=1):
    row = dict.fromkeys(SOURCE_FIELDS)
    row.update(source_workspace_id='origin',session_id='s',thread_id='s',message_id=f'm{number}',
        revision=1,conversation_revision=0,role='human',occurred_at=NOW,quote='保留来源',
        start_offset=0,end_offset=4,evidence_format=1,user_origin=True,tool_success=False,
        verified_at=NOW,supports_scope=personal)
    return row


def record(page, *, scope='workspace', workspace_id='00000000-0000-4000-8000-000000000001', **changes):
    row = dict.fromkeys(k for k in FIELDS if k!='kind')
    row.update(memory_id=uuid4().hex,page_id=page,version=1,scope=scope,workspace_id=workspace_id,
        status='active',title='约定',body='先验证',aliases_json='[]',tags_json='[]',gaps_json='[]',
        reason='',basis='user_declaration',created_at=NOW,updated_at=NOW,evidence_at=NOW,
        last_confirmed_at=NOW,ttl_days=30,expires_at=NOW+30*86400)
    row.update(changes)
    return dict(row=row,expected_version=None,sources=[evidence(personal=scope=='personal')])


def proposed(**changes):
    return dict(matter='pytest环境',topic='working_agreements',scope='workspace',title='测试环境',
        body='使用.venv',subject='pytest环境',basis='user_declaration',intent='activate',reason='用户明确约定',
        sources=[dict(evidence_id='E1')])|changes


def scripted_replies(replies):
    """Explicit Wiki read/finish scripts for worker event and failure scenarios."""
    import json
    from langchain_core.messages import AIMessage
    result=[]
    for reply in replies:
        result.append(reply)
        try:values=json.loads(reply.content).get('proposals',[])
        except (ValueError,TypeError,AttributeError):continue
        valid=[(i,p) for i,p in enumerate(values,1) if isinstance(p,dict) and 'topic' in p and 'body' in p]
        if not valid:continue
        result.append(AIMessage(content=json.dumps(dict(result=dict(step='read',requests=[dict(action='list_pages')])))))
        result.append(AIMessage(content=json.dumps(dict(result=dict(step='finish',
            decisions=[dict(relation='add',proposal_ids=[f'P{i}'],destination='N1') for i,_ in valid],
            page_changes=[dict(ref='N1',topic='working_agreements',title='测试规则',description='')])))))
    return result
