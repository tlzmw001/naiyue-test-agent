"""Real process death leaves only committed facts; startup never replays calls."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from naiyue_test_agent.storage.project_store import ProjectStore


@pytest.mark.parametrize('point', ['start', 'end'])
def test_actual_crash_preserves_known_usage_and_marks_unfinished_root(tmp_path, point):
    script = '''
import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from test_async_application import StreamingToolModel

class CrashModel(StreamingToolModel):
    async def _next(self,messages):
        if self.seen and self.entered is not None:
            self.entered.set()
            await self.release.wait()
        return await super()._next(messages)

async def main():
    model=CrashModel(replies=[AIMessage(content='first',usage_metadata={
        'input_tokens':100,'output_tokens':5,'total_tokens':105}), AIMessage(content='second')])
    application.load_model_catalog=lambda _:ModelCatalog('A',{'A':model})
    async with open_async_persistent_application('unused',database_path=sys.argv[1],tools=[],
            system_prompt='',response_format=None) as app:
        sid=await app.acreate_session('A')
        await app.arun(sid,[('user','first')])
        def crash(*args,**kwargs):
            print(json.dumps({'sid':sid}),flush=True)
            os._exit(31)
        if sys.argv[2]=='end':
            app._storage.usage.finish_model_usage_run=crash
            await app.arun(sid,[('user','second')])
        else:
            model.entered,model.release=asyncio.Event(),asyncio.Event()
            task=asyncio.create_task(app.arun(sid,[('user','second')]))
            await model.entered.wait()
            async def committed():
                while True:
                    records=await run_blocking(app._storage.database.read_rows,
                        "SELECT invocation_id FROM naiyue_model_invocations WHERE status='started'")
                    if records:return
                    await asyncio.sleep(0)
            await asyncio.wait_for(committed(),5)
            crash()
asyncio.run(main())
'''
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
