"""Version upgrade policy around the existing task gate and physical backend facts."""
from naiyue_test_agent.contracts.cloud_releases import UpgradeTask,UpgradeBackup
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .work import cloud_work


def _local(r,task_id,method,*args):
    with r.gates.hold(task_id):
        if method in ('reserve','commit'):
            request=(UpgradeTask if method=='reserve' else UpgradeBackup).model_validate(args[0])
            try:previous=r.registry.get_command(request.command_id)
            except CloudError as error:
                if error.code!='command_not_found':raise
                previous=None
            if previous is None and method=='reserve':
                task=r.registry.get_task(task_id)
                r.releases.validate_template(r.releases.get(request.release_id,task.template_id)['template'])
            elif (previous is not None and previous.operation=='release:upgrade' and previous.resource_id==task_id
                    and previous.result['phase'] not in ('committed','cancelled')):
                r.releases.validate_template(previous.result['target_template'])
        return getattr(r.releases.upgrades,method)(task_id,*args)


async def reserve_upgrade(r,task_id,value):
    request=UpgradeTask.model_validate(value)
    result=await cloud_work(r,lambda:_local(r,task_id,'reserve',request))
    if result['phase'] in ('committed','cancelled'):return result
    found=await r.lifecycle.backend.find({'nyt-instance':r.instance_id,'nyt-task':task_id,'nyt-generation':str(request.expected_generation)})
    if found:raise CloudError('task_upgrade_writer_present')
    return await cloud_work(r,lambda:_local(r,task_id,'verified',request.command_id))


def commit_upgrade(r,task_id,value):return _local(r,task_id,'commit',value)


def cancel_upgrade(r,task_id,value):return _local(r,task_id,'cancel',value)
