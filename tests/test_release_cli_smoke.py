"""The install check drives the terminal over its own real TCP service."""
import asyncio
from pathlib import Path

from test_async_application import StreamingToolModel


def test_install_cli_scenario_uses_http_and_a_temporary_chat(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from scripts.release_smoke import cli_scenario
    (tmp_path / 'workspace').mkdir()
    assert asyncio.run(cli_scenario(tmp_path, StreamingToolModel)) == {
        'http_sse': 'passed', 'temporary_chat': 'passed'}
    assert not list((tmp_path / 'workspace').rglob('*.sqlite*'))
    assert not (tmp_path / 'workspace/.naiyue/workspace.json').exists()
