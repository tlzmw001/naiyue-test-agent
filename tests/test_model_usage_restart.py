"""Real process death leaves only committed facts; startup never replays calls."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from naiyue_test_agent.storage.project_store import ProjectStore


@pytest.mark.parametrize('point', ['start', 'end'])
def test_actual_crash_preserves_known_usage_and_marks_unfinished_root(tmp_path, point):
    script = '\nimport asyncio,json,os,sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path.cwd()/\'tests\'))\nfrom langchain_core.messages import AIMessage\nfrom naiyue_test_agent import application\nfrom naiyue_test_agent.model_access import ModelCatalog\nfrom naiyue_test_agent.application.async_persistent import open_async_persistent_application\nfrom naiyue_test_agent.agent_runtime.async_utils import run_blocking\nfrom test_async_application import StreamingToolModel\n\nclass CrashModel(StreamingToolModel):\n    async def _next(self,messages):\n        if self.seen and self.entered is not None:\n            self.entered.set()\n            await self.release.wait()\n        return await super()._next(messages)\n\nasync def main():\n    model=CrashModel(replies=[AIMessage(content=\'first\',usage_metadata={\n        \'input_tokens\':100,\'output_tokens\':5,\'total_tokens\':105}), AIMessage(content=\'second\')])\n    application.load_model_catalog=lambda _:ModelCatalog(\'A\',{\'A\':model})\n    async with open_async_persistent_application(\'unused\',database_path=sys.argv[1],tools=[],\n            system_prompt=\'\',response_format=None) as app:\n        sid=await app.sessions.create(\'A\')\n        await app.runs.start(sid,[(\'user\',\'first\')])\n        def crash(*args,**kwargs):\n            print(json.dumps({\'sid\':sid}),flush=True)\n            os._exit(31)\n        if sys.argv[2]==\'end\':\n            app.sessions._persistence.storage.usage.finish_model_usage_run=crash\n            await app.runs.start(sid,[(\'user\',\'second\')])\n        else:\n            model.entered,model.release=asyncio.Event(),asyncio.Event()\n            task=asyncio.create_task(app.runs.start(sid,[(\'user\',\'second\')]))\n            await model.entered.wait()\n            async def committed():\n                while True:\n                    records=await run_blocking(app.sessions._persistence.storage.database.read_rows,\n                        "SELECT invocation_id FROM naiyue_model_invocations WHERE status=\'started\'")\n                    if records:return\n                    await asyncio.sleep(0)\n            await asyncio.wait_for(committed(),5)\n            crash()\nasyncio.run(main())\n'
    path = tmp_path / 'state.sqlite'
    completed = subprocess.run([sys.executable, '-c', script, str(path), point],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=20)
    assert completed.returncode == 31, completed.stderr
    sid = json.loads(completed.stdout)['sid']
    with ProjectStore(path) as store:
        saved = store.database.read_rows('SELECT * FROM naiyue_model_invocations WHERE session_id=? ORDER BY started_at', (sid,))
        assert len(saved) == 2 and saved[0]['status'] == 'returned' and saved[0]['input_tokens'] == 100
        assert saved[1]['status'] == ('unconfirmed' if point == 'start' else 'returned')
        assert saved[1]['input_tokens'] is None
        assert store.sessions.get(sid)['state'] == 'running'
        runs = store.database.read_rows('SELECT * FROM naiyue_model_usage_runs WHERE session_id=?', (sid,))
        assert len(runs) == 2 and sum(r['capture_incomplete'] for r in runs) == 1
        assert any(r['reason'] == 'interrupted_execution' for r in runs)
        assert store.database.connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] > 0
