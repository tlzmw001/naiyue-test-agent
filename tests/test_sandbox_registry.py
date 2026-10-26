"""Environment facts use the registry owner without changing project stores."""
import pytest
from contextlib import closing
from naiyue_test_agent.contracts.sandbox import SandboxRecord, SandboxError
from naiyue_test_agent.storage.project_registry import ProjectRegistry


def test_environment_reservation_is_unique_and_survives_reopen(tmp_path):
    from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry
    path=tmp_path/'projects.sqlite'
    with closing(ProjectRegistry(path)) as registry:
        records=EnvironmentRegistry(registry.database)
        row=SandboxRecord('B','P','S','creating','D','container','volume','sha256:image',{})
        records.reserve(row)
        with pytest.raises(SandboxError, match='env_already_exists'):
            records.reserve(row.changed(binding_id='C'))
        records.save(row.changed(lifecycle='running',container_id='container-id'))
    with closing(ProjectRegistry(path)) as registry:
        records=EnvironmentRegistry(registry.database)
        assert records.get('B').container_id=='container-id'
        assert records.list('P')[0].environment.kind=='sandbox'
        assert records.list('another')==()
        assert registry.database.read_rows('PRAGMA user_version')==[{'user_version':6}]
