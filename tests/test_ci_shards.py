"""CI file partitions must cover the complete suite exactly once."""

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci_shards import test_files


def test_shards_cover_nested_files_and_keep_parameterized_files_together(tmp_path):
    names = ['test_a.py', 'nested/test_b.py', 'nested/c_test.py', 'helpers.py']
    for name in names:
        p = tmp_path / name
        p.parent.mkdir(exist_ok=True)
        p.write_text('')
    first, second = (test_files(tmp_path, i, 2) for i in range(2))
    assert not set(first) & set(second)
    assert set(first + second) == {tmp_path / name for name in names[:-1]}
    assert first == test_files(tmp_path, 0, 2)


@pytest.mark.parametrize('index,count', [(-1, 2), (2, 2), (0, 0), (0, -1)])
def test_invalid_shards_are_rejected(tmp_path, index, count):
    with pytest.raises(ValueError, match='shard'):
        test_files(tmp_path, index, count)


def test_cli_propagates_pytest_failure_and_emits_junit(tmp_path):
    from scripts import ci_shards
    directory = tmp_path / 'tests'
    directory.mkdir()
    (directory / 'test_example.py').write_text('def test_fails():\n    assert False\n')
    output = tmp_path / 'result.xml'
    run = subprocess.run([sys.executable, str(Path(ci_shards.__file__)),
                          '--directory', str(directory), '--index', '0', '--count', '1',
                          '--output', str(output)], capture_output=True, text=True)
    assert run.returncode == 1, run.stdout + run.stderr
    assert '1 failed' in run.stdout
    assert output.is_file()


test_files.__test__ = False
