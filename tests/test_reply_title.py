"""Automatic names are optional saved metadata, never extra model requests."""

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_persistent_sessions import build
from test_application_run_events import ToolModel
from naiyue_test_agent.settings import AppSettings, load_settings


@async_test
async def test_first_task_title_is_saved_and_manual_name_wins(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    model = ToolModel(responses=[AIMessage(content='one'), AIMessage(content='two')])
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        assert app._storage.get(sid)['title'] is None
        await app.arun(sid, [('user', '  检查\n登录\t接口\x00  ')])
        assert app._storage.get(sid)['title'] == '检查 登录 接口'
        await app.aupdate_session_presentation(sid, title='我的名称')
        await app.arun(sid, [('user', 'second')])
        assert app._storage.get(sid)['title'] == '我的名称'
    async with build(monkeypatch, path) as app:
        assert app._storage.get(sid)['title'] == '我的名称'


@async_test
async def test_title_failure_does_not_change_run_or_leak_body(monkeypatch, tmp_path, caplog):
    async with build(monkeypatch, tmp_path / 'state.sqlite') as app:
        sid = await app.acreate_session('A')
        def fail(*args):
            raise OSError('PRIVATE_TITLE')
        monkeypatch.setattr(app._storage, 'ensure_title', fail)
        result = await app.arun(sid, [('user', 'task')])
        assert result['messages'][-1].content == 'answer'
        assert app._storage.get(sid)['state'] == 'ready'
        assert 'session_title_save_failed' in caplog.text
        assert 'PRIVATE_TITLE' not in caplog.text


def test_title_budget_config(tmp_path):
    assert AppSettings().session_title_max_chars == 40
    path = tmp_path / 'settings.toml'
    path.write_text('session_title_max_chars = 12\n')
    assert load_settings(path).session_title_max_chars == 12
    path.write_text('session_title_max_chars = 0\n')
    assert load_settings(path).session_title_max_chars == 40
    with pytest.raises(ValueError):
        AppSettings(session_title_max_chars=121)
