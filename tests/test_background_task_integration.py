"""One installed/source acceptance chain, real tools and real disposable services."""

import asyncio
import pytest

from test_async_application import StreamingToolModel


@pytest.mark.parametrize('automatic', [False, True])
def test_formal_background_service_results_and_reopen(tmp_path, automatic):
    from scripts.release_background_smoke import background_scenario
    result = asyncio.run(background_scenario(tmp_path, StreamingToolModel, automatic=automatic))
    assert result['cross_turn_service'] == 'passed'
    assert result['completion_and_output'] == 'passed'
    assert result['reopen_empty_registry'] == 'passed'
    assert result['archives_preserved'] == 'passed'
    assert result['cleanup'] == 'passed'
    assert result['provider_requests'] == 0


def test_background_acceptance_propagates_cleanup_verifier_failure(monkeypatch, tmp_path):
    from scripts import release_background_smoke
    monkeypatch.setattr(release_background_smoke, 'process_alive', lambda pid: True)
    with pytest.raises(AssertionError, match='service process'):
        asyncio.run(release_background_smoke.background_scenario(tmp_path, StreamingToolModel))


def test_background_acceptance_with_installer_restricted_path(monkeypatch, tmp_path):
    import os
    import shutil
    from scripts.release_background_smoke import background_scenario
    executable = shutil.which('pwsh') or shutil.which('powershell.exe') if os.name == 'nt' else shutil.which('bash')
    assert executable
    monkeypatch.setenv('PATH', str(tmp_path))
    result = asyncio.run(background_scenario(tmp_path, StreamingToolModel, executable=executable))
    assert result['cleanup'] == 'passed'
