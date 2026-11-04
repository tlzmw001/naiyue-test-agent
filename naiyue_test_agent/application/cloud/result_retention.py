"""Retire only obsolete fixed results, with durable intent before removing owned bytes."""
import logging,time
from uuid import UUID
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_results import ResultRecord
from naiyue_test_agent.storage.cloud.result_retention import ResultRetentionRepository
from .task_cleanup import remove_task_data

logger=logging.getLogger(__name__)


class ResultRetention:
    def __init__(self,service):
        self.service=service;self.runtime=service.runtime;self.after=''
        self.repository=ResultRetentionRepository(self.runtime.database)

    @staticmethod
    def lease_id(value):
        if set(value)!={'lease_id'} or not isinstance(value['lease_id'],str):raise CloudError('invalid_request',422)
        try:
            if str(UUID(value['lease_id']))!=value['lease_id']:raise ValueError()
        except ValueError:raise CloudError('invalid_request',422) from None
        return value['lease_id']

    def lease(self,task_id,result_id,value):
        lease_id=self.lease_id(value)
        with self.runtime.gates.hold(task_id):
            record=self.service._read(task_id,result_id)[1]
            if record.phase!='available':raise CloudError('result_not_available')
            return self.repository.lease(task_id,result_id,lease_id,time.time(),self.runtime.settings.result_limits)

    def release(self,task_id,result_id,value):
        lease_id=self.lease_id(value)
        with self.runtime.gates.hold(task_id):
            self.service._read(task_id,result_id)
            self.repository.release(task_id,result_id,lease_id)
            return dict(result_id=result_id,lease_id=lease_id,released=True)

    def capacity(self,task):
        r=self.runtime;limit=r.settings.result_limits.max_results
        if task.pending_result_id:raise CloudError('result_capacity')
        if len(task.result_ids)<limit:return
        if len(task.result_ids)>limit:raise CloudError('result_capacity')
        # One rollover slot allows even max_results=1 to replace the latest result.
        latest=next((v for v in reversed(task.result_ids) if self.service._read(task.task_id,v)[1].phase=='available'),None)
        with r.task_io.condition:active=r.task_io.active.get(task.task_id,0)
        if latest is None or active or self.repository.live(task.task_id,latest,time.time()):raise CloudError('result_capacity')

    def collect(self,task_id,*,now=None):
        r=self.runtime;now=time.time() if now is None else now
        with r.task_io.hold(task_id):
            with r.gates.hold(task_id):
                rows=self.repository.candidates(task_id)
                task=r.available_task(task_id)
                latest=next((v for v in reversed(task.result_ids) if self.service._read(task_id,v)[1].phase=='available'),None)
            for item in rows:
                try:lease=r.releases.enter_io(continuation=item.command_id)
                except CloudError as error:
                    if error.code=='cloud_maintenance':continue
                    raise
                try:
                    with r.gates.hold(task_id):
                        with r.task_io.condition:active=r.task_io.active.get(task_id)
                        if active!=1:return
                        task=r.available_task(task_id);receipt,record=self.service._read(task_id,item.command_id)
                        if record.phase!='deleting':
                            if (record.phase not in ('available','abandoned') or record.plan.result_id==latest
                                    or self.repository.live(task_id,record.plan.result_id,now)):continue
                            record=record.model_copy(update={'phase':'deleting','retired_phase':record.phase,'revision':record.revision+1})
                            receipt=receipt.model_copy(update={'result':record.model_dump(mode='json'),'updated_at':now})
                            candidate=task.model_copy(update={'revision':task.revision+1,'updated_at':now})
                            self.service.repository.finish(task,candidate,receipt)
                    ids=[v.artifact.artifact_id for v in record.bundle.files] if record.bundle else []
                    try:remove_task_data(r.settings.data_dir,task_id,r.settings.resource_limits,artifact_ids=ids)
                    except (CloudError,OSError) as error:
                        logger.warning('cloud_result_cleanup_failed error_type=%s',type(error).__name__)
                        continue
                    with r.gates.hold(task_id):
                        task=r.available_task(task_id);receipt,current=self.service._read(task_id,record.plan.result_id)
                        if current!=record:raise CloudError('result_identity_conflict')
                        state=current.model_copy(update={'phase':'deleted','bundle':None,'revision':current.revision+1})
                        candidate=task.model_copy(update={'revision':task.revision+1,'updated_at':now,
                            'result_ids':[v for v in task.result_ids if v!=record.plan.result_id]})
                        self.service.repository.finish(task,candidate,receipt.model_copy(update={
                            'result':state.model_dump(mode='json'),'updated_at':now}),retired_artifacts=ids)
                finally:r.releases.leave_io(lease)

    def sweep(self):
        page=self.service.repository.task_page(self.after,self.runtime.settings.resource_limits.task_batch_size)
        for task in page:
            if task.lifecycle not in ('deleting','deleted'):
                try:self.collect(task.task_id)
                except (CloudError,OSError) as error:
                    logger.warning('cloud_result_maintenance_failed error_type=%s',type(error).__name__)
        self.after=page[-1].task_id if page else ''
