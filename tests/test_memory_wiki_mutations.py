"""Direct Wiki writes against real archived user evidence and isolated stores."""
import pytest
from pydantic import ValidationError
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_readonly import memory
from test_memory_lifecycle import source,proposal
from memory_wiki_helpers import NOW


def args(**changes):
    p=proposal();p.pop('kind')
    return dict(action='record',scope='workspace',destination={'new_page':{'topic':'working_agreements',
        'title':'测试规则','description':'执行约定'}},proposal=p,**changes)


def setup(memory):
    storage,access,workspace,_=memory
    storage.memory.prepare_wiki(now=NOW)
    return storage,access,workspace


def call(memory,request,now=4000.):
    return memory[1].query_wiki('caller',memory[2],request,now=now)


def body(memory,item):
    return memory[0].database.connection.execute('SELECT body FROM naiyue_memories WHERE memory_id=?',(item['memory_id'],)).fetchone()[0]


def test_atomic_record_supplement_confirm_and_replace(memory):
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    saved=call(memory,args(sources=[ref]));item=saved['item']
    assert item['ttl_days']==30 and item['expires_at']==1000+30*86400
    newer=source(access,'caller',text='补充一下之前的规则',at=2000)
    update=dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'supplement'},sources=[newer])
    supplemented=call(memory,update)['item']
    assert body(memory,supplemented)=='使用.venv' and supplemented['expires_at']==item['expires_at']
    assert supplemented['evidence_at']==1000 and supplemented['version']==2
    newer=source(access,'caller',text='之前完整规则继续有效',at=3000)
    confirmed=call(memory,dict(update,expected_version=2,change={'mode':'confirm'},sources=[newer]))['item']
    assert body(memory,confirmed)=='使用.venv' and confirmed['evidence_at']==3000
    assert confirmed['expires_at']==3000+30*86400
    replacement=dict(args(sources=[])['proposal'],body='改用uv运行pytest')
    fresh=source(access,'caller',text='以后改用uv运行pytest',at=3500)
    changed=call(memory,dict(update,expected_version=3,change={'mode':'replace','proposal':replacement},sources=[fresh]))['item']
    assert changed['memory_id']==item['memory_id'] and changed['version']==4
    assert body(memory,changed)=='改用uv运行pytest'
    with pytest.raises(MemoryReadError,match='memory_conflict'):call(memory,update)


def test_personal_scope_binding_preserved_and_forget_suppresses(memory):
    storage,access,workspace=setup(memory)
    personal=access._personal_store.repository();personal.prepare_wiki(now=NOW)
    ref=source(access,'caller',text='所有项目均先验证再修改',at=1000)
    with pytest.raises(ValidationError):
        call(memory,dict(args(sources=[ref]),scope='personal'))
    saved=call(memory,dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0]))['item']
    fresh=source(access,'caller',text='补充案例',at=2000)
    updated=call(memory,dict(action='update',scope='personal',memory_id=saved['memory_id'],expected_version=1,
        change={'mode':'supplement'},sources=[fresh]))['item']
    assert updated['expires_at']==saved['expires_at']
    stored=personal.wiki_sources([(saved['memory_id'],2)])
    assert any(s['supports_scope'] and s['message_id']==ref['message_id'] for s in stored)
    call(memory,dict(action='forget',scope='personal',memory_id=saved['memory_id'],expected_version=2,
        sources=[fresh],reason='用户要求遗忘'))
    assert personal.wiki_sources([(saved['memory_id'],2)])==[]
    assert personal.versions(saved['memory_id'])==[]
    with pytest.raises(MemoryReadError,match='memory_source_suppressed'):
        call(memory,dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0]))


def test_bad_source_expired_evidence_duplicate_and_full_page_write_nothing(memory):
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    with pytest.raises(MemoryReadError,match='memory_source_not_supported'):
        call(memory,args(sources=[dict(ref,quote='不存在')]))
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_pages').fetchone()[0]==0
    with pytest.raises(MemoryReadError,match='memory_evidence_expired'):call(memory,args(sources=[ref]),now=1000+31*86400)
    item=call(memory,args(sources=[ref]))['item']
    with pytest.raises(MemoryReadError,match='memory_duplicate'):call(memory,args(sources=[ref]))
    access.settings=access.settings.model_copy(update={'wiki_topic_max_memories':1})
    request=args(sources=[ref]);request['destination']={'page_id':item['page_id'],'expected_page_version':1}
    request['proposal'].update(body='另一事项',subject='另一事项')
    with pytest.raises(MemoryReadError,match='memory_topic_full'):call(memory,request)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==1


def test_confirm_needs_new_evidence_preserves_deadline_and_does_not_activate_candidate(memory):
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    request=args(sources=[ref]);request['proposal']['deadline']=5000.
    item=call(memory,request)['item']
    confirm=dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'confirm'},sources=[ref])
    with pytest.raises(MemoryReadError,match='memory_new_evidence_required'):call(memory,confirm)
    newer=source(access,'caller',text='完整规则保持',at=2000)
    confirmed=call(memory,dict(confirm,sources=[newer]))['item']
    assert confirmed['expires_at']==5000.
    request=args(sources=[newer]);request['proposal'].update(intent='candidate',subject='另一规则',body='待确认规则',gaps=['待确认'])
    candidate=call(memory,request)['item']
    with pytest.raises(MemoryReadError,match='memory_activation_required'):
        call(memory,dict(confirm,memory_id=candidate['memory_id'],sources=[newer]))


def test_list_search_read_current_sources_and_history_only_new_generation(memory):
    from test_memory_wiki_pagination import decode_content
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    item=call(memory,args(sources=[ref]))['item']
    listed=call(memory,dict(action='list',scope='workspace'))
    assert [r['memory_id'] for r in listed['items']]==[item['memory_id']]
    assert listed['budget']['estimated_tokens']<=2000
    searched=call(memory,dict(action='search',scope='workspace',query='.venv'))
    assert [r['memory_id'] for r in searched['items']]==[item['memory_id']]
    read=call(memory,dict(action='read',scope='workspace',memory_id=item['memory_id'],include_history=True))
    entry=decode_content(read)['entries'][0]
    assert entry['body']=='使用.venv' and entry['sources'][0]['available']
    assert entry['history'][0]['version']==1 and 'kind' not in entry
    assert 'kind' not in listed['items'][0]


def test_forget_succeeds_with_eight_necessary_sources_and_erases_all_versions(memory):
    storage,access,workspace=setup(memory)
    refs=[source(access,'caller',text=f'约定证据{i}',at=1000+i) for i in range(8)]
    item=call(memory,args(sources=refs))['item']
    forget_source=source(access,'caller',text='请忘记这条约定',at=2000)
    result=call(memory,dict(action='forget',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        reason='用户要求删除',sources=[forget_source]))
    assert result['status']=='forgotten'
    c=storage.database.connection
    assert tuple(c.execute('SELECT status,body FROM naiyue_memories').fetchone())==('forgotten','')
    assert c.execute('SELECT COUNT(*) FROM naiyue_memory_versions').fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM naiyue_memory_sources').fetchone()[0]==0


def test_id_list_and_long_read_have_complete_bounded_continuation(memory):
    from test_memory_wiki_pagination import decode_content
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    ids=[]
    for i in range(12):
        request=args(sources=[ref]);request['proposal'].update(body=f'独立规则{i}',subject=f'规则{i}')
        ids.append(call(memory,request)['item']['memory_id'])
    cursor=None;seen=[]
    for _ in range(50):
        result=call(memory,dict(action='list',scope='workspace',limit=5,cursor=cursor))
        seen.extend(i['memory_id'] for i in result['items'])
        assert result['budget']['estimated_tokens']<=2000
        if not result['has_more']:break
        assert result['next_cursor']!=cursor;cursor=result['next_cursor']
    else:pytest.fail('list did not finish')
    assert len(seen)==len(ids) and set(seen)==set(ids)
    text='讲清职责😀'*180
    request=args(sources=[ref]);request['proposal'].update(body=text,subject='长规则')
    item=call(memory,request)['item'];access.settings=access.settings.model_copy(update={'wiki_read_tokens':1000})
    cursor=None;pieces=[]
    for _ in range(50):
        result=call(memory,dict(action='read',scope='workspace',memory_id=item['memory_id'],cursor=cursor))
        pieces.extend(e['body'] for e in decode_content(result)['entries'])
        if not result['has_more']:break
        assert result['next_cursor']!=cursor;cursor=result['next_cursor']
    else:pytest.fail('read did not finish')
    assert ''.join(pieces)==text and len(pieces)>1


def test_original_source_removal_reports_unavailable_without_invented_quote(memory):
    from test_memory_wiki_pagination import decode_content
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    item=call(memory,args(sources=[ref]))['item']
    with storage.database.transaction(write=True) as c:c.execute('DELETE FROM naiyue_chat_messages WHERE message_id=?',(ref['message_id'],))
    result=call(memory,dict(action='read',scope='workspace',memory_id=item['memory_id']))
    proof=decode_content(result)['entries'][0]['sources'][0]
    assert proof['available'] is False and proof['reason']=='source_unavailable' and 'quote' not in proof


def test_disable_with_full_evidence_keeps_existing_sources_and_hides_entry(memory):
    storage, access, _ = setup(memory)
    refs = [source(access, 'caller', text=f'约定证据{i}', at=1000 + i) for i in range(8)]
    item = call(memory, args(sources=refs))['item']
    memory_id = item['memory_id']
    instruction = source(access, 'caller', text='请停用这条约定', at=2000)
    disabled = call(memory, dict(action='disable', scope='workspace', memory_id=memory_id,
        expected_version=1, reason='用户要求停用', sources=[instruction]))['item']
    assert disabled['status'] == 'inactive' and disabled['version'] == 2
    assert disabled['expires_at'] == item['expires_at']
    stored = storage.memory.wiki_sources([(memory_id, 2)])
    assert {s['message_id'] for s in stored} == {ref['message_id'] for ref in refs}
    assert call(memory, dict(action='list', scope='workspace'))['items'] == []
