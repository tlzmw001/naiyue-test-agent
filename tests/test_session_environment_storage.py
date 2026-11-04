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




@pytest.mark.parametrize('value',[None,'{}','{"binding_id":"../outside"}'])
def test_sandbox_without_valid_setup_cannot_be_interpreted_as_no_directory_chat(value):
    row=dict(project_id=str(uuid4()),execution_binding='sandbox',execution_root_kind=None,
        workspace_root=None,execution_setup_json=value)
    with pytest.raises(SessionPersistenceError,match='unsupported_execution_binding'):
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
        assert store.database.read_rows('SELECT version FROM naiyue_store_info')==[dict(version=23)]


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
