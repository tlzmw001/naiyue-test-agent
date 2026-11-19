"""Installed smoke exercises sampling across real approval/resume requests."""
import asyncio
from pathlib import Path

from naiyue_test_agent.agent_runtime.context_calibration import KEY


def test_install_web_smoke_saves_a_sample_before_the_next_request(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    from naiyue_test_agent import application, resources
    monkeypatch.setattr(application, 'load_model_catalog', application.load_model_catalog)
    frontend = tmp_path / 'frontend'
    assets = frontend / 'assets'
    assets.mkdir(parents=True)
    (frontend / 'index.html').write_text('<html><script src="/assets/smoke.js"></script></html>', encoding='utf-8')
    (assets / 'smoke.js').write_text('/* calibration fixture */', encoding='utf-8')
    monkeypatch.setattr(resources, 'frontend_directory', lambda: frontend)
    import release_mcp_smoke
    async def no_mcp(*args):
        return {}
    monkeypatch.setattr(release_mcp_smoke, 'mcp_scenario', no_mcp)
    from scripts.release_smoke import web_scenario
    _, model, _ = asyncio.run(web_scenario(tmp_path))
    sampled = [m for m in model.seen[1] if KEY in m.response_metadata]
    assert len(sampled) == 1
    assert sampled[0].response_metadata[KEY]['agent']['text']['actual_input_tokens'] == 4000
