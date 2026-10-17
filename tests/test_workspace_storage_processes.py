"""Real independent processes prove ownership, initialization and crash release."""

import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread

import pytest

from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.workspaces import prepare_workspace


REPO = Path(__file__).resolve().parents[1]
OWNER = '''
import json, os, sys
from pathlib import Path
from naiyue_test_agent.workspaces import prepare_workspace
from naiyue_test_agent.application.session_storage import SessionStorage
loc = prepare_workspace(Path(sys.argv[1]), data_dir=Path(sys.argv[2]))
with SessionStorage(loc.database_path) as storage:
    storage.bind_workspace(loc.workspace_id, loc.root)
    if sys.argv[3] == 'crash':
        storage.create('S', dict(thread_id='T', model_id='M', workspace_root=loc.root,
            active_tool_names=[], permission_mode='Default'))
        storage.mark_running('S', 'R')
    print(json.dumps(dict(workspace_id=loc.workspace_id, database_path=str(loc.database_path))), flush=True)
    sys.stdin.readline()
    if sys.argv[3] == 'crash':
        os._exit(7)
'''


def owner(root, data, mode='normal'):
    process = subprocess.Popen([sys.executable, '-c', OWNER, str(root), str(data), mode],
        cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ready = Queue()
    Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
    try:
        line = ready.get(timeout=15)
        if not line:
            raise AssertionError(process.communicate(timeout=10))
        return process, json.loads(line)
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise


def stop(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=10)


def test_different_projects_run_together_same_project_rejects_and_close_releases(tmp_path):
    a, b, data = tmp_path / 'A', tmp_path / 'B', tmp_path / 'data'
    a.mkdir()
    b.mkdir()
    one, first = owner(a, data)
    two = None
    try:
        two, second = owner(b, data)
        assert one.poll() is None and two.poll() is None
        assert first['database_path'] != second['database_path']
        with pytest.raises(SessionPersistenceError, match='storage_in_use'):
            SessionStorage(first['database_path'])
        one.communicate('\n', timeout=10)
        assert one.returncode == 0
        # Stale lock file remains; its existence must never block a subsequent owner.
        with SessionStorage(first['database_path']) as reopened:
            assert reopened.get_workspace_binding()['workspace_id'] == first['workspace_id']
    finally:
        stop(one)
        if two is not None:
            stop(two)


def test_abnormal_exit_releases_lock_but_does_not_clear_running_state(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    child, result = owner(root, tmp_path / 'data', mode='crash')
    try:
        child.communicate('\n', timeout=10)
        assert child.returncode == 7
        with SessionStorage(result['database_path']) as reopened:
            assert reopened.get('S')['state'] == 'running'
            assert reopened.get('S')['run_id'] == 'R'
    finally:
        stop(child)


def test_concurrent_initialization_publishes_only_one_identity(tmp_path):
    root, data = tmp_path / '项目', tmp_path / 'data'
    root.mkdir()
    code = '''
import sys
from pathlib import Path
from naiyue_test_agent.workspaces import prepare_workspace
sys.stdin.readline()
print(prepare_workspace(Path(sys.argv[1]), data_dir=Path(sys.argv[2])).workspace_id, flush=True)
'''
    children = [subprocess.Popen([sys.executable, '-c', code, str(root), str(data)], cwd=REPO,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for process in children:
            process.stdin.write('\n')
            process.stdin.flush()
        results = [process.communicate(timeout=15) for process in children]
        assert all(process.returncode == 0 for process in children), results
        assert results[0][0].strip() == results[1][0].strip() == prepare_workspace(root, data_dir=data).workspace_id
        assert list((root / '.naiyue').glob('.workspace-*.tmp')) == []
    finally:
        for process in children:
            stop(process)
