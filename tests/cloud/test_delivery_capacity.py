"""Confirmed delivery shares the same producer budget as preparation."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4
import pytest
from naiyue_test_agent.application.cloud_local.deliveries import LocalDeliveries
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
from naiyue_test_agent.contracts.cloud_receive import ReceiveLimits
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_confirm_cannot_bypass_global_delivery_capacity_across_projects():
    async def scenario():
        settings=CloudTaskSettings(receive_limits=ReceiveLimits(max_active=1))
        service=LocalDeliveries(SimpleNamespace(settings=settings,operation=nullcontext,admission=asyncio.Lock()))
        service._check_root=lambda domain,meta:None
        domains=[];saves=[]
        for _ in range(2):
            record=DeliveryRecord(command_id=str(uuid4()),local_project_id=str(uuid4()),local_session_id=uuid4().hex,
                receive_id=str(uuid4()),action='apply',phase='prepared')
            def read(cid,sid,record=record):return record,{'verified':True}
            repo=SimpleNamespace(read=read,save=lambda record:saves.append(record) or record)
            domains.append((SimpleNamespace(project_id=record.local_project_id,owner=SimpleNamespace(operation=nullcontext),deliveries=repo),record))
        def launch(domain,record,execute):service.flights[(domain.project_id,record.command_id)]=object()
        service.launch=launch
        a,ar=domains[0];b,br=domains[1]
        assert (await service.execute(a,ar.command_id,ar.local_session_id)).phase=='executing'
        with pytest.raises(CloudError,match='cloud_delivery_capacity'):
            await service.execute(b,br.command_id,br.local_session_id)
        assert len(saves)==1 and len(service.flights)==1
    asyncio.run(scenario())
