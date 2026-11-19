"""Navigation metadata obeys the same owner boundary as memory bodies."""
import json
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from test_memory_readonly import memory
from test_memory_wiki_pagination import seed_wiki,query,decode_content
from memory_wiki_helpers import NOW,record


def test_personal_and_project_same_page_identity_stay_separate(memory):
    storage,access,workspace,pages=seed_wiki(memory)
    personal=access._personal_store.repository();personal.prepare_wiki(now=NOW)
    p=dict(pages[0],title='个人学习',topic='collaboration')
    with personal.wiki_edit('personal',None,now=NOW,capacity=30) as writer:
        writer.apply(pages=[p],memories=[record(p['page_id'],scope='personal',workspace_id=None,body='个人约定')])
    both=query(memory,action='list_topics',scope='both')
    assert {t['scope'] for t in decode_content(both)['topics']}=={'workspace','personal'}
    result=query(memory,action='read_page',scope='personal',page_id=p['page_id'])
    assert '个人约定' in result['content'] and '后台规则' not in result['content']
    with pytest.raises(MemoryReadError,match='memory_scope_mismatch'):
        access.query_wiki('caller','other',dict(action='list_topics',scope='workspace'),now=NOW)


def test_missing_and_expired_link_targets_are_not_rendered(memory):
    storage,access,workspace,pages=seed_wiki(memory)
    personal=access._personal_store.repository();personal.prepare_wiki(now=NOW)
    absent=WikiWriter.new_page('collaboration','empty','empty')
    expired=WikiWriter.new_page('collaboration','expired','expired')
    with personal.wiki_edit('personal',None,now=NOW,capacity=30) as writer:
        writer.apply(pages=[absent,expired],memories=[record(expired['page_id'],scope='personal',workspace_id=None,expires_at=NOW)])
    targets=[dict(scope='personal',page_id=p['page_id'],description='关联',workspace_id=None) for p in (absent,expired)]
    with storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=30) as writer:
        writer.apply(pages=[dict(pages[0],expected_version=1)],
            links=[dict(page_id=pages[0]['page_id'],targets=targets)],external_targets=targets)
    result=query(memory,action='read_page',scope='workspace',page_id=pages[0]['page_id'])
    assert decode_content(result)['links']==[]
