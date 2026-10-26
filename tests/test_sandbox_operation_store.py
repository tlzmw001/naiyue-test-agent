"""Local facts and committed archive positions survive service reopening."""
from contextlib import closing
from pathlib import Path
import os
import pytest


def test_registry_v3_upgrade_preserves_environment_and_operation_fingerprints(tmp_path):
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.sandbox_operations import SandboxOperations
    from naiyue_test_agent.contracts.sandbox import SandboxError
    path=tmp_path/'registry.sqlite'
    with closing(ProjectRegistry(path)) as registry:
        repo=SandboxOperations(registry.database)
        row,new=repo.reserve('B','P','S','a'*32,'shell',{'command':'one'},{'offset':0})
        assert new
        repo.save('B','a'*32,{'state':'unknown','metadata':{'offset':3}})
    with closing(ProjectRegistry(path)) as registry:
        repo=SandboxOperations(registry.database)
        row,new=repo.reserve('B','P','S','a'*32,'shell',{'command':'one'}, {})
        assert not new and row['metadata']['offset']==3 and row['state']=='unknown'
        with pytest.raises(SandboxError,match='env_operation_conflict'):
            repo.reserve('B','P','S','a'*32,'shell',{'command':'two'}, {})
        with pytest.raises(SandboxError,match='env_operation_not_found'):repo.get('OTHER','a'*32)


def test_archive_replay_overwrites_uncommitted_tail_and_rejects_replacement(tmp_path):
    from naiyue_test_agent.execution.sandbox_output import SandboxOutput
    output=SandboxOutput(tmp_path,'a'*32)
    meta=output.prepare(dict(offset=0,saved_bytes=0,max_archive_bytes=100,tail_limit=100))
    first=output.append(meta,dict(offset=0,next_offset=3,observed_bytes=3,gap=False,data=b'one'))
    output.append(first,dict(offset=3,next_offset=6,observed_bytes=6,gap=False,data=b'two'))
    # Crash after bytes were saved but before the second offset commit.
    resumed=output.append(first,dict(offset=3,next_offset=6,observed_bytes=6,gap=False,data=b'two'))
    assert resumed['offset']==6 and Path(resumed['archive_path']).read_bytes()==b'onetwo'
    path=Path(resumed['archive_path']);path.unlink();path.write_bytes(b'other')
    with pytest.raises(OSError):output.append(resumed,dict(offset=6,next_offset=7,observed_bytes=7,gap=False,data=b'!'))


def test_real_version3_registry_upgrade_keeps_project_and_environment_rows(tmp_path):
    import sqlite3
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    from naiyue_test_agent.contracts.sandbox import SandboxRecord
    from naiyue_test_agent.contracts.projects import RegisteredProject,LocalSource
    path=tmp_path/'projects.sqlite'
    project=RegisteredProject('P','project',LocalSource(tmp_path),tmp_path/'sessions.sqlite')
    environment=SandboxRecord('B','P','S','running','D','container','volume','image',{})
    with closing(ProjectRegistry(path)) as registry:
        registry.reserve('seed','import_legacy',{},project,new=True)
        EnvironmentRegistry(registry.database).reserve(environment)
        before=registry.operation('seed')
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE git_deliveries')
        connection.execute('DROP TABLE git_inputs')
        connection.execute('DROP TABLE sandbox_operations');connection.execute('PRAGMA user_version=3')
    with closing(ProjectRegistry(path)) as registry:
        assert registry.operation('seed')==before
        assert EnvironmentRegistry(registry.database).get('B')==environment
        assert registry.database.read_rows('PRAGMA user_version')==[{'user_version':6}]
