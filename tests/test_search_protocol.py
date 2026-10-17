"""Bounded framing for all three native search streams."""

import pytest

from naiyue_test_agent.tools.search_protocol import RecordBuffer, DiagnosticTail, SkippedTargets, safe_text


def test_nul_records_survive_split_utf8_newlines_and_empty_chunks():
    parser = RecordBuffer(b'\0', 32)
    assert list(parser.feed(b'\xe4\xb8')) == []
    assert list(parser.feed(b'\xad\nname\0other')) == [b'\xe4\xb8\xad\nname']
    assert list(parser.feed(b'')) == []
    assert list(parser.feed(b'\0')) == [b'other']
    parser.finish()


def test_oversized_record_is_discarded_without_buffering_or_losing_next_record():
    parser = RecordBuffer(b'\n', 8)
    assert list(parser.feed(b'x' * 100000)) == []
    assert len(parser.buffer) <= 8
    assert list(parser.feed(b'\nvalid\n')) == [None, b'valid']
    parser.finish()


@pytest.mark.parametrize('last', [b'partial', b'x' * 100])
def test_partial_eof_is_an_error(last):
    parser = RecordBuffer(b'\n', 8)
    list(parser.feed(last))
    with pytest.raises(ValueError, match='incomplete_record'):
        parser.finish()


def test_diagnostics_and_permission_report_memory_are_bounded():
    tail = DiagnosticTail(4)
    tail.feed(b'private' * 1000)
    assert bytes(tail.buffer) == b'vate'
    report = SkippedTargets(256)
    for index in range(10000):
        report.add(f'.hidden/{index}: deny')
    assert report.count == 10000 and len(report.items) < 30
    text = report.render('grep')
    assert 'not searched' in text and 'omitted' in text
    assert len(text.encode()) < 512


def test_names_and_text_do_not_inject_terminal_controls_or_surrogates():
    assert safe_text('中\n\t\x1b\udcff') == '中\\n\\t\\u001b\\udcff'


def test_native_error_words_in_filename_do_not_misclassify_io_failure():
    from naiyue_test_agent.tools.grep import _native_failure
    diagnostic = DiagnosticTail(8192)
    diagnostic.feed(b'/path/regex-glob.txt: Permission denied (os error 13)')
    error = _native_failure(diagnostic)
    assert 'file availability' in str(error) and 'regex' not in str(error) and 'glob' not in str(error)
