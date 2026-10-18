"""One behavior class owns independent session state and atomic transitions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from naiyue_test_agent.application import SessionService
from naiyue_test_agent.application.errors import SessionBusyError
from naiyue_test_agent.model_access import ModelCatalog
from test_dynamic_tools import definition
from test_session_state import restored_config


def make_service():
    catalog = ModelCatalog('A', {name: FakeMessagesListChatModel(responses=[]) for name in ('A', 'B')})
    catalog._thinking_capabilities['A'] = {'kind': 'openai', 'levels': ('low', 'high')}
    return SessionService(catalog)


def test_new_and_restored_sessions_share_methods_but_not_configuration():
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    first = service.create_session('A')
    service.install_restored_session('saved', **restored_config())
    a, b = service._sessions[first], service._sessions['saved']
    assert isinstance(a, AgentSession) and type(a) is type(b)
    assert type(a).select_model is type(b).select_model
    service.require(first).select_model('B')
    assert a.resolve_model()[0] == 'B' and b.resolve_model()[0] == 'A'
    assert service._catalog.get('B') is a.resolve_model()[1]
    assert service.require('saved').get_thread_id() == 'saved-thread'


def test_run_snapshots_and_allow_rules_do_not_leak_nested_mutation():
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    sid, other = service.create_session('A'), service.create_session('A')
    a, observation_run = service._admit(sid, 'run-a', 'message')
    rid, observation = observation_run.run_id, observation_run.observation
    assert isinstance(a, AgentSession)
    pending = {'batch_id': 'batch', 'items': [{'args': {'path': 'original'}}]}
    a.replace_pending_approval(observation_run, pending)
    rule = {'tool': 'read', 'target': {'path': 'original'}}
    a.remember_approved_rules(observation_run, [rule, rule])
    pending['items'][0]['args']['path'] = 'caller-change'
    rule['target']['path'] = 'caller-change'
    snapshot = a.snapshot_for_run()
    snapshot['pending']['items'][0]['args']['path'] = 'snapshot-change'
    permission = a.resolve_permission_context()
    permission['session_allow'][0]['target']['path'] = 'snapshot-change'
    assert a.snapshot_for_run()['pending']['items'][0]['args']['path'] == 'original'
    assert a.resolve_permission_context()['session_allow'] == [{'tool': 'read', 'target': {'path': 'original'}}]
    assert service.resolve_permission_context(other)['session_allow'] == []
    assert service._sessions[other].snapshot_for_run()['pending'] is None
    a.freeze_run_resources(observation_run, [{'text': 'original'}], 'project rules')
    parts = a.resolve_prompt_parts()
    parts[0]['text'] = 'outside-change'
    assert a.resolve_prompt_parts() == [{'text': 'original'}]


@pytest.mark.parametrize('operation', ['model', 'thinking', 'tools', 'mode'])
def test_configuration_failure_does_not_commit_memory(operation):
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    service.register_tool(definition())
    writes = []
    failure = OSError('synthetic configuration failure')
    def save(*args):
        writes.append(args)
        raise failure
    storage = SimpleNamespace(create=lambda *args: None, select_model=save,
        select_thinking=save, select_active_tools=save, select_permission_mode=save)
    service.bind_persistence(SimpleNamespace(storage=storage))
    sid, other = service.create_session('A'), service.create_session('A')
    assert isinstance(service._sessions[sid], AgentSession)
    previous = service.require(sid).describe_session()
    actions = {'model': lambda: service.require(sid).select_model('B'),
        'thinking': lambda: service.require(sid).select_thinking('A', 'high'),
        'tools': lambda: service.require(sid).set_active_tools([]),
        'mode': lambda: service.require(sid).set_permission_mode('NoAsk')}
    with pytest.raises(OSError) as caught:
        actions[operation]()
    assert caught.value is failure and len(writes) == 1
    assert service.require(sid).describe_session() == previous
    assert service.require(other).describe_session()['model_id'] == 'A'


def test_configuration_io_leaves_state_lock_available_until_commit():
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    entered, release = Event(), Event()
    def save(*args):
        entered.set()
        assert release.wait(5)
    service.bind_persistence(SimpleNamespace(storage=SimpleNamespace(create=lambda *args: None, select_model=save)))
    sid = service.create_session('A')
    assert isinstance(service._sessions[sid], AgentSession)
    with ThreadPoolExecutor(max_workers=2) as workers:
        selection = workers.submit(service.require(sid).select_model, 'B')
        try:
            assert entered.wait(5)
            reading = workers.submit(service.require(sid).describe_session)
            assert reading.result(timeout=5)['model_id'] == 'A'
        finally:
            release.set()
        selection.result(timeout=5)
    assert service.require(sid).describe_session()['model_id'] == 'B'


def test_duplicate_admission_is_atomic_and_stale_transitions_are_rejected():
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    sid = service.create_session('A')
    barrier = Barrier(2)
    def admit(rid):
        barrier.wait(timeout=5)
        try:
            return service._admit(sid, rid, 'message')
        except SessionBusyError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as workers:
        attempts = list(workers.map(admit, ['one', 'two']))
    successful = [result for result in attempts if isinstance(result, tuple)]
    assert len(successful) == 1
    assert sum(isinstance(result, SessionBusyError) for result in attempts) == 1
    a, run = successful[0]
    rid, observation = run.run_id, run.observation
    assert isinstance(a, AgentSession)
    for transition in (lambda: a.release_run('stale'),
                       lambda: a.replace_pending_approval('stale', None),
                       lambda: a.freeze_run_resources('stale', [], None)):
        with pytest.raises(SessionBusyError):
            transition()
    assert service.require(sid).describe_session()['run_id'] == rid


def test_closing_controls_keeps_busy_until_persistent_finish_releases_it():
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    service.bind_persistence(SimpleNamespace(storage=SimpleNamespace(create=lambda *args: None)))
    sid = service.create_session('A')
    a, observation_run = service._admit(sid, 'run-a', 'message')
    rid, observation = observation_run.run_id, observation_run.observation
    assert isinstance(a, AgentSession)
    service.require(sid).steer('steering')
    service.require(sid).follow_up('follow-up')
    assert a._cleanup_run(observation_run) is False
    assert a.is_busy() and a._run is observation_run and not observation_run.controls_open
    assert service.drain_steering(sid) == []
    a.release_run(observation_run)
    assert not a.is_busy() and service.require(sid).describe_session()['run_id'] is None


def test_invalid_resume_does_not_start_a_run_or_remember_allow():
    from naiyue_test_agent.application.agent_session import AgentSession
    service = make_service()
    pending = {'batch_id': 'batch', 'requested_run_id': 'old',
               'items': [{'tool_call_id': 'call', 'tool_name': 'read', 'target': 'file', 'workspace': None}]}
    service.install_restored_session('saved', **restored_config(), pending=pending)
    a = service._sessions['saved']
    assert isinstance(a, AgentSession)
    events = []
    service.subscribe_events(events.append)
    with pytest.raises(ValueError, match='One approve/reject'):
        service._admit('saved', 'invalid', 'approval_resume', decisions=[])
    assert not a.is_busy() and a._run is None and events == []
    assert a.resolve_permission_context()['session_allow'] == []
    assert a.snapshot_for_run()['pending'] == pending
