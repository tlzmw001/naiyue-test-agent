"""Real transport decoding preserves finite failures without trusting arbitrary codes."""
import json
import pytest

from test_docker_environment import record
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.execution.docker_environment import DockerEnvironment


@pytest.mark.parametrize('mutates',[False,True])
@pytest.mark.parametrize('code',['git_result_busy','git_result_binding_mismatch','git_result_unconfirmed'])
def test_result_failure_survives_docker_transport(code,mutates):
    class CLI:
        def run(self,args,*,input_bytes=None,**kwargs):
            request=json.loads(input_bytes)
            return json.dumps(dict(request,ok=False,error={'code':code,'message':code})).encode()
    engine=DockerEnvironment(CLI());engine.validate=lambda *_a,**_k:None
    with pytest.raises(GitInputError,match=code):engine.request(record(),'result_options',{},mutates=mutates)
