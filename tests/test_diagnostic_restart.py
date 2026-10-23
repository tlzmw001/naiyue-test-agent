"""Hard process exit leaves committed diagnostics without replaying execution."""

import json
from pathlib import Path
import subprocess
import sys

from naiyue_test_agent.storage.session_storage import SessionStorage


def test_real_process_death_preserves_boundaries_and_marks_only_capture_interrupted(tmp_path):
    script = '''
import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_persistent_sessions import build
from test_async_application import StreamingToolModel
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.agent_runtime.async_utils import run_blocking

async def main():
    model=StreamingToolModel(replies=[],entered=asyncio.Event(),release=asyncio.Event())
    application.load_model_catalog=lambda _:ModelCatalog('A',{'A':model})
    async with open_async_persistent_application('unused',database_path=sys.argv[1],tools=[],system_prompt='',response_format=None) as app:
        sid=await app.acreate_session('A')
        asyncio.create_task(app.arun(sid,[('user','PRIVATE_PROMPT')]))
        await model.entered.wait()
        async def committed():
            while True:
                records=await run_blocking(app._storage.read_diagnostic_runs,sid)
                if records['runs']:
                    print(json.dumps({'sid':sid,'rid':records['runs'][0]['run_id']}),flush=True)
                    return
                await asyncio.sleep(0)
        await asyncio.wait_for(committed(),5)
        os._exit(37)
asyncio.run(main())
'''
    path = tmp_path / 'state.sqlite'
    result = subprocess.run([sys.executable, '-c', script, str(path)], text=True, capture_output=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=20)
    assert result.returncode == 37, result.stderr
    identity = json.loads(result.stdout)
    with SessionStorage(path) as store:
        from test_diagnostic_storage import limits
        before = store.get(identity['sid'])
        assert before['state'] == 'running'
        store.prepare_diagnostics(limits())
        page = store.read_diagnostic_events(identity['sid'], identity['rid'])
        assert page['events'][0]['event_type'] == 'run_started'
        assert page['summary']['capture_state'] == 'interrupted' and page['summary']['status'] is None
        assert page['summary']['capture_incomplete'] and store.get(identity['sid']) == before
        assert 'PRIVATE_PROMPT' not in json.dumps(page)
