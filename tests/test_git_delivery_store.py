from contextlib import closing
import pytest
from naiyue_test_agent.storage.project_registry import ProjectRegistry


def test_registry5_upgrade_and_immutable_delivery_request(tmp_path):
    from naiyue_test_agent.storage.git_deliveries import GitDeliveries
    from naiyue_test_agent.contracts.git_delivery import DeliveryCommand,GitDeliveryError
    from naiyue_test_agent.delivery_settings import DeliverySettings
    path=tmp_path/'projects.sqlite'
    with closing(ProjectRegistry(path)) as registry:
        before=registry.database.read_rows('SELECT * FROM projects')
        with registry.database.transaction(write=True) as conn:
            conn.execute('DROP TABLE git_deliveries');conn.execute('PRAGMA user_version=5')
    with closing(ProjectRegistry(path)) as registry:
        assert registry.database.read_rows('SELECT * FROM projects')==before
        records=GitDeliveries(registry.database)
        command=DeliveryCommand(operation_id='a'*32,action='branch',branch='review')
        row,new=records.reserve('P','B','R',command,DeliverySettings())
        assert new and row['state']=='preparing'
        assert records.reserve('P','B','R',command,DeliverySettings())==(row,False)
        with pytest.raises(GitDeliveryError,match='git_delivery_conflict'):
            records.reserve('P','B','R',command.model_copy(update={'branch':'other'}),DeliverySettings())
        assert registry.database.read_rows('PRAGMA user_version')==[{'user_version':6}]
        records.save('P','B','a'*32,dict(state='unknown'))
        with pytest.raises(GitDeliveryError,match='git_delivery_pending'):
            records.reserve('P','B','R',command.model_copy(update={'operation_id':'b'*32}),DeliverySettings())
        records.acknowledge('P','B','a'*32)
        assert records.reserve('P','B','R',command.model_copy(update={'operation_id':'b'*32}),DeliverySettings())[1]


def test_delivery_ownership_is_project_and_binding_scoped(tmp_path):
    from naiyue_test_agent.storage.git_deliveries import GitDeliveries
    from naiyue_test_agent.contracts.git_delivery import DeliveryCommand,GitDeliveryError
    from naiyue_test_agent.delivery_settings import DeliverySettings
    with closing(ProjectRegistry(tmp_path/'projects.sqlite')) as registry:
        records=GitDeliveries(registry.database)
        records.reserve('P','B','R',DeliveryCommand(operation_id='a'*32,action='discard'),DeliverySettings())
        for project,binding in [('other','B'),('P','other')]:
            with pytest.raises(GitDeliveryError,match='git_delivery_not_found'):records.get(project,binding,'a'*32)
