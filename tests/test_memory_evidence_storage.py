"""S2 persisted span and verification contracts, using real stores."""
from test_memory_batches import archive, save
import pytest
from langchain_core.messages import HumanMessage


def test_two_spans_are_both_resolved(archive):
    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
    from naiyue_test_agent.application.memory.memory_sources import resolve
    from test_memory_input_units import build
    save(archive,[HumanMessage(id='u',content='本地用.venv。CI保持原样。',additional_kwargs={'naiyue_human_input':True})])
    refs=bind_proposal_evidence({'sources':[{'evidence_id':'E1'},{'evidence_id':'E2'}]},build(archive))['sources']
    from naiyue_test_agent.application.memory.memory_sources import SECRET
    project=archive.sessions.get('session')['project_id']
    with archive.memory.batch_reader(SECRET) as reader:
        rows=resolve(reader,project,'session',refs,1001.)
    assert len(rows)==2
    assert all(r['verified_at']==1001. and r['evidence_format']==1 for r in rows)


def test_new_store_has_evidence_columns(archive):
    columns={r[1] for r in archive.database.connection.execute('PRAGMA table_info(naiyue_memory_sources)')}
    assert {'evidence_format','verified_at','user_origin','tool_success'}<=columns


from test_memory_lifecycle import wiki as memory, storage_memory, source
from test_memory_wiki_mutations import call,args
from test_memory_wiki_pagination import decode_content


def test_partial_update_keeps_dates_and_body(memory):
    from pydantic import ValidationError
    storage,access,_,_=memory
    first=source(access,'caller',at=1000.)
    old=call(memory,args(sources=[first]))['item']
    before=storage.database.connection.execute('SELECT body,status,expires_at,evidence_at,last_confirmed_at FROM naiyue_memories').fetchone()
    newer=source(access,'caller',text='本地继续用.venv',at=2000.)
    request=dict(action='update',scope='workspace',memory_id=old['memory_id'],expected_version=1,
        change={'mode':'supplement'},sources=[newer])
    item=call(memory,request)['item']
    assert item['version']==2
    after=storage.database.connection.execute('SELECT body,status,expires_at,evidence_at,last_confirmed_at FROM naiyue_memories').fetchone()
    assert tuple(after)==tuple(before)
    with pytest.raises(ValidationError):
        call(memory,dict(request,expected_version=2,change={'mode':'supplement','body':'更改整体正文'}))
    assert storage.database.connection.execute('SELECT version FROM naiyue_memories').fetchone()[0]==2


def test_personal_format2_upgrade_and_rollback(tmp_path,monkeypatch):
    import sqlite3
    import pytest
    from naiyue_test_agent.storage.database import Database
    from naiyue_test_agent.storage.personal_database import APPLICATION_ID
    from naiyue_test_agent.storage.memory_sql import memory_schema,memory_lifecycle_schema,memory_evidence_schema,lexical
    from naiyue_test_agent.memory_settings import MemorySettings
    path=tmp_path/'personal.sqlite'
    with sqlite3.connect(path) as c:
        lexical.configure(c);memory_schema.create(c);memory_lifecycle_schema.upgrade(c)
        c.execute('PRAGMA user_version=2');c.execute(f'PRAGMA application_id={APPLICATION_ID}')
    original=memory_evidence_schema.upgrade
    def fail(c):
        c.execute('ALTER TABLE naiyue_memory_sources ADD COLUMN partial TEXT')
        raise RuntimeError('injected')
    with monkeypatch.context() as patch:
        patch.setattr(memory_evidence_schema,'upgrade',fail)
        with pytest.raises(RuntimeError,match='injected'):Database.open_personal(path,MemorySettings())
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==2
        assert 'partial' not in {r[1] for r in c.execute('PRAGMA table_info(naiyue_memory_sources)')}
    with Database.open_personal(path,MemorySettings()) as db:
        memory_schema.check(db.connection,version=4)
        assert db.connection.execute('PRAGMA user_version').fetchone()[0]==4
    assert list(tmp_path.glob('*.format2-*.sqlite'))


def test_evidence_group_constraint_and_historical_message_identity(storage_memory):
    # Historical schema remains readable for backup/migration; test its original
    # constraint directly, without reviving the retired product write API.
    import sqlite3
    from test_memory_readonly import seed,add_source
    storage,access,workspace,_=storage_memory
    mid=seed(storage.database.connection,1,workspace_id=workspace)
    add_source(storage.database.connection,mid,workspace,quote='使用.venv')
    with storage.memory.edit_with_sources(storage.memory) as (writer,reader):
        writer._connection.execute('UPDATE naiyue_memory_sources SET evidence_format=1,user_origin=1,tool_success=0,verified_at=1000')
        assert writer.has_message(mid,writer.sources(mid)[0])
        with pytest.raises(sqlite3.IntegrityError):
            writer._connection.execute('UPDATE naiyue_memory_sources SET verified_at=NULL WHERE memory_id=?',(mid,))


@pytest.mark.parametrize('confirmation',['partial','unknown'])
def test_partial_candidate_receipt_matches_saved_status(memory,confirmation):
    _,access,_,_=memory
    request=args(sources=[source(access,'caller',at=1000.)])
    request['proposal'].update(intent='candidate',gaps=['等待确认'])
    old=call(memory,request)['item']
    ref=source(access,'caller',text='本地继续用.venv' if confirmation=='partial' else '补充之前的记录',at=2000.)
    result=call(memory,dict(action='update',scope='workspace',memory_id=old['memory_id'],expected_version=1,
        change={'mode':'supplement'},sources=[ref]))
    assert result['status']=='still_candidate' and result['item']['status']=='candidate'
    read,=decode_content(call(memory,dict(action='read',scope='workspace',status='candidate',memory_id=old['memory_id'])))['entries']
    assert read['gaps']==['等待确认'] and read['evidence_at']==1000.
    assert read['body']=='使用.venv' and read['version']==2


def test_source_boolean_read_mapping_preserves_legacy_null(storage_memory):
    from test_memory_readonly import seed,add_source
    storage,access,workspace,_=storage_memory
    mid=seed(storage.database.connection,1,workspace_id=workspace)
    add_source(storage.database.connection,mid,workspace,quote='使用.venv')
    with storage.memory.edit_with_sources(storage.memory) as (writer,_):
        writer._connection.execute('UPDATE naiyue_memory_sources SET evidence_format=1,user_origin=1,tool_success=0,verified_at=1000')
        row=writer.sources(mid)[0]
        assert row['user_origin'] is True and row['tool_success'] is False
        writer._connection.execute('UPDATE naiyue_memory_sources SET evidence_format=NULL,user_origin=NULL,tool_success=NULL,verified_at=NULL')
        row=writer.sources(mid)[0]
        assert row['user_origin'] is None and row['tool_success'] is None


def test_whole_rewrite_requires_fresh_matching_message_in_commit(memory):
    from naiyue_test_agent.contracts.memory import MemoryReadError
    storage,access,_,_=memory
    ref=source(access,'caller',at=1000.)
    old=call(memory,args(sources=[ref]))['item']
    proposal=args()['proposal'];proposal['body']='改用另一种环境'
    request=dict(action='update',scope='workspace',memory_id=old['memory_id'],expected_version=1,
        change={'mode':'replace','proposal':proposal},sources=[ref])
    with pytest.raises(MemoryReadError,match='memory_new_evidence_required'):call(memory,request,now=2000.)
    row=storage.database.connection.execute('SELECT version,body,evidence_at FROM naiyue_memories').fetchone()
    assert tuple(row)==(1,'使用.venv',1000.)
