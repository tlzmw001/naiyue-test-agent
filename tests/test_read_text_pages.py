"""Read keeps useful text at line/byte boundaries without an unbounded read."""

import io
import logging
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.tools import ReadSettings, create_session_read_tool
from naiyue_test_agent.tools.read import _read
from test_read_tool import call_read, EXPECTED_ENCODING, EXPECTED_NON_TEXT
from test_permission_pipeline import build, call
from test_dynamic_tools import DynamicModel


def test_complete_lines_before_giant_line_are_returned_then_prefix_can_be_read(tmp_path):
    path = tmp_path / 'large.txt'
    path.write_bytes(b'first\nsecond\n' + b'x' * 100000 + b'\nlast\n')
    spec = create_session_read_tool()
    first = call_read(spec, {'path': path.name}, tmp_path)
    assert first.status == 'success'
    assert first.content == ('first\nsecond\n[read: lines 1-2; next_offset=3]\n'
        '[read: stopped by line 3 exceeding 65536 bytes; continue with the same path, offset=3, limit=2000]')
    second = call_read(spec, {'path': path.name, 'offset': 3}, tmp_path)
    assert second.status == 'success' and second.content.startswith('x' * 65536 + '\n[read: line 3 partial;')
    assert 'returned_bytes=65536; byte_limit=65536; next_offset=unavailable' in second.content
    assert 'line remainder omitted' in second.content and 'cannot resume within this line' in second.content
    last = call_read(spec, {'path': path.name, 'offset': 4}, tmp_path)
    assert last.content == 'last\n[read: lines 4-4; next_offset=none]'


@pytest.mark.parametrize('text,budget,expected', [('中中中', 4, '中'), ('a😀b', 4, 'a'),
    ('ééé', 3, 'é'), ('中中', 1, ''), ('x' * 9, 8, 'x' * 8)])
def test_first_line_prefix_never_splits_a_utf8_character(tmp_path, text, budget, expected):
    (tmp_path / 'one.txt').write_bytes(text.encode())
    spec = create_session_read_tool(settings=ReadSettings(max_output_bytes=budget))
    result = call_read(spec, {'path': 'one.txt'}, tmp_path)
    assert result.status == 'success'
    assert result.content == (expected + ('\n' if expected else '') +
        f'[read: line 1 partial; returned_bytes={len(expected.encode())}; byte_limit={budget}; next_offset=unavailable; '
        'line remainder omitted. offset/limit cannot resume within this line; use a shell to inspect the remainder.]')


@pytest.mark.parametrize('content,expected', [(b'a\xff' + b'x' * 20, EXPECTED_ENCODING),
    (b'a\x00' + b'x' * 20, EXPECTED_NON_TEXT), (b'a\x1b' + b'x' * 20, EXPECTED_NON_TEXT),
    (b'ab\xe4', EXPECTED_ENCODING)])
def test_partial_mode_does_not_hide_invalid_bytes_or_incomplete_file_end(tmp_path, content, expected):
    (tmp_path / 'bad.txt').write_bytes(content)
    spec = create_session_read_tool(settings=ReadSettings(max_output_bytes=4))
    result = call_read(spec, {'path': 'bad.txt'}, tmp_path)
    assert result.status == 'error' and result.content == expected


@pytest.mark.parametrize('content,budget,limit,reason,next_offset,body', [
    (b'a\nb\nc\n', 64, 2, '2 line page limit', 3, 'a\nb\n'),
    (b'aa\nbb\ncc\n', 5, 3, '5 byte page limit', 2, 'aa\n'),
    ('中\r\n文\r\n'.encode(), 6, 3, '6 byte page limit', 2, '中\r\n'),
])
def test_page_notes_explain_the_limit_and_give_correct_continuation(tmp_path, content, budget, limit, reason, next_offset, body):
    (tmp_path / 'pages.txt').write_bytes(content)
    spec = create_session_read_tool(settings=ReadSettings(max_output_bytes=budget))
    result = call_read(spec, {'path': 'pages.txt', 'offset': 1, 'limit': limit}, tmp_path)
    assert result.status == 'success'
    assert result.content == (body + f'[read: lines 1-{next_offset-1}; next_offset={next_offset}]\n'
        f'[read: stopped by {reason}; continue with the same path, offset={next_offset}, limit={limit}]')


def test_exact_byte_and_line_boundary_at_eof_is_complete(tmp_path):
    (tmp_path / 'exact.txt').write_bytes(b'abc\ndef')
    spec = create_session_read_tool(settings=ReadSettings(max_output_bytes=7))
    plain = call_read(spec, {'path': 'exact.txt'}, tmp_path)
    page = call_read(spec, {'path': 'exact.txt', 'offset': 1, 'limit': 2}, tmp_path)
    assert plain.content == 'abc\ndef'
    assert page.content == 'abc\ndef\n[read: lines 1-2; next_offset=none]'


def test_skipping_and_returning_long_lines_use_only_bounded_io():
    sizes = []
    class Bounded(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)
        def readline(self, size=-1):
            sizes.append(size)
            return super().readline(size)
    class Target:
        def open(self, mode):
            assert mode == 'rb'
            return Bounded(b'x' * 200000 + b'\nend\n')
    result = _read(Target(), 2, 1, settings=ReadSettings(max_output_bytes=64))
    assert result == 'end\n[read: lines 2-2; next_offset=none]'
    prefix = _read(Target(), None, None, settings=ReadSettings(max_output_bytes=64))
    assert prefix.startswith('x' * 64 + '\n[read: line 1 partial;')
    assert sizes and all(0 < size <= 65 for size in sizes)


@pytest.mark.parametrize('error,words', [(FileNotFoundError('PRIVATE'), ('missing', 'Check')),
    (IsADirectoryError('PRIVATE'), ('regular file', 'Choose')),
    (PermissionError('PRIVATE'), ('permission', 'Check'))])
def test_read_open_failure_has_specific_safe_feedback(tmp_path, monkeypatch, caplog, error, words):
    target = tmp_path / 'io.txt'
    target.write_text('PRIVATE_BODY')
    original = Path.open
    def fail(path, *args, **kwargs):
        if path == target:
            raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail)
    with caplog.at_level(logging.WARNING):
        result = call_read(create_session_read_tool(), {'path': target.name}, tmp_path)
    assert result.status == 'error' and all(word in result.content for word in words)
    assert type(error).__name__ in caplog.text and 'PRIVATE' not in caplog.text + result.content


def test_full_graph_returns_partial_text_as_success_and_model_continues(monkeypatch, tmp_path):
    (tmp_path / 'long.txt').write_bytes(b'x' * 70000)
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[call('long.txt', 'read-long')]), AIMessage(content='received prefix')])
    app = build(monkeypatch, model)
    result = app.run(app.create_workspace_session('A', tmp_path), [('user', 'read the long file')])
    feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert len(feedback) == 1 and feedback[0].tool_call_id == 'read-long' and feedback[0].status == 'success'
    assert feedback[0].content.startswith('x' * 65536) and 'line remainder omitted' in feedback[0].content
    assert result['messages'][-1].content == 'received prefix'
