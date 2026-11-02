"""Real owner admission and fixed identities for observation tests."""
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_runner import Hello,RunnerFrame
from test_runner_control import prepared,hello


class Socket:
    def __init__(self):self.sent=[]
    async def send_json(self,value):self.sent.append(value)
    async def close(self,**kwargs):pass


async def running(runtime):
    b=prepared(runtime);socket=Socket()
    connection=await runtime.control.attach(socket,b.token.get_secret_value(),Hello.model_validate(hello(b)))
    task=runtime.registry.get_task(b.task_id)
    receipt=await runtime.control.runs.start(b.task_id,dict(command_id=str(uuid4()),expected_revision=task.revision,
        expected_generation=task.generation,message='work',model_id='A'))
    return b,connection,receipt.result['command']


def item(command,event_type,data,*,thread='root-thread',source=None):
    return dict(run_id=command['run_id'],thread_id=thread,source=source,event_type=event_type,data=data)


def frame(b,connection,records,*,seq=1,epoch=None,dropped=0):
    return RunnerFrame.validate_python(dict(kind='observations',**connection.fence,batch=dict(task_id=b.task_id,
        epoch=epoch or b.boot_id,first_seq=seq,dropped=dropped,records=records)))
