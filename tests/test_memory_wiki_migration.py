from contextlib import closing
import json
import sqlite3
import pytest
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.memory import MemoryRepo
from naiyue_test_agent.storage.memory_sql import memory_schema, memory_wiki_schema
from naiyue_test_agent.storage.memory_sql.writes import MemoryWriter
from naiyue_test_agent.storage.backup import validate_backup
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_readonly import memory
from test_memory_lifecycle import source
from memory_wiki_helpers import NOW, record, evidence
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter


def legacy(c,workspace_id):
    data=record('unused',workspace_id=workspace_id)
    row=data['row'];row.pop('page_id');row['kind']='convention'
    MemoryWriter(c).save(row,data['sources'])
    c.execute('INSERT INTO naiyue_memory_suppression VALUES(?,?)',('suppressed',20.))
    return row


def test_upgrade_isolates_old_data_preserves_history_and_advances_progress(memory):
    store,access,identity,root=memory
    source(access,'caller',at=50)
    store.memory.ensure('caller');store.memory.mark_directory('caller')
    # Archive helper writes a checkpoint reference; complete it with the real saver schema.
    from langgraph.checkpoint.sqlite import SqliteSaver
    SqliteSaver(store.database.connection).setup()
    with store.database.transaction(write=True) as c:
        for head in c.execute('SELECT thread_id,checkpoint_ns,checkpoint_id FROM naiyue_chat_sync').fetchall():
            c.execute('INSERT INTO checkpoints VALUES(?,?,?,NULL,?,?,?)',(*tuple(head),'json',b'checkpoint',b'metadata'))
        old=legacy(c,identity)
        c.execute('''INSERT INTO naiyue_memory_overviews VALUES(?,?,0,1,?,1,1,?,NULL,0,'[]',0,?)''',
            ('caller','caller',json.dumps({'goal':'原概览'}),'task','saved'))
        history=[tuple(r) for r in c.execute('SELECT * FROM naiyue_chat_messages')]
        overview=[tuple(r) for r in c.execute('SELECT * FROM naiyue_memory_overviews')]
    result=store.memory.prepare_wiki(now=NOW)
    assert validate_backup(store.path,result['backup']['backup_id'])['source_schema']==25
    with store.database.transaction() as c:
        memory_wiki_schema.check(c)
        assert c.execute('SELECT version FROM naiyue_store_info').fetchone()[0]==26
        assert c.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM naiyue_memory_search').fetchone()[0]==0
        assert c.execute('SELECT memory_id FROM legacy143bm_memories').fetchone()[0]==old['memory_id']
        assert [tuple(r) for r in c.execute('SELECT * FROM naiyue_chat_messages')]==history
        assert [tuple(r) for r in c.execute('SELECT * FROM naiyue_memory_overviews')]==overview
        assert c.execute('SELECT source_hash FROM naiyue_memory_suppression').fetchone()[0]=='suppressed'
        progress=dict(c.execute('SELECT * FROM naiyue_memory_progress').fetchone())
        assert progress['directory_seen']==1 and progress['next_sequence']==2
        assert progress['accepted_turns']==progress['completed_turns']==1
        assert progress['accepted_chars']==progress['completed_chars']>0
    assert store.memory.prepare_wiki(now=NOW+1)==dict(generation_id=result['generation_id'],backup=None)


def test_migration_failure_rolls_back_ddl_and_keeps_backup(memory,monkeypatch):
    store,_,identity,_=memory
    with store.database.transaction(write=True) as c:legacy(c,identity)
    error=RuntimeError('injected migration interruption')
    def fail(c,**kwargs):
        c.execute('CREATE TABLE incomplete(value TEXT)')
        raise error
    monkeypatch.setattr(memory_wiki_schema,'create',fail)
    with pytest.raises(RuntimeError) as caught:store.memory.prepare_wiki(now=NOW)
    assert caught.value is error
    with store.database.transaction() as c:
        memory_schema.check(c,version=4)
        assert c.execute('SELECT version FROM naiyue_store_info').fetchone()[0]==25
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name IN ('incomplete','legacy143bm_memories')").fetchone() is None
    assert len(list(store.path.with_name(store.path.name+'.backups').glob('*/database.sqlite')))==1


def test_backup_failure_does_not_begin_migration(memory,monkeypatch):
    from naiyue_test_agent.storage import backup
    store,_,_,_=memory
    def fail(*args,**kwargs):raise OSError('injected backup failure')
    monkeypatch.setattr(backup,'create_backup',fail)
    with pytest.raises(OSError):store.memory.prepare_wiki(now=NOW)
    with store.database.transaction() as c:memory_schema.check(c,version=4)


def test_personal_upgrade_and_scope_evidence_are_preserved(tmp_path):
    path=tmp_path/'personal.sqlite'
    with Database.open_personal(path,MemorySettings()) as database:
        repo=MemoryRepo(database)
        result=repo.prepare_wiki(now=NOW)
        with closing(sqlite3.connect(result['backup'])) as old:
            old.row_factory=sqlite3.Row
            memory_schema.check(old,version=4)
        assert database.connection.execute('PRAGMA user_version').fetchone()[0]==5
        assert database.connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        assert database.connection.execute('PRAGMA synchronous').fetchone()[0]==2
        p=WikiWriter.new_page('collaboration','讲解偏好','一般学习习惯')
        m=record(p['page_id'],scope='personal',workspace_id=None)
        with repo.wiki_edit('personal',None,now=NOW,capacity=30) as writer:writer.apply(pages=[p],memories=[m])
        changed=dict(m,row=dict(m['row'],version=2),expected_version=1,sources=[evidence(number=2)])
        with pytest.raises(MemoryReadError,match='memory_scope_evidence_required'):
            with repo.wiki_edit('personal',None,now=NOW,capacity=30) as writer:
                writer.apply(pages=[dict(p,expected_version=1)],memories=[changed])
        current=repo.wiki_members('personal',None,p['page_id'],now=NOW)
        assert current[0]['version']==1
        assert repo.wiki_sources([(m['row']['memory_id'],1)])[0]['supports_scope']==1


def test_default_startup_still_creates_legacy_format(tmp_path):
    with ProjectStore(tmp_path/'unchanged.sqlite') as store:
        assert store.database.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0]==25
    with Database.open_personal(tmp_path/'unchanged-personal.sqlite',MemorySettings()) as db:
        assert db.connection.execute('PRAGMA user_version').fetchone()[0]==4


def test_wiki_source_reader_refuses_legacy_generation(memory):
    store,_,identity,_=memory
    with store.database.transaction(write=True) as c:old=legacy(c,identity)
    with pytest.raises(MemoryReadError,match='memory_scope_mismatch'):
        store.memory.wiki_sources([(old['memory_id'],1)])


def test_new_format_validation_rejects_missing_required_index(memory):
    store,_,_,_=memory
    store.memory.prepare_wiki(now=NOW)
    with store.database.transaction(write=True) as c:c.execute('DROP INDEX wiki_members')
    with pytest.raises(ValueError,match='memory_invalid_format'):
        store.memory.prepare_wiki(now=NOW)


def test_wiki_cutover_backup_restores_previous_format_with_store_owners_closed(tmp_path):
    from naiyue_test_agent.storage.restore import restore_backup
    from naiyue_test_agent.workspaces import prepare_workspace

    location = prepare_workspace(tmp_path, data_dir=tmp_path/'data', database_path=tmp_path/'state.sqlite')
    workspace = location.workspace_id
    with ProjectStore(location.database_path) as store:
        store.sessions.bind_workspace(workspace, tmp_path)
        with store.database.transaction(write=True) as connection:
            old = legacy(connection, workspace)
        migrated = store.memory.prepare_wiki(now=NOW)
        backup_id = migrated['backup']['backup_id']
        assert store.database.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 26
        page = WikiWriter.new_page('collaboration', '确认新格式写入', '')
        new = record(page['page_id'], workspace_id=workspace)
        with store.memory.wiki_edit('workspace', workspace, now=NOW, capacity=30) as writer:
            writer.apply(pages=[page], memories=[new])

    reviewed = []

    def confirm(preview):
        reviewed.append((preview['source_schema'], preview['backup_id']))
        return backup_id

    restore_backup(location, backup_id, confirm=confirm)
    assert reviewed == [(25, backup_id)]
    with ProjectStore(location.database_path, require_existing=True) as restored:
        connection = restored.database.connection
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 25
        restored_ids = [row[0] for row in connection.execute('SELECT memory_id FROM naiyue_memories')]
        assert restored_ids == [old['memory_id']]
        assert new['row']['memory_id'] not in restored_ids
        assert connection.execute('SELECT source_hash FROM naiyue_memory_suppression').fetchone()[0] == 'suppressed'
