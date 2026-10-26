"""Global admission uses durable reservations, not an optimistic Docker count."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest
from pydantic import ValidationError

from naiyue_test_agent.contracts.sandbox import SandboxError, SandboxRecord
from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
from naiyue_test_agent.storage.project_registry import ProjectRegistry


def row(ident, state='creating'):
    return SandboxRecord(ident, 'P-'+ident, 'S-'+ident, state, 'D', 'c-'+ident, 'v-'+ident, 'image', {})


def test_lifecycle_defaults_and_hot_policy_are_not_part_of_frozen_execution():
    configured = SandboxConfiguration.model_validate({'image': 'fixture:image'})
    policy = configured.lifecycle
    assert (policy.max_running, policy.max_retained, policy.idle_stop_seconds, policy.sweep_seconds) == (2, 10, 1800, 30)
    changed = SandboxConfiguration.model_validate({'image': 'fixture:image', 'lifecycle': {'max_running': 1, 'idle_stop_seconds': 0}})
    assert changed.select().revision == configured.select().revision
    assert 'lifecycle' not in changed.select().settings.model_dump()
    assert SandboxConfiguration.model_validate_json(changed.model_dump_json()) == changed


@pytest.mark.parametrize('value', [{'max_running': 0}, {'max_retained': 0}, {'max_running': 3, 'max_retained': 2},
    {'idle_stop_seconds': -1}, {'sweep_seconds': 0}, {'max_running': '2'}])
def test_lifecycle_configuration_rejects_invalid_values(value):
    with pytest.raises(ValidationError): SandboxConfiguration.model_validate({'lifecycle': value})


def test_running_and_retained_slots_are_released_only_by_confirmed_states(tmp_path):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        records = EnvironmentRegistry(registry.database)
        policy = SandboxConfiguration().lifecycle.model_copy(update={'max_running': 1, 'max_retained': 2})
        records.reserve(row('A'), limits=policy)
        with pytest.raises(SandboxError, match='env_running_capacity'): records.reserve(row('B'), limits=policy)
        records.save(records.get('A').changed(lifecycle='unknown'))
        assert records.usage() == {'running': 1, 'retained': 1}
        records.save(records.get('A').changed(lifecycle='stopped'))
        records.reserve(row('B'), limits=policy)
        with pytest.raises(SandboxError, match='env_retained_capacity'): records.reserve(row('C'), limits=policy)
        records.save(records.get('A').changed(lifecycle='destroyed'))
        records.save(records.get('B').changed(lifecycle='stopped'))
        records.reserve(row('C'), limits=policy)
        with pytest.raises(SandboxError, match='env_running_capacity'): records.reserve_start('B', limits=policy)
        records.save(records.get('C').changed(lifecycle='stopped'))
        assert records.reserve_start('B', limits=policy).lifecycle == 'starting'
        assert records.usage() == {'running': 1, 'retained': 2}


def test_concurrent_cross_project_reservations_cannot_both_take_the_last_slot(tmp_path):
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        records = EnvironmentRegistry(registry.database)
        policy = SandboxConfiguration().lifecycle.model_copy(update={'max_running': 1})
        ready = Barrier(2)
        def reserve(ident):
            ready.wait(5)
            try: records.reserve(row(ident), limits=policy); return 'accepted'
            except SandboxError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ['A', 'B']))
        assert sorted(results) == ['accepted', 'env_running_capacity']
        assert len(records.list_all()) == 1


def test_activity_excludes_management_and_readonly_observation_does_not_extend_idle(tmp_path, monkeypatch):
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        records = EnvironmentRegistry(registry.database); records.reserve(row('A', 'running'))
        activity = EnvironmentActivity(lambda: records)
        with activity.use('A', used=False):
            with pytest.raises(SandboxError, match='env_busy'):
                with activity.manage('A'): pytest.fail('management entered during use')
        assert records.get('A').last_used_at is None
        with activity.manage('A'):
            with pytest.raises(SandboxError, match='env_busy'):
                with activity.use('A'): pytest.fail('use entered during management')
        with activity.use('A'): pass
        assert records.get('A').last_used_at is not None
        before = records.get('A').last_used_at
        with activity.use('A', used=False): pass
        assert records.get('A').last_used_at == before


def test_activity_timestamp_failure_is_visible_and_disables_automatic_stopping(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.environments.activity import EnvironmentActivity
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        records = EnvironmentRegistry(registry.database); records.reserve(row('A', 'running'))
        activity = EnvironmentActivity(lambda: records)
        def broken(*args): raise OSError('private database details')
        monkeypatch.setattr(records, 'touch', broken)
        with activity.use('A'): result = 'original result'
        assert result == 'original result' and activity.uncertain('A')
        assert 'sandbox_activity_save_failed' in caplog.text and 'private database details' not in caplog.text
