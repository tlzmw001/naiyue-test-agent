"""Native rg/fd behavior and search budgets through actual ToolError feedback."""

from pathlib import Path

from naiyue_test_agent.tools import GrepSettings, FindSettings
from naiyue_test_agent.tools.grep import create_session_grep_tool
from naiyue_test_agent.tools.find import create_session_find_tool
from tool_feedback_helpers import invoke_tool_feedback


def invoke(spec, root, **args):
    return invoke_tool_feedback(spec, args, call_id='search', context={'workspace_root': root})


def test_grep_searches_file_larger_than_old_limit_without_python_content_io(tmp_path, monkeypatch):
    target = tmp_path / 'large.txt'
    with target.open('w') as stream:
        for _ in range(150000):
            stream.write('a' * 64 + '\n')
        stream.write('UNIQUE_NEEDLE\n')
    original = Path.open
    def no_content(path, *args, **kwargs):
        assert path != target, 'Content belongs to rg, not a Python pre-read'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_content)
    result = invoke(create_session_grep_tool(), tmp_path, pattern='UNIQUE_NEEDLE')
    assert result.status == 'success' and 'large.txt:150001: UNIQUE_NEEDLE' in result.content


def test_grep_many_files_are_batched_without_total_candidate_limit(tmp_path, monkeypatch):
    from naiyue_test_agent.tools.processes import HostProcessRunner
    for index in range(2050):
        (tmp_path / f'{index:04d}.txt').touch()
    (tmp_path / 'unique.txt').write_text('NEEDLE\n')
    calls = []
    original = HostProcessRunner.run
    async def record(self, request, *args, **kwargs):
        calls.append(request.argv)
        return await original(self, request, *args, **kwargs)
    monkeypatch.setattr(HostProcessRunner, 'run', record)
    result = invoke(create_session_grep_tool(), tmp_path, pattern='NEEDLE')
    assert result.status == 'success', result.content
    assert 'unique.txt:1: NEEDLE' in result.content
    searches = [args for args in calls if '--json' in args]
    assert len(searches) < 100 and any(len(args[args.index('--') + 1:]) > 1 for args in searches)


def test_fd_lists_directories_and_native_smart_case(tmp_path):
    (tmp_path / 'Docs').mkdir()
    (tmp_path / 'docs.txt').touch()
    result = invoke(create_session_find_tool(), tmp_path, pattern='docs*')
    assert result.status == 'success' and result.content.splitlines() == ['Docs/', 'docs.txt']
    result = invoke(create_session_find_tool(), tmp_path, pattern='Docs*')
    assert result.content == 'Docs/'


def test_oversized_json_record_marks_incomplete_and_preserves_later_match(tmp_path):
    (tmp_path / 'long.txt').write_text('HIT' + 'x' * 4000 + '\nHIT short\n')
    spec = create_session_grep_tool(settings=GrepSettings(max_record_bytes=1024))
    result = invoke(spec, tmp_path, pattern='HIT')
    assert result.status == 'success' and 'long.txt:2: HIT short' in result.content
    assert 'incomplete' in result.content and 'record' in result.content
    (tmp_path / 'long.txt').write_text('HIT' + 'x' * 4000 + '\n')
    result = invoke(spec, tmp_path, pattern='HIT')
    assert result.status == 'error' and 'No matches' not in result.content


def test_configured_schema_default_result_and_context_limits(tmp_path):
    (tmp_path / 'doc.txt').write_text('one\nHIT\nthree\nHIT\n')
    spec = create_session_grep_tool(settings=GrepSettings(default_limit=1, max_limit=2,
        default_context_lines=1, max_context_lines=2, max_pattern_chars=8))
    props = spec.tool.tool_call_schema.model_json_schema()['properties']
    assert props['limit']['maximum'] == 2 and props['context']['maximum'] == 2
    assert props['pattern']['maxLength'] == 8 and 'runtime' not in props
    result = invoke(spec, tmp_path, pattern='HIT')
    assert 'doc.txt-1- one' in result.content and 'doc.txt:2: HIT' in result.content
    assert 'truncated' in result.content and 'doc.txt:4:' not in result.content
    assert invoke(spec, tmp_path, pattern='HIT', context=3).status == 'error'
    finder = create_session_find_tool(settings=FindSettings(default_limit=1, max_limit=2))
    assert finder.tool.tool_call_schema.model_json_schema()['properties']['limit']['maximum'] == 2


def test_fd_nul_names_keep_newlines_separate(tmp_path):
    (tmp_path / 'line\nbreak.txt').touch()
    result = invoke(create_session_find_tool(), tmp_path, pattern='*.txt')
    assert result.status == 'success'
    assert result.content.splitlines() == ['line\\nbreak.txt']


def test_non_utf8_native_name_bytes_are_framed_and_rendered_without_loss(tmp_path, monkeypatch):
    from naiyue_test_agent.tools import find as module
    from naiyue_test_agent.tools.processes import ProcessOutcome
    # APFS refuses invalid UTF-8 filenames. Inject the protocol bytes and metadata
    # for this case; actual newline names above still use native fd and the filesystem.
    monkeypatch.setattr(module, 'prepare_file_subject', lambda path, *args: {'requested': path})
    monkeypatch.setattr(module, 'verify_file_target', lambda subject: tmp_path / 'file')
    monkeypatch.setattr(module, 'candidate_access', lambda *args, **kwargs: (True, None))
    async def stream(self, request, consume, control=None):
        import os
        record = os.fsencode(tmp_path) + b'/bad\xff.txt\0'
        await consume('stdout', record[:-2])
        await consume('stdout', record[-2:])
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    monkeypatch.setattr(module.HostProcessRunner, 'run', stream)
    result = invoke(create_session_find_tool(), tmp_path, pattern='*.txt')
    assert result.status == 'success' and result.content == 'bad\\udcff.txt'
