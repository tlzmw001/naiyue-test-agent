"""Formal application/authorization paths for JSON Schema tools."""
import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime.tool_execution.authorization import ExecutionAuthorizationMiddleware
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from test_json_schema_tools import definition, VALID
from test_permission_pipeline import build
from test_dynamic_tools import DynamicModel


def test_invalid_json_permission_returns_paired_not_executed_result():
    call = {'id': 'bad', 'name': 'schema_probe', 'args': {}, 'validation': 'invalid',
            'validation_feedback': 'Invalid schema_probe arguments: required check failed.'}
    request = SimpleNamespace(state={'tool_execution': {'calls': [call]}},
                              tool_call={'id': 'bad', 'name': 'schema_probe', 'args': {}})
    result = ExecutionAuthorizationMiddleware(PermissionSettings())._authorize(request)
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == 'bad' and result.status == 'error'
    assert result.additional_kwargs == {'naiyue_not_executed': 'invalid_arguments'}


@pytest.mark.parametrize('case', ['approve', 'reject', 'allow', 'deny', 'invalid', 'artifact'])
@async_test
async def test_current_application_json_permission_and_runtime(case, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='naiyue_test_agent.agent_runtime')
    seen = []
    args = {} if case == 'invalid' else VALID
    spec = definition(seen=seen, result_format='content_and_artifact' if case == 'artifact' else 'content')
    call = {'name': 'schema_probe', 'args': args, 'id': 'original-call'}
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[call]), AIMessage(content='done')])
    rules = {case: [{'tool': 'schema_probe'}]} if case in ('allow', 'deny') else {}
    app = await build(monkeypatch, model, [spec], **rules)
    sid = await app.acreate_session('A')
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(sid, [('user', 'exercise JSON contract')])
    if case in ('approve', 'reject', 'artifact'):
        assert len(result['approval_requests']) == 1 and seen == []
        assert result['tool_execution']['calls'][0]['subject']['args'] == VALID
        result = await app.aresume_tool_approval(sid, ['reject' if case == 'reject' else 'approve'])
    else:
        assert not result.get('approval_requests')
    output = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    success = case in ('approve', 'allow', 'artifact')
    assert output.tool_call_id == 'original-call'
    assert output.status == ('success' if success else 'error')
    assert len(seen) == int(success)
    observations = [r.model_request_context for r in caplog.records if hasattr(r, 'model_request_context')]
    assert observations and all(row['schema_status'] == 'available' and row['tool_schema_bytes'] > 0
                                and row['tool_schema_sha256'] for row in observations)
    assert 'model_request_tool_schema_unavailable' not in caplog.text
    if success:
        assert seen[0]['args'] == VALID and seen[0]['session_id'] == sid
        assert 'runtime' not in seen[0]['args']
    if case == 'artifact':
        assert output.artifact == {'args': VALID}
    if case == 'invalid':
        assert 'Invalid schema_probe arguments' in output.content
        event = next(e for e in events if e.event_type == 'tool_call_finished')
        assert event.data.outcome == 'not_executed' and event.data.reason == 'invalid_arguments'
        assert not any(hasattr(row, 'tool_error') for row in caplog.records)


@pytest.mark.parametrize('rules', [
    {'allow': [{'tool': 'schema_probe', 'args': {'note': 'value'}}]},
    {'deny': [{'tool': 'schema_probe', 'path': '${workspace}/private/**'}]},
    {'allow': [{'tool': '*', 'args': {'note': 'value'}}]},
])
@async_test
async def test_unsupported_json_rules_fail_before_model_acquisition(rules, monkeypatch):
    from naiyue_test_agent import application
    from naiyue_test_agent.settings import AppSettings
    from native_application_helpers import build_application
    reached = []
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: reached.append('model'))
    with pytest.raises(ValueError):
        await build_application('synthetic', tools=[definition()], settings=AppSettings.model_validate({'permissions': rules}),
                                system_prompt='', response_format=None)
    assert reached == []


@async_test
async def test_wildcard_file_rules_keep_their_existing_scope(monkeypatch):
    from naiyue_test_agent.tools import create_session_read_tool
    app = await build(monkeypatch, DynamicModel(responses=[]), [create_session_read_tool(), definition()],
                      deny=[{'tool': '*', 'path': '${workspace}/private/**'}])
    assert len(app.get_registered_tools()) == 2


@async_test
async def test_invalid_json_waits_for_existing_batch_approval_and_does_not_leak_values(monkeypatch, caplog):
    seen = []
    bad = {'choice': {'kind': 'count', 'value': 'PRIVATE_SECRET_VALUE'}}
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'schema_probe', 'id': 'invalid', 'args': bad},
        {'name': 'schema_probe', 'id': 'valid', 'args': VALID}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [definition(seen=seen)])
    sid = await app.acreate_session('A')
    events = []; app.subscribe_events(events.append)
    paused = await app.arun(sid, [('user', 'mixed batch')])
    assert [r['tool_call_id'] for r in paused['approval_requests']] == ['valid']
    assert not any(isinstance(m, ToolMessage) for m in paused['messages']) and seen == []
    result = await app.aresume_tool_approval(sid, ['approve'])
    outputs = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.status) for m in outputs] == [('invalid', 'error'), ('valid', 'success')]
    assert 'PRIVATE_SECRET_VALUE' not in outputs[0].content + caplog.text
    assert len(seen) == 1 and seen[0]['args'] == VALID
    assert next(e.data.outcome for e in events if e.event_type == 'tool_call_finished'
                and e.data.tool_call_id == 'invalid') == 'not_executed'


@async_test
async def test_approval_binding_is_exact_and_consumed_once(monkeypatch):
    from naiyue_test_agent.tools.permissions import PermissionMechanismError
    seen = []
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'schema_probe', 'id': 'approved', 'args': VALID}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [definition(seen=seen)])
    original = ExecutionAuthorizationMiddleware._authorize
    failures = []
    def checked(self, request):
        authorized = original(self, request)
        if request.tool_call['id'] == 'approved':
            changed = request.override(tool_call={**request.tool_call, 'args': {**VALID, 'note': 'different'}})
            with pytest.raises(PermissionMechanismError):
                original(self, changed)
            with pytest.raises(PermissionMechanismError):
                original(self, request)
            # The real tool receives the exact already-authorized request once.
            failures.append('changed-and-replayed-rejected')
        return authorized
    monkeypatch.setattr(ExecutionAuthorizationMiddleware, '_authorize', checked)
    sid = await app.acreate_session('A')
    paused = await app.arun(sid, [('user', 'approve')])
    assert paused['approval_requests'] and not seen
    await app.aresume_tool_approval(sid, ['approve'])
    assert len(seen) == 1 and failures == ['changed-and-replayed-rejected']


@pytest.mark.parametrize('failure_kind', ['tool-error', 'unexpected', 'mechanism'])
@async_test
async def test_errors_keep_existing_conversion_and_propagation(failure_kind, monkeypatch, caplog):
    from langchain_core.tools import ToolException
    from naiyue_test_agent.tools.permissions import ToolPermission, PermissionMechanismError
    failure = {'tool-error': ToolException('Fixture tool error'), 'unexpected': RuntimeError('PRIVATE_DETAIL'),
               'mechanism': PermissionMechanismError('mechanism failure')}[failure_kind]
    seen = []
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'schema_probe', 'id': 'failure', 'args': VALID}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [definition(seen=seen, permission=ToolPermission(default='allow'), failure=failure)])
    sid = await app.acreate_session('A')
    if failure_kind == 'mechanism':
        with pytest.raises(PermissionMechanismError) as raised:
            await app.arun(sid, [('user', 'fail')])
        assert raised.value is failure
    else:
        result = await app.arun(sid, [('user', 'fail')])
        message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert message.status == 'error' and message.tool_call_id == 'failure'
        assert 'PRIVATE_DETAIL' not in str(result) + caplog.text
        if failure_kind == 'tool-error':
            assert message.content == 'Fixture tool error'
        else:
            assert 'RuntimeError' in message.content
    assert len(seen) == 1


@pytest.mark.parametrize('invalid_in_batch', [False, True])
@async_test
async def test_json_checkpoint_reopen_obeys_existing_approval_recovery_contract(monkeypatch, tmp_path, invalid_in_batch):
    from naiyue_test_agent import application
    from naiyue_test_agent.application.async_persistent import open_async_persistent_application
    from naiyue_test_agent.application.errors import SessionPersistenceError
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.settings import AppSettings
    seen = []
    spec = definition(seen=seen, result_format='content_and_artifact')
    calls = ([{'name': 'schema_probe', 'id': 'invalid', 'args': {}}] if invalid_in_batch else [])
    calls += [{'name': 'schema_probe', 'id': 'approved', 'args': VALID}]
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=calls), AIMessage(content='done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    def persistent():
        return open_async_persistent_application('synthetic', database_path=tmp_path / 'sessions.sqlite',
            tools=[spec], settings=AppSettings(), system_prompt='', response_format=None)
    async with persistent() as app:
        sid = await app.acreate_session('A')
        paused = await app.arun(sid, [('user', 'pending batch')])
        assert paused['approval_requests'] and not seen
        saved_calls = paused['tool_execution']['calls']
    async with persistent() as app:
        history = await app.aget_session_history(sid)
        assert history['recovery']['kind'] == 'pending_approval'
        thread = app._storage.get(sid)['thread_id']
        snapshot = await app._agent.aget_state({'configurable': {'thread_id': thread}})
        assert snapshot.values['tool_execution']['calls'] == saved_calls
        if invalid_in_batch:
            # Existing recovery only restores batches whose EVERY input remains
            # valid. Preserve this boundary; do not widen recovery in MCP-01.
            assert 'validation_feedback' in saved_calls[0]
            with pytest.raises(SessionPersistenceError, match='approval_restore_unavailable'):
                await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
            assert not seen
        else:
            await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
            result = await app.aresume_tool_approval(sid, ['approve'])
            outputs = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [(m.tool_call_id, m.status) for m in outputs] == [('approved', 'success')]
            assert outputs[0].artifact == {'args': VALID} and len(seen) == 1


@pytest.mark.parametrize('kind', ['pointer', 'anchor', 'literal-data'])
@async_test
async def test_actual_model_binding_preserves_json_schema_without_framework_rewriting(kind, monkeypatch):
    from copy import deepcopy
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from langchain_anthropic.chat_models import convert_to_anthropic_tool
    from naiyue_test_agent.tools.permissions import ToolPermission
    from test_json_schema_tools import SCHEMA

    class ConvertingModel(DynamicModel):
        def bind_tools(self, tools, **kwargs):
            # Exercise actual public provider conversion, not only FakeModel's
            # acceptance of arbitrary BaseTool instances.
            for tool in tools:
                convert_to_openai_tool(tool)
                convert_to_anthropic_tool(tool)
            return super().bind_tools(tools, **kwargs)

    if kind == 'pointer':
        schema, args = deepcopy(SCHEMA), VALID
    elif kind == 'anchor':
        schema = {'type': 'object', '$defs': {'value': {'$anchor': 'value', 'type': 'integer'}},
                  'properties': {'value': {'$ref': '#value'}}, 'required': ['value']}
        args = {'value': 2}
    else:
        schema = {'type': 'object', 'properties': {'data': {
            'default': {'$ref': 'https://business-data.invalid/value'},
            'enum': [{'$schema': 'business-data'}]}}}
        args = {'data': {'$schema': 'business-data'}}
    original = deepcopy(schema)
    seen = []
    model = ConvertingModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'schema_probe', 'id': 'bound', 'args': args}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [definition(schema, seen=seen, permission=ToolPermission(default='allow'))])
    sid = await app.acreate_session('A')
    result = await app.arun(sid, [('user', 'bind generic schema')])
    for definitions in model.bound:
        assert definitions == [{'type': 'function', 'function': {'name': 'schema_probe',
            'description': 'Deterministic JSON tool', 'parameters': original}}]
        assert convert_to_openai_tool(definitions[0])['function']['parameters'] == original
        assert convert_to_anthropic_tool(definitions[0])['input_schema'] == original
    assert schema == original and len(seen) == 1 and seen[0]['args'] == args
    assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).status == 'success'


@async_test
async def test_schema_mutation_while_approval_is_pending_cannot_execute_old_permit(monkeypatch):
    seen = []
    spec = definition(seen=seen)
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'schema_probe', 'id': 'pending', 'args': VALID}]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [spec])
    sid = await app.acreate_session('A')
    paused = await app.arun(sid, [('user', 'pending')])
    assert paused['approval_requests'] and not seen
    spec.tool.args_schema['required'] = []
    with pytest.raises(ValueError, match='schema.*changed|changed.*schema'):
        await app.aresume_tool_approval(sid, ['approve'])
    assert not seen
