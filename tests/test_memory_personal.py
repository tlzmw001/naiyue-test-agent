"""Personal SQLite concurrency/format guards use disposable paths only."""

import sqlite3

import pytest

from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.memory_settings import MemorySettings
from test_memory_readonly import seed, query, memory


def test_two_connections_observe_commits_and_delete_full(tmp_path):
    path = tmp_path / 'personal/memory.sqlite'
    with Database.open_personal(path, MemorySettings()) as one, Database.open_personal(path, MemorySettings()) as two:
        assert one.connection.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert one.connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        item = seed(one.connection, 1, scope='personal')
        assert two.connection.execute('SELECT memory_id FROM naiyue_memories').fetchone()[0] == item
        with two.connection:
            two.connection.execute('UPDATE naiyue_memories SET version=2,body=? WHERE memory_id=?', ('new text',item))
        assert one.connection.execute('SELECT body FROM naiyue_memories').fetchone()[0] == 'new text'
        assert one.connection.execute("SELECT count(*) FROM naiyue_memory_search WHERE memory_version=2").fetchone()[0] == 1


def test_unknown_format_is_not_changed_and_unsafe_directory_is_rejected(tmp_path):
    path = tmp_path / 'unknown.sqlite'
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE unrelated(secret TEXT)')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='memory_invalid_format'):Database.open_personal(path,MemorySettings())
    assert path.read_bytes() == before
    target = tmp_path / 'elsewhere';target.mkdir()
    link = tmp_path / 'linked';link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):Database.open_personal(link/'memory.sqlite',MemorySettings())
    assert not (target/'memory.sqlite').exists()


def test_old_and_new_runtimes_share_delete_and_do_not_convert_existing_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3,'sqlite_version_info',(3,50,0))
    path = tmp_path / 'rollback.sqlite'
    with Database.open_personal(path,MemorySettings()) as personal:
        assert personal.connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        monkeypatch.setattr(sqlite3,'sqlite_version_info',(3,53,4))
        with Database.open_personal(path,MemorySettings()) as newer:
            assert newer.connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
            assert personal.connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    with sqlite3.connect(path) as c:assert c.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    with pytest.raises(ValueError,match='memory_personal_mode_unavailable'):Database.open_personal(path,MemorySettings())
    with sqlite3.connect(path) as c:assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'


def test_personal_busy_is_diagnostic_and_later_query_can_retry(memory):
    _,access,_,root=memory
    access.settings=MemorySettings(busy_timeout_ms=0)
    path=root/'data/personal/memory.sqlite'
    with Database.open_personal(path,MemorySettings()) as owner:
        owner.connection.execute('BEGIN IMMEDIATE')
        from naiyue_test_agent.application.memory.memory_access import MemoryReadError
        with pytest.raises(MemoryReadError,match='memory_busy'):query(memory,action='list',scope='personal')
        owner.connection.rollback()
        assert query(memory,action='list',scope='personal')['items']==[]


@pytest.mark.parametrize('statement', ["CREATE VIEW foreign_view AS SELECT 'unrelated' AS value",
    'PRAGMA user_version=42', 'PRAGMA application_id=42'])
def test_nonempty_or_marked_unknown_database_is_never_initialized(tmp_path,statement):
    from contextlib import closing
    path=tmp_path/'unknown.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(statement);connection.commit()
    before=path.read_bytes()
    with pytest.raises(ValueError,match='memory_invalid_format'):Database.open_personal(path,MemorySettings())
    assert path.read_bytes()==before


@pytest.mark.parametrize('problem,code',[('format','memory_invalid_format'),('wal','memory_personal_mode_unavailable')])
def test_personal_guard_returns_actionable_safe_query_error(memory,problem,code):
    from contextlib import closing
    from naiyue_test_agent.application.memory.memory_access import MemoryReadError
    _,_,_,root=memory
    path=root/'data/personal/memory.sqlite';path.parent.mkdir(parents=True)
    if problem=='wal':
        with Database.open_personal(path,MemorySettings()):pass
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA journal_mode=WAL' if problem=='wal' else 'CREATE VIEW foreign_view AS SELECT 1')
        connection.commit()
    with pytest.raises(MemoryReadError,match=code):query(memory,action='list',scope='personal')
