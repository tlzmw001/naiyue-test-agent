"""Saved file feedback uses the real before/after and independent read authorization."""

from native_application_helpers import async_test
from langchain_core.messages import AIMessage
from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool
from test_write_tool import make_app, call, results


@async_test
async def test_write_captures_real_diff_without_adding_it_to_model_text(monkeypatch, tmp_path):
    (tmp_path / 'a').write_text('before\n')
    app, model = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'a', 'content': 'after\n'}, 'w')]), AIMessage(content='done')])
    try:
        sid = await app.acreate_workspace_session('A', tmp_path)
        result = results(await app.arun(sid, [('user', 'write')]))[0]
        assert result.artifact['type'] == 'file_change'
        assert '-before\n' in result.artifact['diff'] and '+after\n' in result.artifact['patch']
        assert result.artifact['operation'] == 'write' and result.artifact['firstChangedLine'] == 1
        assert 'before' not in result.content
        assert (tmp_path / 'a').read_text() == 'after\n'
    finally:
        await app.aclose()


@async_test
async def test_write_allow_does_not_override_read_deny(monkeypatch, tmp_path):
    (tmp_path / 'a').write_text('private before')
    app, model = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'a', 'content': 'after'}, 'w')]), AIMessage(content='done')],
        tools=[create_session_read_tool(), create_session_write_tool()], permissions={'deny': [{'tool': 'read', 'path': '${workspace}/a'}]})
    try:
        sid = await app.acreate_workspace_session('A', tmp_path)
        result = results(await app.arun(sid, [('user', 'write')]))[0]
        assert result.status == 'success' and result.artifact['unavailable_reason'] == 'read_not_allowed'
        assert result.artifact['patch'] is None and 'private' not in str(result.artifact)
    finally:
        await app.aclose()


@async_test
async def test_write_alias_never_exposes_read_denied_old_content(monkeypatch, tmp_path):
    (tmp_path / 'real').write_text('private old body')
    (tmp_path / 'link').symlink_to(tmp_path / 'real')
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'link', 'content': 'after'}, 'w')]), AIMessage(content='done')],
        tools=[create_session_read_tool(), create_session_write_tool()],
        permissions={'deny': [{'tool': 'read', 'path': '${workspace}/link'}]})
    try:
        sid = await app.acreate_workspace_session('A', tmp_path)
        result = results(await app.arun(sid, [('user', 'write')]))[0]
        assert result.status == 'success'
        assert result.artifact['unavailable_reason'] == 'read_not_allowed'
        assert 'private old body' not in str(result.artifact)
        assert (tmp_path / 'link').is_symlink() and (tmp_path / 'real').read_text() == 'after'
    finally:
        await app.aclose()


def test_write_feedback_unchanged_file_has_no_first_changed_line(tmp_path):
    from naiyue_test_agent.tools.file_change import write_feedback
    from naiyue_test_agent.tools.settings import WriteSettings
    target = tmp_path / 'same'
    target.write_text('same\n')
    result = write_feedback(target, 'same\n', WriteSettings(), read_allowed=True, display_path='same')
    assert result['diff'] == '' and result['firstChangedLine'] is None


def test_feedback_read_access_time_does_not_count_as_content_change(monkeypatch, tmp_path):
    from pathlib import Path
    from naiyue_test_agent.tools import write as module
    from naiyue_test_agent.tools.paths import prepare_file_subject
    from naiyue_test_agent.tools.settings import WriteSettings
    target = tmp_path / 'a'
    target.write_text('before')
    subject = prepare_file_subject('a', tmp_path, 'write')
    original, ticks = Path.stat, iter(range(10000))
    class AccessStamp:
        def __init__(self, info):
            self.info, self.access = info, next(ticks)
        def __getattr__(self, name):
            return self.access if name in ('st_atime', 'st_atime_ns') else getattr(self.info, name)
        def __eq__(self, other):
            return isinstance(other, AccessStamp) and self.info == other.info and self.access == other.access
    def stat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        return AccessStamp(info) if path == target else info
    monkeypatch.setattr(Path, 'stat', stat)
    artifact = {}
    module._write(tmp_path, 'a', 'after', 1024, subject=subject, feedback_artifact=artifact,
                  feedback_settings=WriteSettings(), feedback_read_allowed=True, feedback_path='a')
    assert target.read_text() == 'after' and '-before' in artifact['diff']
