"""Disposable format3 databases exercise the format4 upgrade contract."""
import json
import sqlite3
import pytest
from naiyue_test_agent.storage.memory_sql import memory_schema,memory_lifecycle_schema,memory_evidence_schema,lexical


def legacy():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;lexical.configure(c)
    memory_schema.create(c);memory_lifecycle_schema.upgrade(c);memory_evidence_schema.upgrade(c)
    return c


def test_two_kind_migration_preserves_history_clocks_and_is_idempotent():
    from naiyue_test_agent.storage.memory_sql import memory_time_schema
    from naiyue_test_agent.storage.memory_sql.writes import MemoryWriter,FIELDS
    c=legacy();writer=MemoryWriter(c)
    row=dict.fromkeys(FIELDS)
    row.update(memory_id='a'*32,version=1,scope='personal',kind='user',status='active',title='约定',body='先验证',
        aliases_json='[]',tags_json='[]',subject='分析',basis='user_declaration',created_at=10.,updated_at=20.,evidence_at=15.,
        last_confirmed_at=15.,gaps_json='[]',reason='原始')
    writer.save(row,[])
    with c:memory_time_schema.upgrade(c)
    current=dict(c.execute('SELECT * FROM naiyue_memories').fetchone())
    assert current['kind']=='convention' and current['version']==2
    assert current['expires_at']==15.+90*86400
    assert all(current[k]==row[k] for k in ('created_at','updated_at','evidence_at','last_confirmed_at'))
    history=[json.loads(r[0]) for r in c.execute('SELECT snapshot_json FROM naiyue_memory_versions ORDER BY version')]
    assert [r['kind'] for r in history]==['user','convention']
    before=c.total_changes
    with c:memory_time_schema.upgrade(c)
    assert c.total_changes==before
    with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE naiyue_memories SET kind='user'")
    assert c.execute('SELECT memory_version FROM naiyue_memory_search').fetchone()[0]==2


def test_migration_conflicts_sources_forgotten_and_rollback(monkeypatch):
    from naiyue_test_agent.storage.memory_sql import memory_time_schema
    from naiyue_test_agent.storage.memory_sql.writes import MemoryWriter,FIELDS,SOURCE_FIELDS
    monkeypatch.setattr(memory_time_schema.time,'time',lambda:0.)
    c=legacy();w=MemoryWriter(c)
    for i,kind in enumerate(('rule','decision','procedure'),1):
        row=dict.fromkeys(FIELDS);row.update(memory_id=str(i)*32,version=1,scope='workspace',workspace_id='w',kind=kind,
            status='active',title='测试',body=str(i),aliases_json='[]',tags_json='[]',subject='测试',basis='user_declaration',
            created_at=1.,updated_at=2.,evidence_at=1.,last_confirmed_at=1.,expires_at=100000.,deadline_at=90000.,gaps_json='[]',reason='')
        source=dict.fromkeys(SOURCE_FIELDS);source.update(source_workspace_id='w',session_id='s',thread_id='t',message_id='m'+str(i),
            revision=1,conversation_revision=1,role='human',occurred_at=1.,quote='原文',start_offset=0,end_offset=2)
        w.save(row,[source])
    w.forget(dict(row,version=2),['suppressed'],3.)
    c.commit();c.execute('BEGIN')
    memory_time_schema.upgrade(c);c.rollback()
    assert c.execute('SELECT format_version FROM naiyue_memory_info').fetchone()[0]==3
    assert c.execute('SELECT COUNT(*) FROM naiyue_memory_sources').fetchone()[0]==2
    with c:memory_time_schema.upgrade(c)
    rows=[dict(r) for r in c.execute('SELECT * FROM naiyue_memories ORDER BY memory_id')]
    assert [r['status'] for r in rows]==['candidate','candidate','forgotten']
    assert all(r['expires_at']<=90000. for r in rows[:2])
    assert all('legacy_kind_merge_conflict' in json.loads(r['gaps_json']) for r in rows[:2])
    assert c.execute('SELECT COUNT(*) FROM naiyue_memory_sources').fetchone()[0]==4
    assert c.execute('SELECT COUNT(*) FROM naiyue_memory_sources WHERE evidence_format IS NOT NULL').fetchone()[0]==0
    assert rows[2]['body']=='' and rows[2]['version']==2
    assert c.execute('SELECT COUNT(*) FROM naiyue_memory_versions WHERE memory_id=?',('3'*32,)).fetchone()[0]==0
    assert c.execute('SELECT source_hash FROM naiyue_memory_suppression').fetchone()[0]=='suppressed'


def test_new_contract_defaults_and_old_requests_rejected():
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request
    from pydantic import ValidationError

    settings = MemorySettings()
    assert settings.default_ttl_days == 30
    assert settings.wiki_navigation_tokens == settings.wiki_read_tokens == 2000
    for kind in ('user', 'rule', 'decision', 'procedure', 'experience', 'reference', 'context', 'convention'):
        with pytest.raises(ValidationError):
            compile_wiki_request({'request': dict(action='list', scope='personal', kind=kind)})
    assert compile_wiki_request({'request': dict(action='list', scope='personal')})['scope'] == 'personal'


from test_memory_readonly import memory
from test_memory_lifecycle import source
from test_memory_wiki_mutations import setup, args, call


def test_replace_uses_only_fresh_evidence_for_unified_ttl(memory):
    from naiyue_test_agent.contracts.memory import MemoryReadError
    from pydantic import ValidationError

    _, access, _ = setup(memory)
    first = source(access, 'caller', at=1000.)
    old = call(memory, args(sources=[first]), now=1000.)['item']
    newer = source(access, 'caller', at=2000.)
    proposal = args(sources=[])['proposal']
    request = dict(action='update', scope='workspace', memory_id=old['memory_id'], expected_version=1,
                   change=dict(mode='replace', proposal=proposal), sources=[newer])
    with pytest.raises(ValidationError):
        call(memory, dict(request, change=dict(mode='replace', proposal=dict(proposal, kind='convention'))))
    current = call(memory, request, now=2001.)['item']
    assert current['memory_id'] == old['memory_id'] and current['version'] == 2
    assert current['expires_at'] == 2000. + 30 * 86400
    request.update(expected_version=2)
    with pytest.raises(MemoryReadError, match='memory_new_evidence_required'):
        call(memory, request, now=2002.)
    request['sources'] = [source(access, 'caller', at=3000.)]
    final = call(memory, request, now=3001.)['item']
    assert final['version'] == 3 and final['expires_at'] == 3000. + 30 * 86400


def test_wiki_topic_cannot_bypass_exact_duplicate_or_conflict(memory):
    import json
    from naiyue_test_agent.contracts.memory import MemoryReadError

    storage, access, _ = setup(memory)
    ref = source(access, 'caller', at=1000.)
    call(memory, args(sources=[ref]))
    request = args(sources=[ref])
    request['destination']['new_page']['topic'] = 'stable_knowledge'
    with pytest.raises(MemoryReadError, match='memory_duplicate'):
        call(memory, request)
    request['proposal']['body'] = '不同结论'
    item = call(memory, request)['item']
    row = storage.database.connection.execute(
        'SELECT gaps_json FROM naiyue_memories WHERE memory_id=?', (item['memory_id'],)).fetchone()
    assert item['status'] == 'candidate' and 'known_conflict' in json.loads(row['gaps_json'])


@pytest.mark.parametrize('raw', ['preference_limit = 0', 'preference_limit = 3',
    'preference_limit = 2\npersonal_convention_tokens = 123'])
def test_legacy_count_requires_explicit_config_migration(tmp_path, raw):
    from naiyue_test_agent.settings import load_settings

    path = tmp_path / 'settings.toml'
    path.write_text('[memory]\n' + raw)
    with pytest.raises(ValueError, match='memory_config_migration_required: remove .*preference_limit'):
        load_settings(path)
    assert path.read_text() == '[memory]\n' + raw


@pytest.mark.parametrize('raw', ['preference_limit = true', 'preference_limit = 11',
    '[memory.active_ttl_days]\nuser = 30'])
def test_invalid_obsolete_config_is_rejected_before_application_start(tmp_path, raw):
    from naiyue_test_agent.settings import load_settings

    path = tmp_path / 'settings.toml'
    path.write_text('[memory]\n' + raw)
    with pytest.raises(ValueError, match='memory_config_migration_required'):
        load_settings(path)


@pytest.mark.parametrize('kind',list(('user','rule','decision','context','procedure','experience','reference')))
@pytest.mark.parametrize('status',['active','candidate','inactive'])
def test_every_legacy_kind_maps_without_renewal(kind,status):
    from naiyue_test_agent.storage.memory_sql import memory_time_schema
    from naiyue_test_agent.storage.memory_sql.writes import MemoryWriter,FIELDS
    c=legacy();row=dict.fromkeys(FIELDS)
    row.update(memory_id='b'*32,version=1,scope='personal',kind=kind,status=status,title='记录',body='正文',
        aliases_json='[]',tags_json='[]',subject='事项',basis='user_declaration',created_at=1.,updated_at=2.,
        evidence_at=1.,last_confirmed_at=1.,gaps_json='[]',reason='',expires_at=10.)
    MemoryWriter(c).save(row,[])
    with c:memory_time_schema.upgrade(c)
    current=dict(c.execute('SELECT * FROM naiyue_memories').fetchone())
    assert current['kind']==('convention' if kind in ('user','rule','decision') else 'context')
    assert current['expires_at']==10. and current['updated_at']==2.
    assert current['status']==status
    assert current['ttl_days']==(90 if current['kind']=='convention' and status!='candidate' else 30)


@pytest.mark.parametrize('relation,confirmation,user,valid',[
    ('correction','whole',True,True),('supplement','whole',True,False),
    ('correction','partial',True,False),('correction','whole',False,False)])
def test_comparison_compiler_controls_kind_change(relation,confirmation,user,valid):
    from test_memory_proposal_comparison import prepared,decision,group
    from test_memory_s3_review_fixes import old_row
    from naiyue_test_agent.application.memory.memory_proposals import compile_comparison_decisions
    from naiyue_test_agent.contracts.memory import MemoryReadError
    p=prepared();p['proposal']['kind']='convention';p['evidence'][0]['user_origin']=user
    claim=dict(kind='convention',subject='pytest环境',body='使用.venv',basis='user_declaration',intent='activate')
    raw=dict(group_id='G1',decisions=[decision(relation=relation,target_old_ref='O1',confirmation_scope=confirmation,claim=claim)])
    def compile():return compile_comparison_decisions(raw,group(old_refs=['O1']),{'P1':p},{'O1':old_row()})
    if valid:assert compile()[0][0]['proposal']['kind']=='convention'
    else:
        with pytest.raises(MemoryReadError):compile()


def test_non_table_memory_config_still_disables_only_memory(tmp_path):
    from naiyue_test_agent.settings import load_settings
    path=tmp_path/'settings.toml';path.write_text('memory = "invalid"\n')
    assert load_settings(path).memory.enabled is False


def test_inference_cannot_change_time_kind_even_with_user_source():
    from test_memory_proposal_comparison import prepared,decision,group
    from test_memory_s3_review_fixes import old_row
    from naiyue_test_agent.application.memory.memory_proposals import compile_comparison_decisions
    from naiyue_test_agent.contracts.memory import MemoryReadError
    p=prepared();p['proposal']['kind']='convention'
    claim=dict(kind='convention',subject='pytest环境',body='使用.venv',basis='user_declaration',intent='activate')
    raw=dict(group_id='G1',decisions=[decision(relation='correction',support='inference',target_old_ref='O1',claim=claim)])
    with pytest.raises(MemoryReadError):
        compile_comparison_decisions(raw,group(old_refs=['O1']),{'P1':p},{'O1':old_row()})


def test_personal_format3_entry_creates_backup_and_reopens_idempotently(tmp_path):
    from naiyue_test_agent.storage.database import Database
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.storage.personal_database import APPLICATION_ID
    path=tmp_path/'memory.sqlite';old=legacy()
    old.execute('PRAGMA user_version=3');old.execute(f'PRAGMA application_id={APPLICATION_ID}');old.commit()
    with sqlite3.connect(path) as dest:old.backup(dest)
    old.close()
    with Database.open_personal(path,MemorySettings()) as db:
        assert db.connection.execute('PRAGMA user_version').fetchone()[0]==4
        memory_schema.check(db.connection)
    backups=list(tmp_path.glob('*.format3-*.sqlite'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as c:memory_schema.check(c,version=3)
    with Database.open_personal(path,MemorySettings()) as db:memory_schema.check(db.connection)
    assert len(list(tmp_path.glob('*.format3-*.sqlite')))==1


@pytest.mark.parametrize('expiry',[100.,None])
def test_expired_legacy_row_cannot_demote_current_choice(expiry,monkeypatch):
    from naiyue_test_agent.storage.memory_sql import memory_time_schema
    from naiyue_test_agent.storage.memory_sql.writes import MemoryWriter,FIELDS
    import time
    now=time.time();c=legacy();w=MemoryWriter(c)
    for i,kind,at,until in [(1,'rule',now-100*86400,expiry),(2,'decision',now-86400,None)]:
        row=dict.fromkeys(FIELDS);row.update(memory_id=str(i)*32,version=1,scope='personal',kind=kind,status='active',
            title='网关',body=str(i),aliases_json='[]',tags_json='[]',subject='网关',basis='user_declaration',
            created_at=at,updated_at=at,evidence_at=at,last_confirmed_at=at,expires_at=until,gaps_json='[]',reason='')
        w.save(row,[])
    with c:memory_time_schema.upgrade(c)
    current=dict(c.execute('SELECT * FROM naiyue_memories WHERE memory_id=?',('2'*32,)).fetchone())
    assert current['status']=='active' and json.loads(current['gaps_json'])==[]
    assert current['expires_at']>now
