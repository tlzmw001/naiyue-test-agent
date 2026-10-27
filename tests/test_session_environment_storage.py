"""Persist project identity without inventing a Host directory for Git projects."""
from contextlib import closing
import json
import sqlite3
from uuid import uuid4

import pytest

from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.execution_storage import root_from_row
from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA,check_snapshot
from naiyue_test_agent.storage.errors import SessionPersistenceError


def test_git_identity_and_sandbox_setup_survive_reopen_and_backup_check(tmp_path):
    path=tmp_path/'sessions.sqlite';project=str(uuid4())
    setup=dict(model_id='M',include_untracked=['new.txt'],input_id='a'*32,binding_id='b'*32)
    with closing(ProjectStore(path)) as store:
        store.sessions.bind_git_project(project)
        store.sessions.create('S',dict(thread_id='T',model_id='M',workspace_root=None,project_id=project,
            execution_root_kind=None,execution_binding='sandbox',execution_setup=setup,
            active_tool_names=[],permission_mode='Default'))
        assert store.sessions.get_workspace_binding()==dict(workspace_id=project,root_path=None)
        row=store.sessions.get('S')
        assert root_from_row(row) is None
        assert json.loads(row['execution_setup_json'])==setup
        assert CURRENT_SCHEMA==20
    assert check_snapshot(path)['workspace_id']==project
    with closing(ProjectStore(path,runtime_profile=project)) as store:
        assert store.sessions.get('S')['execution_binding']=='sandbox'
        assert not store.database.read_rows('SELECT * FROM naiyue_workspace')


@pytest.mark.parametrize('value',[None,'{}','{"binding_id":"../outside"}'])
def test_sandbox_without_valid_setup_cannot_be_interpreted_as_no_directory_chat(value):
    row=dict(project_id=str(uuid4()),execution_binding='sandbox',execution_root_kind=None,
        workspace_root=None,execution_setup_json=value)
    with pytest.raises(SessionPersistenceError,match='invalid_execution_identity'):
        root_from_row(row)


def test_schema19_local_binding_migrates_without_changing_its_location(tmp_path):
    path=tmp_path/'sessions.sqlite';project=str(uuid4())
    with closing(ProjectStore(path)) as store:
        store.sessions.bind_workspace(project,tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE naiyue_project_identity')
        connection.execute('ALTER TABLE naiyue_sessions DROP COLUMN execution_setup_json')
        connection.execute('ALTER TABLE naiyue_subagent_instances DROP COLUMN environment_binding_id')
        connection.execute('UPDATE naiyue_store_info SET version=19')
    with closing(ProjectStore(path,runtime_profile=project)) as store:
        assert store.sessions.get_workspace_binding()==dict(workspace_id=project,root_path=str(tmp_path))
        assert store.database.read_rows('SELECT * FROM naiyue_project_identity')==[
            dict(project_id=project,source_kind='local')]
        assert store.database.read_rows('SELECT version FROM naiyue_store_info')==[dict(version=20)]


@pytest.mark.parametrize('profile',['local','chats'])
def test_schema20_rejects_conflicting_project_identity_before_runtime_open(tmp_path,profile):
    from naiyue_test_agent.contracts.projects import ProjectError
    path=tmp_path/'identity.sqlite';project=str(uuid4())
    with closing(ProjectStore(path)) as store:
        if profile=='local':store.sessions.bind_workspace(project,tmp_path)
        else:store.database.connection.execute("INSERT INTO naiyue_project_identity VALUES(?,'local')",(project,));store.database.connection.commit()
    if profile=='local':
        with sqlite3.connect(path) as connection:connection.execute('UPDATE naiyue_project_identity SET project_id=?',(str(uuid4()),))
    with pytest.raises(ProjectError,match='project_identity_conflict|chat_database_mismatch'):
        with ProjectStore(path,runtime_profile=project if profile=='local' else 'chats'):pass


def test_git_project_session_listing_and_reorder_keep_none_as_no_host_path(tmp_path):
    project=str(uuid4())
    with closing(ProjectStore(tmp_path/'git.sqlite')) as store:
        store.sessions.bind_git_project(project)
        store.sessions.create('S',dict(thread_id='T',model_id='M',workspace_root=None,project_id=project,
            execution_binding='sandbox',execution_root_kind=None,active_tool_names=[],permission_mode='Default',
            execution_setup=dict(model_id='M',include_untracked=[],input_id='a'*32,binding_id='b'*32)))
        assert [row['session_id'] for row in store.sessions.list_workspace_sessions(None)]==['S']
        store.sessions.reorder(None,['S'])
