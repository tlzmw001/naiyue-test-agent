import os
from uuid import uuid4

from naiyue_test_agent.cache_cleanup_settings import CacheCleanupSettings
from naiyue_test_agent.application.projects.cache_cleanup_operation import CleanupRun
from naiyue_test_agent.tools.output import _directory
from naiyue_test_agent.tools.output_cleanup import clean_outputs


def test_only_complete_old_groups_are_deleted(tmp_path):
    archive = _directory(tmp_path, create=True)
    old, mixed = uuid4().hex, uuid4().hex
    run = CleanupRun(uuid4().hex, ['tool_outputs'], CacheCleanupSettings(), 65536)
    cutoff = run.cutoffs['tool_outputs']
    for name, stamp in [(old+'-raw.log', cutoff-1), (mixed+'-raw.log', cutoff-1),
                         (mixed+'-text.log', cutoff)]:
        file = archive / name
        file.write_bytes(b'abc')
        os.utime(file, ns=(stamp, stamp))
    run.select('tool_outputs', 'chat', 'test')
    clean_outputs(tmp_path, run)
    assert not (archive / (old+'-raw.log')).exists()
    assert (archive / (mixed+'-raw.log')).exists()
    assert run.result()['totals']['bytes_deleted'] == '3'


def test_archive_symlink_is_never_followed(tmp_path):
    archive = _directory(tmp_path, create=True)
    target = tmp_path / 'keep.txt'
    target.write_bytes(b'preserve')
    link = archive / (uuid4().hex + '-raw.log')
    link.symlink_to(target)
    run = CleanupRun(uuid4().hex, ['tool_outputs'], CacheCleanupSettings(), 65536)
    run.select('tool_outputs', 'chat', 'test')
    clean_outputs(tmp_path, run)
    assert run.result()['details'][0]['code'] == 'unexpected_entry'
    assert run.result()['totals']['files_deleted'] == 0
    assert link.is_symlink() and target.read_bytes() == b'preserve'


def test_foreign_entry_keeps_entire_archive(tmp_path):
    archive = _directory(tmp_path, create=True)
    log = archive / (uuid4().hex + '-raw.log')
    log.touch()
    os.utime(log, (1, 1))
    (archive / 'unmanaged').mkdir()
    run = CleanupRun(uuid4().hex, ['tool_outputs'], CacheCleanupSettings(), 65536)
    clean_outputs(tmp_path, run)
    assert log.exists()
    assert run.details[0]['code'] == 'unexpected_entry'


def test_crlf_archive_markers_are_accepted(tmp_path):
    from naiyue_test_agent.tools.output import _OWNER_FILE

    archive = _directory(tmp_path, create=True)
    for name in ('.gitignore', _OWNER_FILE):
        marker = archive / name
        marker.write_bytes(marker.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    log = archive / (uuid4().hex + '-raw.log')
    log.touch()
    os.utime(log, (1, 1))
    run = CleanupRun(uuid4().hex, ['tool_outputs'], CacheCleanupSettings(), 65536)
    clean_outputs(tmp_path, run)
    assert not log.exists()
    assert run.result()['status'] == 'completed'


def test_changed_marker_stops_before_delete(tmp_path, monkeypatch):
    from naiyue_test_agent.tools.output import _OWNER_FILE

    archive = _directory(tmp_path, create=True)
    log = archive / (uuid4().hex + '-raw.log')
    log.touch()
    os.utime(log, (1, 1))
    run = CleanupRun(uuid4().hex, ['tool_outputs'], CacheCleanupSettings(), 65536)
    check = run.check

    def replace_marker(*args, **kwargs):
        if kwargs.get('unit'):
            (archive / _OWNER_FILE).write_text('different owner')
        return check(*args, **kwargs)

    monkeypatch.setattr(run, 'check', replace_marker)
    clean_outputs(tmp_path, run)
    assert log.exists()
    assert run.details[0]['code'] == 'ownership_invalid'
