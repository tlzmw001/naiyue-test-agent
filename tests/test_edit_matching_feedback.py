"""Edit behavior through the real permission, dispatch and ToolError boundary."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test

import json
import re
from pathlib import Path

import pytest

from naiyue_test_agent.tools import EditSettings, create_session_edit_tool
from tool_feedback_helpers import invoke_tool_feedback


async def run_edit(tmp_path, original, edits, settings=None):
    target = tmp_path / 'text.txt'
    target.write_bytes(original)
    result = (await invoke_tool_feedback(create_session_edit_tool(settings=settings),
        {'path': target.name, 'edits': [{'oldText': old, 'newText': new} for old, new in edits]},
        call_id='edit-detail', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    return result, target.read_bytes()


@async_test
@pytest.mark.parametrize(('original', 'old', 'new', 'expected'), [
    (b'\xef\xbb\xbfalpha\r\nbeta\r\n', 'alpha\nbeta', 'A\nB', b'\xef\xbb\xbfA\r\nB\r\n'),
    ('print(“旧值”)\r\n'.encode(), 'print("旧值")', 'print("新值")', 'print("新值")\r\n'.encode()),
    ('cafe\u0301 = １\n'.encode(), 'café = 1', 'ok', b'ok\n'),
    ('a\u00a0—\u2009b  \r\nc\n'.encode(), 'a - b\nc', 'X\nY', b'X\r\nY\n'),
    (b'first\r\nsecond\nthird\n', 'second\nthird', 'S\nT', b'first\r\nS\nT\n'),
])
async def test_finite_normalization_and_local_newline_preserve_bytes(tmp_path, original, old, new, expected):
    result, actual = (await run_edit(tmp_path, original, [(old, new)]))
    assert result.status == 'success', result.content
    assert actual == expected
    assert result.tool_call_id == 'edit-detail'
    assert result.artifact['firstChangedLine'] == (2 if old.startswith('second') else 1)
    assert result.artifact['patch_omitted'] is False
    assert json.loads(json.dumps(result.artifact)) == result.artifact


@async_test
async def test_success_contains_bounded_diff_and_json_artifact(tmp_path):
    result, actual = (await run_edit(tmp_path, b'a\nb\nc\nd\ne\nf\ng\nh\n', [('d', 'D'), ('h', 'H')]))
    assert result.status == 'success'
    assert actual == b'a\nb\nc\nD\ne\nf\ng\nH\n'
    assert 'firstChangedLine=4' in result.content and '+D' in result.content
    assert result.artifact['firstChangedLine'] == 4
    assert '-h' in result.artifact['patch'] and '+H' in result.artifact['patch']
    assert result.artifact['diff_truncated'] is False


@async_test
@pytest.mark.parametrize(('original', 'edits', 'reason'), [
    ('“a”\n"a"\nother\n', [('"a"', 'A'), ('ｏｔｈｅｒ', 'B')], 'unique'),
    ('“a” same same\n', [('"a"', 'A'), ('same', 'B')], 'unique'),
    ('“abc”\n', [('"abc"', 'A'), ('abc', 'B')], 'overlap'),
    ('ﬃ\n', [('fi', 'X')], 'not found'),
])
async def test_ambiguity_overlap_and_partial_unicode_expansion_are_atomic(tmp_path, original, edits, reason):
    encoded = original.encode()
    result, actual = (await run_edit(tmp_path, encoded, edits))
    assert result.status == 'error'
    assert reason in result.content
    assert actual == encoded


@async_test
async def test_fuzzy_crosses_io_chunks_and_hangul_combining_boundary(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import edit as module
    monkeypatch.setattr(module, '_CHUNK_BYTES', 5)
    original = ('prefix\n' + '\u1100\u1161\u11a8 cafe\u0301 “x”\n').encode()
    result, actual = (await run_edit(tmp_path, original, [('각 café "x"', 'done')]))
    assert result.status == 'success', result.content
    assert actual == b'prefix\ndone\n'


def apply_patch(original, patch):
    """Small strict unified-patch consumer: verify old lines and hunk coordinates."""
    def lf_lines(data):
        parts = data.split(b'\n')
        return [part + b'\n' for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    source = lf_lines(original)
    lines = lf_lines(patch.encode())
    output, position, index = [], 0, 2
    while index < len(lines):
        header = re.fullmatch(rb'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n', lines[index])
        assert header, lines[index]
        old_start, old_count, new_start, new_count = header.groups()
        count = int(old_count) if old_count else 1
        start = int(old_start) - (1 if count else 0)
        output.extend(source[position:start])
        position = start
        assert len(output) == int(new_start) - (1 if int(new_count or b'1') else 0)
        index += 1
        old_seen = new_seen = 0
        while index < len(lines) and not lines[index].startswith(b'@@'):
            line = lines[index]
            prefix, body = line[:1], line[1:]
            assert prefix in (b' ', b'-', b'+')
            if index + 1 < len(lines) and lines[index + 1].startswith(b'\\ No newline'):
                body = body[:-1]
                index += 1
            if prefix != b'+':
                assert source[position] == body
                position += 1
                old_seen += 1
            if prefix != b'-':
                output.append(body)
                new_seen += 1
            index += 1
        assert old_seen == count and new_seen == int(new_count or b'1')
    output.extend(source[position:])
    return b''.join(output)


@async_test
@pytest.mark.parametrize(('original', 'edits'), [
    (b'a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl\nm\nn\no\np\n', [('b', 'B\nBB'), ('o', 'O')]),
    (b'one\ntwo', [('two', 'TWO\nlast\n')]),
    (b'one\ntwo\n', [('one\ntwo\n', '')]),
    (b'one\ntwo', [('one\ntwo', '')]),
    (b'\xef\xbb\xbfalpha\r\nbeta\r\n', [('alpha\nbeta', 'A\nB')]),
    ('a\u2028b\nc\n'.encode(), [('b', 'B')]),
])
async def test_complete_patch_reconstructs_exact_result(tmp_path, original, edits):
    result, actual = (await run_edit(tmp_path, original, edits))
    assert result.status == 'success', result.content
    assert result.artifact['patch_omitted'] is False
    assert apply_patch(original, result.artifact['patch']) == actual


@async_test
async def test_changed_line_is_first_difference_not_oldtext_start(tmp_path):
    result, actual = (await run_edit(tmp_path, b'same\nold\ntail\n', [('same\nold', 'same\nNEW')]))
    assert actual == b'same\nNEW\ntail\n'
    assert result.artifact['firstChangedLine'] == 2


@async_test
@pytest.mark.parametrize('setting', ['max_normalization_buffer_bytes', 'max_affected_line_bytes'])
async def test_exact_remains_available_but_mixed_fuzzy_fails_atomically_at_buffer_limit(tmp_path, setting):
    settings = EditSettings(**{setting: 8})
    original = ('x' * 100 + ' old\n“a”\n').encode()
    result, actual = (await run_edit(tmp_path, original, [('old', 'NEW')], settings))
    assert result.status == 'success' and actual == original.replace(b'old', b'NEW')
    if setting == 'max_affected_line_bytes':
        assert result.artifact['patch_omitted'] is True
        assert result.artifact['diff_truncated'] is True
    failed, unchanged = (await run_edit(tmp_path, original, [('old', 'NEW'), ('"a"', 'A')], settings))
    assert failed.status == 'error' and 'buffer limit' in failed.content
    assert unchanged == original


@async_test
async def test_feedback_budgets_are_independent_utf8_limits_and_patch_never_partial(tmp_path):
    settings = EditSettings(max_diff_bytes=13, max_preview_bytes=11, max_patch_bytes=20)
    result, actual = (await run_edit(tmp_path, '旧值\n'.encode(), [('旧值', '新值新值')], settings))
    assert result.status == 'success' and actual == '新值新值\n'.encode()
    detail = result.artifact
    assert len(detail['diff'].encode()) <= 13 and detail['diff_truncated'] is True
    assert len(detail['preview'].encode()) <= 11 and detail['preview_truncated'] is True
    assert detail['patch'] is None and detail['patch_omitted'] is True
    assert 'patch omitted' in result.content and 'truncated' in result.content


@async_test
@pytest.mark.parametrize('fuzzy', [False, True])
async def test_feedback_does_not_add_a_full_file_scan(tmp_path, monkeypatch, fuzzy):
    target = tmp_path / 'text.txt'
    reads, real_open = [], Path.open
    def count_open(path, mode='r', *args, **kwargs):
        if path == target and mode == 'rb':
            reads.append(path)
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', count_open)
    original = 'zero\n“a”\nend\n'.encode() if fuzzy else b'zero\na\nend\n'
    result, _ = (await run_edit(tmp_path, original, [('"a"' if fuzzy else 'a', 'A')]))
    assert result.status == 'success'
    assert len(reads) - 1 == (3 if fuzzy else 2)  # Exclude test's final read_bytes.


@async_test
@pytest.mark.parametrize(('source', 'old'), [('ﬃ', 'ffi'), ('\u09c7\u09be', '\u09cb'), ('e\u0301\u0323', 'ẹ\u0301')])
async def test_indivisible_expansion_and_combining_sequences(tmp_path, source, old):
    result, actual = (await run_edit(tmp_path, (source + '\n').encode(), [(old, 'X')]))
    assert result.status == 'success', result.content
    assert actual == b'X\n'


@async_test
async def test_unicode_expansion_crossing_normalization_tail_is_not_partial_match(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import edit as module
    monkeypatch.setattr(module, '_CHUNK_BYTES', 1)
    result, actual = (await run_edit(tmp_path, 'ﬃ\nnext\n'.encode(), [('fi\nnext', 'BAD')]))
    assert result.status == 'error' and actual == 'ﬃ\nnext\n'.encode()


@async_test
async def test_patch_headers_support_spaces_without_quote_parser_dependency(tmp_path):
    target = tmp_path / 'text with space.txt'
    target.write_text('old\n')
    result = (await invoke_tool_feedback(create_session_edit_tool(), {'path': target.name,
        'edits': [{'oldText': 'old', 'newText': 'NEW'}]}, call_id='space', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    assert result.status == 'success'
    assert result.artifact['patch'].startswith('--- a/text with space.txt\t\n+++ b/text with space.txt\t\n')


@async_test
@pytest.mark.parametrize('name', ['tab\tname.txt', 'line\nname.txt'])
async def test_unrepresentable_patch_filename_does_not_inject_headers(tmp_path, name):
    target = tmp_path / name
    target.write_text('old\n')
    result = (await invoke_tool_feedback(create_session_edit_tool(), {'path': name,
        'edits': [{'oldText': 'old', 'newText': 'NEW'}]}, call_id='path', context=typed_context(identity=RunIdentity(execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None, session_id=None))))
    assert result.status == 'success' and target.read_text() == 'NEW\n'
    assert result.artifact['patch'] is None and result.artifact['patch_omitted'] is True
    assert result.artifact['diff_truncated'] is False
    assert result.artifact['preview_truncated'] is False
    assert 'lines/paths' in result.content


def test_edit_feedback_limits_load_from_commented_sample_and_explicit_settings(tmp_path):
    from naiyue_test_agent.settings import load_settings
    sample = Path(__file__).resolve().parents[1] / 'docs/examples/settings.example.toml'
    assert load_settings(sample).tools.edit == EditSettings()
    names = ('max_normalization_buffer_bytes', 'max_affected_line_bytes',
             'max_diff_bytes', 'max_preview_bytes', 'max_patch_bytes')
    configured = tmp_path / 'settings.toml'
    configured.write_text('[tools.edit]\n' + '\n'.join(f'{name}={101 + i}' for i, name in enumerate(names)))
    result = load_settings(configured).tools.edit
    assert [getattr(result, name) for name in names] == list(range(101, 106))


@async_test
async def test_exact_replacement_newlines_use_file_fallback_and_noop_is_not_committed(tmp_path):
    result, actual = (await run_edit(tmp_path, b'first\r\nold\r\n', [('old', 'NEW\nMORE')]))
    assert result.status == 'success' and actual == b'first\r\nNEW\r\nMORE\r\n'
    failed, unchanged = (await run_edit(tmp_path, b'first\r\nold\r\n', [('old\r\n', 'old\n')]))
    assert failed.status == 'error' and 'no change' in failed.content
    assert unchanged == b'first\r\nold\r\n'
