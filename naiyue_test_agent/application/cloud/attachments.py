"""Owner attachment custody and run-bound task download authorization."""
from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_inputs import AttachInput
from naiyue_test_agent.contracts.cloud_tasks import CloudError,TaskId,UUIDText


def attach(runtime,task_id,value):
    task_id=TypeAdapter(TaskId).validate_python(task_id);request=AttachInput.model_validate(value)
    with runtime.gates.hold(task_id):
        return runtime.inputs.repository.attach(task_id,request)


def for_command(runtime,task_id,command_id,attachment_id):
    command_id=TypeAdapter(UUIDText).validate_python(command_id)
    attachment_id=TypeAdapter(UUIDText).validate_python(attachment_id)
    receipt=runtime.registry.get_command(command_id)
    if receipt.resource_id!=task_id or receipt.operation!='run:start':raise CloudError('attachment_not_found',404)
    from naiyue_test_agent.contracts.cloud_runner import RunCommand
    command=RunCommand.model_validate(receipt.result['command'])
    attachment=next((v for v in command.attachments if v.attachment_id==attachment_id),None)
    if attachment is None:raise CloudError('attachment_not_found',404)
    return attachment
