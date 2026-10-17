"""Owned archives accept ordinary permissions but reject redirected objects."""

from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

from naiyue_test_agent.tools.output import ShellOutputArchive, _directory, preview_shell_outputs, clear_shell_outputs
from naiyue_test_agent.tools.settings import BashSettings, ToolOutputSettings


def test_owned_0755_archive_can_save_read_and_clear(tmp_path):
    directory = _directory(tmp_path, create=True)
    directory.chmod(0o755)
    original_mode = stat.S_IMODE(directory.stat().st_mode)
    archive = ShellOutputArchive(tmp_path, BashSettings(), ToolOutputSettings())
    archive.record(b'hello', activate=True)
    result = archive.finish()
    assert result.complete
    assert (tmp_path / result.paths['raw']).read_bytes() == b'hello'
    plan = preview_shell_outputs(tmp_path)
    assert (plan.file_count, plan.total_bytes) == (1, 5)
    assert clear_shell_outputs(plan) == (1, 5)
    assert stat.S_IMODE(directory.stat().st_mode) == original_mode


@pytest.mark.parametrize('kind', ['directory', 'marker', 'artifact'])
def test_reparse_objects_cannot_be_used_for_archive_or_cleanup(tmp_path, monkeypatch, kind):
    directory = _directory(tmp_path, create=True)
    artifact = directory / ('a' * 32 + '-raw.log')
    artifact.write_bytes(b'keep')
    selected = {'directory': directory, 'marker': directory / '.naiyue-bash-output-v1', 'artifact': artifact}[kind]
    real = Path.lstat
    def lstat(path, *args, **kwargs):
        value = real(path, *args, **kwargs)
        if path == selected:
            return SimpleNamespace(st_mode=value.st_mode, st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns, st_ino=value.st_ino, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return value
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ValueError):
        preview_shell_outputs(tmp_path)
    assert artifact.read_bytes() == b'keep'
