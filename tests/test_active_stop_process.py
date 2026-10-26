"""A stopped checkpoint must survive a real Python process boundary."""

import json
from pathlib import Path
import subprocess
import sys


CODE = "\nimport asyncio, json, sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path.cwd() / 'tests'))\nfrom test_async_application import StreamingToolModel\nfrom langchain_core.messages import AIMessage\nfrom naiyue_test_agent import application\nfrom naiyue_test_agent.model_access import ModelCatalog\nfrom scripts.component_fixtures import open_component_application\n\nasync def main():\n    database, phase = sys.argv[1:3]\n    entered, release = asyncio.Event(), asyncio.Event()\n    chosen = StreamingToolModel(replies=[AIMessage(content='continued')],\n        entered=entered if phase == 'stop' else None, release=release)\n    application.load_model_catalog = lambda _: ModelCatalog('A', {'A': chosen})\n    async with open_component_application('unused', database_path=database,\n            tools=[], system_prompt='', response_format=None) as app:\n        if phase == 'stop':\n            session = await app.host.sessions.create('A')\n            task = asyncio.create_task(app.host.runs.start(session, [('user', 'stopped')]))\n            await asyncio.wait_for(entered.wait(), 5)\n            app.host.runs.stop(session)\n            assert (await asyncio.wait_for(task, 5))['run_stopped']\n        else:\n            session = (await app.host.sessions.list())[0]['session_id']\n            await app.host.sessions.restore(session)\n            result = await app.host.runs.start(session, [('user', 'new')])\n            assert [m.content for m in result['messages']] == ['stopped', 'new', 'continued']\n        print(json.dumps((await app.host.sessions.list())[0]))\nasyncio.run(main())\n"


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
