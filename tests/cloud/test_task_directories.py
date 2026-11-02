import json
from pathlib import Path

import pytest

from naiyue_test_agent.application.cloud.directories import TaskDirectories
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import config, register, template


def test_interrupted_first_directory_preparation_reconciles_without_overwriting(tmp_path, monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        task = register(runtime)
        directories = TaskDirectories(runtime.settings.data_dir)
        original = Path.mkdir
        failed = False
        def mkdir(path, *args, **kwargs):
            nonlocal failed
            if path.name == 'state' and not failed:
                failed = True
                raise OSError('fixture interrupted after workspace mkdir')
            return original(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'mkdir', mkdir)
            with pytest.raises(OSError): directories.prepare(task, template())
        workspace = runtime.settings.data_dir / 'tasks' / task.task_id / 'workspace'
        (workspace / 'keep.txt').write_text('operator kept this fixture')
        assert len(directories.prepare(task, template())) == 3
        assert (workspace / 'keep.txt').read_text() == 'operator kept this fixture'


def test_ready_directory_loss_is_not_replaced_by_empty_state(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        task = register(runtime)
        directories = TaskDirectories(runtime.settings.data_dir)
        directories.prepare(task, template())
        state = runtime.settings.data_dir / 'tasks' / task.task_id / 'state'
        state.rmdir()
        with pytest.raises((CloudError, OSError)): directories.prepare(task, template())
        assert not state.exists()


@pytest.mark.parametrize('kind', ['linked_workspace', 'unknown_parent', 'bad_marker'])
def test_directory_aliases_and_foreign_data_fail_closed(tmp_path, kind):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        task = register(runtime)
        directories = TaskDirectories(runtime.settings.data_dir)
        parent = runtime.settings.data_dir / 'tasks' / task.task_id
        if kind == 'unknown_parent':
            parent.mkdir(mode=0o700, parents=True)
            (parent / 'unknown.txt').write_text('keep')
        else:
            directories.prepare(task, template())
            if kind == 'linked_workspace':
                (parent / 'workspace').rmdir()
                (parent / 'workspace').symlink_to(runtime.settings.data_dir)
            else: (parent / 'environment.json').write_text('invalid marker')
        with pytest.raises((CloudError, OSError)): directories.prepare(task, template())
        if kind == 'unknown_parent': assert (parent / 'unknown.txt').read_text() == 'keep'
