"""A stopped checkpoint must survive a real Python process boundary."""

import json
from pathlib import Path
import subprocess
import sys


CODE = '''
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_async_application import StreamingToolModel
from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.application.async_persistent import open_async_persistent_application

async def main():
    database, phase = sys.argv[1:3]
    entered, release = asyncio.Event(), asyncio.Event()
    chosen = StreamingToolModel(replies=[AIMessage(content='continued')],
        entered=entered if phase == 'stop' else None, release=release)
    application.load_model_catalog = lambda _: ModelCatalog('A', {'A': chosen})
    async with open_async_persistent_application('unused', database_path=database,
            tools=[], system_prompt='', response_format=None) as app:
        if phase == 'stop':
            session = await app.acreate_session('A')
            task = asyncio.create_task(app.arun(session, [('user', 'stopped')]))
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(session)
            assert (await asyncio.wait_for(task, 5))['run_stopped']
        else:
            session = (await app.alist_sessions())[0]['session_id']
            await app.arestore_session(session)
            result = await app.arun(session, [('user', 'new')])
            assert [m.content for m in result['messages']] == ['stopped', 'new', 'continued']
        print(json.dumps((await app.alist_sessions())[0]))
asyncio.run(main())
'''


def test_two_processes_stop_then_restore(tmp_path):
    rows = []
    for phase in ('stop', 'resume'):
        result = subprocess.run([sys.executable, '-c', CODE, str(tmp_path / 'sessions.sqlite'), phase],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        rows.append(json.loads(result.stdout))
    first, second = rows
    assert first['state'] == second['state'] == 'ready'
    assert first['last_run_status'] == 'stopped' and second['last_run_status'] == 'completed'
    assert first['session_id'] == second['session_id'] and first['thread_id'] == second['thread_id']
    assert first['run_id'] != second['run_id'] and first['checkpoint_id'] != second['checkpoint_id']
