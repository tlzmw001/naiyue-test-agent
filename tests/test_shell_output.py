"""Incremental merged tail and bounded raw/text archives."""

import pytest


def accumulator(tmp_path, **limits):
    from naiyue_test_agent.tools.output import ShellOutputArchive
    from naiyue_test_agent.tools.output_text import OutputAccumulator
    from naiyue_test_agent.tools.settings import BashSettings
    settings = BashSettings(**limits)
    return OutputAccumulator(settings), ShellOutputArchive(tmp_path, settings)


def capture(acc, archive, chunks):
    for chunk in chunks:
        acc.feed(chunk)
        archive.record(chunk, activate=acc.truncated or acc.transformed)
    acc.finish()
    archive.activate(acc.truncated or acc.transformed)
    return archive.finish(transformed=acc.transformed)


def test_tail_prefers_complete_lines_and_reports_range(tmp_path):
    acc, archive = accumulator(tmp_path, max_output_lines=2, max_output_bytes=30)
    result = capture(acc, archive, [b'first\nsecond\nthird\n'])
    assert acc.text == 'second\nthird\n'
    assert acc.observed_lines == 3 and acc.start_line == 2
    assert acc.truncated and not acc.partial_line and acc.reasons == {'lines'}
    assert result.complete and set(result.paths) == {'raw'}
    assert (tmp_path / result.paths['raw']).read_bytes() == b'first\nsecond\nthird\n'


def test_single_huge_line_keeps_safe_utf8_tail(tmp_path):
    acc, archive = accumulator(tmp_path, max_output_bytes=8)
    capture(acc, archive, ['前面'.encode(), ('中' * 10000 + '尾').encode()])
    assert acc.text == '中尾' and len(acc.text.encode()) <= 8
    assert acc.partial_line and acc.start_line == 1 and acc.observed_lines == 1
    assert acc.reasons == {'bytes'} and not acc.lossy
    assert acc.buffer_bytes <= 8


def test_chunked_utf8_ansi_crlf_controls_and_lossy_raw_view(tmp_path):
    acc, archive = accumulator(tmp_path)
    raw = b'prefix\r\n\x1b[31m' + '中'.encode() + b'\x1b[0m\rnext\x00\xff'
    result = capture(acc, archive, [raw[i:i+1] for i in range(len(raw))])
    assert acc.text == 'prefix\n中\nnext\\x00\ufffd'
    assert acc.lossy and acc.transformed and result.complete
    assert set(result.paths) == {'raw', 'text'}
    assert (tmp_path / result.paths['raw']).read_bytes() == raw
    assert (tmp_path / result.paths['text']).read_text() == acc.text
    assert result.saved_bytes == len(raw) + len(acc.text.encode())


def test_utf16_configured_decoder_is_incremental(tmp_path):
    acc, archive = accumulator(tmp_path, output_encoding='utf-16-le')
    data = '中文\n'.encode('utf-16-le')
    result = capture(acc, archive, [data[:3], data[3:]])
    assert acc.text == '中文\n' and not acc.lossy
    assert result.paths['text'] and result.complete


def test_short_plain_output_has_no_artifact(tmp_path):
    acc, archive = accumulator(tmp_path)
    result = capture(acc, archive, [b'short\n'])
    assert acc.text == 'short\n' and not acc.truncated
    assert result.paths == {} and result.reason is None
    assert not (tmp_path / 'naiyue-agent-output').exists()


def test_late_transformation_replays_prefix_into_text_view(tmp_path):
    acc, archive = accumulator(tmp_path, max_output_bytes=8)
    raw = b'0123456789\n\x1b[32mEND\x1b[0m\n'
    result = capture(acc, archive, [raw[:12], raw[12:]])
    assert (tmp_path / result.paths['raw']).read_bytes() == raw
    assert (tmp_path / result.paths['text']).read_text() == '0123456789\nEND\n'
    assert result.complete


def test_raw_and_view_count_against_same_budget(tmp_path):
    acc, archive = accumulator(tmp_path, max_archive_bytes=15)
    result = capture(acc, archive, [b'\x1b[31m123456\x1b[m'])
    assert not result.complete and result.reason == 'command_limit'
    assert result.saved_bytes == 15
    assert sum((tmp_path / p).stat().st_size for p in result.paths.values()) == 15
    assert acc.text == '123456'


def test_calls_have_independent_budgets_and_legacy_files_still_clearable(tmp_path):
    from naiyue_test_agent.tools.output import ShellOutputArchive, preview_shell_outputs, clear_shell_outputs
    from naiyue_test_agent.tools.output_text import OutputAccumulator
    from naiyue_test_agent.tools.settings import BashSettings
    settings = BashSettings(max_output_bytes=2, max_archive_bytes=6)
    first = capture(OutputAccumulator(settings), ShellOutputArchive(tmp_path, settings), [b'first'])
    second = capture(OutputAccumulator(settings), ShellOutputArchive(tmp_path, settings), [b'second'])
    assert first.complete and second.complete and second.reason is None
    directory = tmp_path / 'naiyue-agent-output'
    legacy = directory / ('a' * 32 + '-stdout.log')
    legacy.write_bytes(b'old')
    plan = preview_shell_outputs(tmp_path)
    assert plan.file_count == 3 and plan.total_bytes == 14
    assert clear_shell_outputs(plan) == (3, 14)
    assert not legacy.exists()


def test_archive_writes_do_not_scan_existing_history(tmp_path, monkeypatch):
    import naiyue_test_agent.tools.output as module
    acc, archive = accumulator(tmp_path, max_output_bytes=2)
    def unexpected_scan(*args):
        pytest.fail('Writing this call must not scan all historical archives')
    monkeypatch.setattr(module, '_snapshot', unexpected_scan)
    result = capture(acc, archive, [b'first', b'second'])
    assert result.complete and result.saved_bytes == 11


def test_archive_disk_error_does_not_stop_tail(tmp_path, monkeypatch, caplog):
    import naiyue_test_agent.tools.output as module
    acc, archive = accumulator(tmp_path, max_output_bytes=4)
    def failed(*args, **kwargs):
        raise OSError('PRIVATE')
    monkeypatch.setattr(module, '_write_owned_file', failed)
    result = capture(acc, archive, [b'earlier', b'LAST'])
    assert acc.text == 'LAST' and not result.complete
    assert result.reason == 'directory_unavailable' and 'PRIVATE' not in caplog.text
    assert 'shell_archive_failed' in caplog.text


def test_unicode_line_separators_do_not_change_lf_line_ranges(tmp_path):
    acc, archive = accumulator(tmp_path, max_output_lines=1)
    capture(acc, archive, ['one\u2028two\u0085three\n'.encode()])
    assert acc.text == 'one\u2028two\\x85three\n'
    assert acc.observed_lines == acc.start_line == 1 and not acc.truncated


def test_literal_replacement_character_is_not_lossy(tmp_path):
    acc, archive = accumulator(tmp_path)
    result = capture(acc, archive, ['literal \ufffd'.encode()])
    assert not acc.lossy and result.paths == {}


def test_quota_cannot_split_utf8_in_readable_text_view(tmp_path):
    acc, archive = accumulator(tmp_path, output_encoding='utf-16-le', max_archive_bytes=8)
    result = capture(acc, archive, ['中中'.encode('utf-16-le')])
    assert result.reason == 'command_limit' and not result.complete
    assert (tmp_path / result.paths['text']).read_text(encoding='utf-8') == '中'
    assert result.saved_bytes == 7  # Raw 4 + complete UTF-8 character 3.
