"""Hard process exit leaves committed diagnostics without replaying execution."""

import json
from pathlib import Path
import subprocess
import sys

from naiyue_test_agent.storage.project_store import ProjectStore


def test_real_process_death_preserves_boundaries_and_marks_only_capture_interrupted(tmp_path):
    script = "\nimport asyncio,json,os,sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path.cwd()/'tests'))\nfrom test_persistent_sessions import build\nfrom test_async_application import StreamingToolModel\nfrom naiyue_test_agent import application\nfrom naiyue_test_agent.model_access import ModelCatalog\nfrom naiyue_test_agent.application.async_persistent import open_async_persistent_application\nfrom naiyue_test_agent.agent_runtime.async_utils import run_blocking\n\nasync def main():\n    model=StreamingToolModel(replies=[],entered=asyncio.Event(),release=asyncio.Event())\n    application.load_model_catalog=lambda _:ModelCatalog('A',{'A':model})\n    async with open_async_persistent_application('unused',database_path=sys.argv[1],tools=[],system_prompt='',response_format=None) as app:\n        sid=await app.sessions.create('A')\n        asyncio.create_task(app.runs.start(sid,[('user','PRIVATE_PROMPT')]))\n        await model.entered.wait()\n        async def committed():\n            while True:\n                records=await run_blocking(app.sessions._persistence.storage.diagnostics.read_diagnostic_runs,sid)\n                if records['runs']:\n                    print(json.dumps({'sid':sid,'rid':records['runs'][0]['run_id']}),flush=True)\n                    return\n                await asyncio.sleep(0)\n        await asyncio.wait_for(committed(),5)\n        os._exit(37)\nasyncio.run(main())\n"
    path = tmp_path / 'state.sqlite'
    result = subprocess.run([sys.executable, '-c', script, str(path)], text=True, capture_output=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=20)
    assert result.returncode == 37, result.stderr
    identity = json.loads(result.stdout)
    with ProjectStore(path) as store:
        from test_diagnostic_storage import limits
        before = store.sessions.get(identity['sid'])
        assert before['state'] == 'running'
        store.diagnostics.prepare_diagnostics(limits())
        page = store.diagnostics.read_diagnostic_events(identity['sid'], identity['rid'])
        assert page['events'][0]['event_type'] == 'run_started'
        assert page['summary']['capture_state'] == 'interrupted' and page['summary']['status'] is None
        assert page['summary']['capture_incomplete'] and store.sessions.get(identity['sid']) == before
        assert 'PRIVATE_PROMPT' not in json.dumps(page)
