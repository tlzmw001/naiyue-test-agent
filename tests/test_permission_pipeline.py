"""Deterministic permission decisions precede the official batch interrupt."""

from native_application_helpers import async_test, build_application

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import FilePermission
from test_dynamic_tools import DynamicModel


def call(path, identity='c1', **args):
    return {'name': 'read', 'args': {'path': str(path), **args}, 'id': identity}


def settings(**permissions):
    return AppSettings.model_validate({'permissions': permissions})


async def build(monkeypatch, model, specs=None, **permissions):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    return await build_application('synthetic', tools=specs or [create_session_read_tool()],
        settings=settings(**permissions), system_prompt='', response_format=None)


@async_test
async def test_exact_deny_overrides_directory_allow_without_reading(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'work'
        root.mkdir()
        secret, plain = tmp_path / 'secret.txt', tmp_path / 'plain.txt'
        secret.write_text('PRIVATE_DENIED')
        plain.write_text('approved')
        model = DynamicModel(responses=[AIMessage(content='', tool_calls=[call(secret, 'deny'), call(plain, 'allow')]), AIMessage(content='done')])
        options = dict(deny=[{'tool': 'read', 'path': str(secret)}], allow=[{'tool': 'read', 'path': str(tmp_path) + '/**'}])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('synthetic', tools=[create_session_read_tool()],
            settings=settings(**options), system_prompt='', response_format=None)
        sid = await app.acreate_workspace_session('A', root)
        events = []
        app.subscribe_events(events.append)
        try:
            result = await app.arun(sid, [('user', 'read')])
            outputs = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [(m.tool_call_id, m.status) for m in outputs] == [('deny', 'error'), ('allow', 'success')]
            assert 'PRIVATE_DENIED' not in outputs[0].content and outputs[1].content == 'approved'
            assert not result.get('approval_requests')
            denied = next(e for e in events if e.event_type == 'tool_call_finished' and e.data.tool_call_id == 'deny')
            assert denied.data.outcome == 'not_executed'
        finally:
            await app.aclose()
    await scenario()


@async_test
async def test_approval_prepares_once_and_remember_does_not_write_settings(monkeypatch, tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    target = tmp_path / 'outside.txt'
    target.write_text('approved')
    visits = []
    class CountingPermission(FilePermission):
        def prepare(self, args, context):
            visits.append('prepare')
            return super().prepare(args, context)
        def default_decision(self, subject):
            visits.append('default')
            return super().default_decision(subject)
    spec = replace(create_session_read_tool(), permissions=CountingPermission('path', 'read'))
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[call(target)]), AIMessage(content='done'),
        AIMessage(content='', tool_calls=[call(target, 'c2')]), AIMessage(content='remembered')])
    app = await build(monkeypatch, model, [spec])
    sid = await app.acreate_workspace_session('A', root)
    paused = await app.arun(sid, [('user', 'read')])
    assert visits == ['prepare', 'default'] and len(paused['approval_requests']) == 1
    assert paused['tool_execution']['calls'][0]['decision'] == 'ask'
    assert not any(isinstance(m, ToolMessage) for m in paused['messages'])
    done = await app.aresume_tool_approval(sid, ['approve'], remember_approved=['c1'])
    assert visits == ['prepare', 'default']
    assert next(m.content for m in done['messages'] if isinstance(m, ToolMessage)) == 'approved'
    remembered = await app.arun(sid, [('user', 'again')])
    assert not remembered.get('approval_requests') and remembered['messages'][-1].content == 'remembered'
    assert visits == ['prepare', 'default', 'prepare']
    assert app._sessions.resolve_permission_context(sid)['session_allow']
    other = await app.acreate_workspace_session('A', root)
    assert app._sessions.resolve_permission_context(other)['session_allow'] == []


@pytest.mark.parametrize('mode,status,pending', [('Default', None, True), ('NoAsk', 'error', False), ('BypassApprovals', 'success', False)])
@async_test
async def test_modes_resolve_remaining_ask(monkeypatch, tmp_path, mode, status, pending):
    root = tmp_path / 'work'
    root.mkdir()
    path = tmp_path / 'outside.txt'
    path.write_text('data')
    app = await build(monkeypatch, DynamicModel(responses=[AIMessage(content='', tool_calls=[call(path)]), AIMessage(content='done')]), mode=mode)
    result = await app.arun(await app.acreate_workspace_session('A', root), [('user', 'read')])
    assert bool(result.get('approval_requests')) is pending
    if not pending:
        assert next(m.status for m in result['messages'] if isinstance(m, ToolMessage)) == status


@async_test
async def test_default_callback_failure_falls_back_to_human(monkeypatch, tmp_path, caplog):
    path = tmp_path / 'file.txt'
    path.write_text('data')
    class Broken(FilePermission):
        def default_decision(self, subject):
            raise RuntimeError('PRIVATE_DETAIL')
    spec = replace(create_session_read_tool(), permissions=Broken('path', 'read'))
    app = await build(monkeypatch, DynamicModel(responses=[AIMessage(content='', tool_calls=[call(path)])]), [spec])
    paused = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'read')])
    assert paused['approval_requests'] and 'PRIVATE_DETAIL' not in caplog.text


@async_test
async def test_mode_change_during_pause_does_not_change_saved_batch(monkeypatch, tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    path = tmp_path / 'outside.txt'
    path.write_text('data')
    app = await build(monkeypatch, DynamicModel(responses=[AIMessage(content='', tool_calls=[call(path)]), AIMessage(content='done')]))
    sid = await app.acreate_workspace_session('A', root)
    paused = await app.arun(sid, [('user', 'read')])
    await app.aset_permission_mode(sid, 'BypassApprovals')
    assert app.get_permission_mode(sid) == 'BypassApprovals'
    assert paused['tool_execution']['mode'] == 'Default'
    rejected = await app.aresume_tool_approval(sid, ['reject'])
    assert next(m.status for m in rejected['messages'] if isinstance(m, ToolMessage)) == 'error'


@async_test
async def test_target_change_after_approval_has_no_read(monkeypatch, tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    first, second = tmp_path / 'one.txt', tmp_path / 'two.txt'
    first.write_text('one')
    second.write_text('PRIVATE_TWO')
    link = root / 'link.txt'
    link.symlink_to(first)
    app = await build(monkeypatch, DynamicModel(responses=[AIMessage(content='', tool_calls=[call(link)]), AIMessage(content='done')]))
    sid = await app.acreate_workspace_session('A', root)
    await app.arun(sid, [('user', 'read')])
    link.unlink()
    link.symlink_to(second)
    result = await app.aresume_tool_approval(sid, ['approve'])
    output = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert output.status == 'error' and 'changed' in output.content and 'PRIVATE_TWO' not in output.content
