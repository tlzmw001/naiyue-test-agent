"""Probe catalog guards use the formal loader and clean up failed installation."""

import asyncio

import pytest

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from scripts import provider_acceptance_budget as limits


def test_guard_installs_before_handing_catalog_to_application_and_restores_loader(monkeypatch):
    async def scenario():
        seen = []
        catalog = ModelCatalog('empty', {})
        original = lambda path: seen.append(('load', path)) or catalog
        budget = limits.RequestBudget(2)
        monkeypatch.setattr(application, 'load_model_catalog', original)
        monkeypatch.setattr(limits, 'guard_catalog', lambda cat, quota: seen.append(('guard', cat, quota)))
        async with limits.guard_application_catalog(budget):
            assert application.load_model_catalog('synthetic') is catalog
            assert seen == [('load', 'synthetic'), ('guard', catalog, budget)]
        assert application.load_model_catalog is original
    asyncio.run(scenario())


def test_failed_guard_closes_catalog_without_replacing_installation_failure(monkeypatch, caplog):
    async def scenario():
        failure = ValueError('synthetic install failure')
        closed = []
        class Catalog:
            async def aclose(self):
                closed.append(True)
                raise RuntimeError('synthetic close failure')
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: Catalog())
        def reject(*_):
            raise failure
        monkeypatch.setattr(limits, 'guard_catalog', reject)
        with pytest.raises(ValueError) as caught:
            async with limits.guard_application_catalog(limits.RequestBudget(1)):
                application.load_model_catalog('synthetic')
        assert caught.value is failure and closed == [True]
        assert 'probe_catalog_close_failed' in caplog.text
    asyncio.run(scenario())
