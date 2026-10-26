"""Deletion requires exact ownership and confirmed absence of both volumes."""
import pytest
from test_docker_environment import record
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.execution.docker_environment import DockerEnvironment,labels,state_volume


class CLI:
    def __init__(self,row,*,stuck=None,bad_identity=False):
        self.row=row;self.stuck=stuck;self.calls=[]
        self.data=dict(Id='f'*64 if bad_identity else row.container_id,Image=row.image_id,
            Name='/'+row.container_name,Config=dict(Labels=labels(row)))
        self.volumes={n:dict(Name=n,Labels=labels(row)) for n in (row.volume_name,state_volume(row))}
    def daemon(self,expected):assert expected==self.row.daemon_id
    def container(self,*args,**kwargs):return self.data
    def volume(self,name):return self.volumes.get(name)
    def run(self,args,**kwargs):
        assert kwargs['mutates'];self.calls.append(args)
        if args[:2]==['container','rm']:
            if self.stuck!='container':self.data=None
        elif args[:2]==['volume','rm'] and args[-1]!=self.stuck:self.volumes.pop(args[-1])


@pytest.mark.parametrize('stuck',[None,'container','nyt-v-b','nyt-s-B'])
def test_destroy_confirms_container_and_both_volumes(stuck):
    row=record();cli=CLI(row,stuck=stuck)
    if stuck:
        with pytest.raises(SandboxError,match='env_operation_unknown'):DockerEnvironment(cli).action(row,'destroy')
    else:
        assert DockerEnvironment(cli).action(row,'destroy').lifecycle=='destroyed'
        assert cli.data is None and cli.volumes=={}
    if stuck=='container':assert len(cli.calls)==1


def test_destroy_never_adopts_a_different_known_container_id():
    row=record();cli=CLI(row,bad_identity=True)
    with pytest.raises(SandboxError,match='env_binding_mismatch'):DockerEnvironment(cli).action(row,'destroy')
    assert cli.calls==[]


def test_destroy_never_removes_a_foreign_volume():
    row=record();cli=CLI(row);cli.volumes[row.volume_name]['Labels']={'naiyue.project':'foreign'}
    with pytest.raises(SandboxError,match='env_binding_mismatch'):DockerEnvironment(cli).action(row,'destroy')
    assert all(call[:2]!=['volume','rm'] for call in cli.calls)
