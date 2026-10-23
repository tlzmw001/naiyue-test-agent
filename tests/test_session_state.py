"""State ownership contracts, independent from graph/checkpoint representation."""

import ast
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from native_application_helpers import async_test
from naiyue_test_agent.application import SessionService
from naiyue_test_agent.application.agent_session import AgentSession
from naiyue_test_agent.application.session_state import ActiveRun, SessionState
from naiyue_test_agent.application.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog


def service():
    return SessionService(ModelCatalog('A', {
        name: FakeMessagesListChatModel(responses=[]) for name in ('A', 'B')
    }))


def restored_config():
    return dict(thread_id='saved-thread', model_id='A', thinking_level='model_default',
                workspace_root=None, active_tool_names=(), permission_mode='Default')


def test_new_and_restored_states_use_one_dataclass_and_isolate_mutable_defaults():
    sessions = service()
    fresh = sessions.create_session('A')
    sessions.install_restored_session('saved', **restored_config())
    first, second = sessions._sessions[fresh], sessions._sessions['saved']
    assert isinstance(first, AgentSession) and type(first) is type(second)
    configuration = restored_config()
    configuration.pop('workspace_root')
    first_state, second_state = (SessionState(**configuration, execution_root=None) for _ in range(2))
    assert is_dataclass(first_state) and type(first_state) is type(second_state)
    assert len(fields(first_state)) == 12
    assert not hasattr(first_state, '__getitem__')
    for name in ('session_allow',):
        left, right = getattr(first_state, name), getattr(second_state, name)
        assert left is not right
        left.append('only-first')
        assert not right
    first_run, second_run = ActiveRun('a', None), ActiveRun('b', None)
    for name in ('prompt_parts', 'steering_queue', 'follow_up_queue'):
        left, right = getattr(first_run, name), getattr(second_run, name)
        assert left is not right
        left.append('only-first')
        assert not right
    assert sessions.require('saved').get_thread_id() == 'saved-thread'
    assert set(sessions.loaded_session_ids()) == {fresh, 'saved'}


def test_purpose_snapshots_are_bounded_and_do_not_expose_live_state():
    sessions = service()
    sid = sessions.create_session('A')
    finish = sessions._sessions[sid].snapshot_for_finish()
    assert finish == {'thread_id': sessions.require(sid).get_thread_id(), 'manual_review_required': False}
    finish['manual_review_required'] = True
    assert sessions.require(sid).describe_session()['manual_review_required'] is False
    view = sessions.require(sid).snapshot_for_context()
    assert set(view) == {'thread_id', 'model_id', 'workspace_root', 'active_tool_names',
                         'busy', 'pending_approval', 'manual_review_required', 'project_id', 'execution_root'}
    assert isinstance(view['active_tool_names'], tuple)
    assert sessions.context_snapshot_is_current(sid, view)
    view['model_id'] = 'outside-mutation'
    assert sessions.require(sid).describe_session()['model_id'] == 'A'
    assert not sessions.context_snapshot_is_current(sid, view)
    public = sessions.require(sid).describe_session()
    public['active_tools'].append('outside-mutation')
    assert sessions.require(sid).get_active_tools() == ()


def test_context_snapshot_is_invalidated_by_selection_busy_pending_and_removal():
    sessions = service()
    sid = sessions.create_session('A')
    original = sessions.require(sid).snapshot_for_context()
    sessions.require(sid).select_model('B')
    assert not sessions.context_snapshot_is_current(sid, original)
    current = sessions.require(sid).snapshot_for_context()
    owner, observation_run = sessions._admit(sid, 'context-run', 'message')
    run_id, observation = observation_run.run_id, observation_run.observation
    assert not sessions.context_snapshot_is_current(sid, current)
    owner.replace_pending_approval(observation_run, {'batch_id': 'batch'})
    owner._cleanup_run(observation_run)
    assert not sessions.context_snapshot_is_current(sid, current)
    sessions.remove_session(sid)
    assert not sessions.context_snapshot_is_current(sid, current)


def test_restored_pending_is_detached_and_duplicate_install_does_not_replace_state():
    sessions = service()
    pending = {'batch_id': 'b1', 'items': [{'args': {'path': 'original'}}]}
    sessions.install_restored_session('saved', **restored_config(), pending=pending)
    assert sessions.require('saved').approval_seed_identity() == ('saved-thread', 'b1')
    pending['items'][0]['args']['path'] = 'outside-mutation'
    assert sessions._sessions['saved'].snapshot_for_run()['pending']['items'][0]['args']['path'] == 'original'
    existing = sessions._sessions['saved']
    with pytest.raises(SessionPersistenceError, match='session_already_loaded'):
        sessions.install_restored_session('saved', **restored_config())
    assert sessions._sessions['saved'] is existing
    fresh = sessions.create_session('A')
    assert sessions.require(fresh).approval_seed_identity() is None


def test_delete_guard_holds_only_the_admission_boundary_and_rejects_busy():
    sessions = service()
    sid = sessions.create_session('A')
    with sessions.deletion_guard(sid) as result:
        assert result is None
        assert not sessions._lock.acquire(blocking=False)
    assert sessions._lock.acquire(blocking=False)
    sessions._lock.release()
    owner, observation_run = sessions._admit(sid, 'delete-run', 'message')
    run_id, observation = observation_run.run_id, observation_run.observation
    with pytest.raises(SessionPersistenceError, match='session_recovery_blocked'):
        with sessions.deletion_guard(sid):
            pytest.fail('Busy session must not reserve deletion')
    owner._cleanup_run(observation_run)
    other = sessions.create_session('A')
    sessions._sessions[sid]._run.subagent_instances['root/child'] = {'child_thread_id': 'child-thread'}
    sessions._admit(other, 'other-run', 'message')
    sessions._sessions[other]._run.subagent_instances['root/child'] = {'child_thread_id': 'other-thread'}
    assert sessions.child_thread_ids(sid) == ('child-thread',)
    sessions.remove_session(sid)
    assert not sessions.has_session(sid)
    assert sessions.child_thread_ids(other) == ('other-thread',)


@async_test
async def test_real_persistent_restore_preserves_configuration_but_resets_transient_state(monkeypatch, tmp_path):
    from test_persistent_sessions import build

    database = tmp_path / 'sessions.sqlite'
    async with build(monkeypatch, database) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'save')])
        await app.aset_permission_mode(sid, 'NoAsk')
        thread = app._sessions.require(sid).get_thread_id()
    async with build(monkeypatch, database) as app:
        await app.arestore_session(sid)
        state = app._sessions._sessions[sid]
        assert isinstance(state, AgentSession)
        assert state.get_thread_id() == thread and state.get_permission_mode() == 'NoAsk'
        assert state.snapshot_for_run()['run_id'] is None and not state.is_busy()
        assert state.resolve_permission_context()['session_allow'] == [] and state.resolve_prompt_parts() == []
        assert app._sessions.drain_steering(sid) == [] and app._sessions.finish_run_boundary(sid) == (False, [])
        assert [m.content for m in await app.aget_messages(sid)] == ['save', 'answer']


@pytest.mark.parametrize('filename', [
    'async_application.py', 'async_persistent.py', 'session_context.py',
    'session_persistence.py', 'session_recovery.py',
])
def test_state_consumers_do_not_pierce_service_state_or_lock(filename):
    path = Path(__file__).parents[1] / 'naiyue_test_agent' / 'application' / filename
    tree = ast.parse(path.read_text())
    violations = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        and node.attr in ('_sessions', '_lock', '_persistence', '_subagent_instances')
        and isinstance(node.value, ast.Attribute) and node.value.attr in ('sessions', '_sessions')]
    assert violations == []
