"""Probe argv is fixed, bounded and preserves Windows SSH prerequisites."""
import pytest


def test_probe_ssh_environment_keeps_programdata_and_omits_secrets():
    from scripts.sandbox_probe.target import child_environment
    assert child_environment({'PROGRAMDATA':'system','PATH':'bin','MODEL_TOKEN':'secret'})=={
        'PROGRAMDATA':'system','PATH':'bin'}


def test_ssh_argv_keeps_alias_and_quotes_remote_docker_arguments():
    import shlex
    from scripts.sandbox_probe.target import Target
    target=Target('nyt-e-example',ssh_target='jump-example',socket_path='/var/run/docker.sock')
    args=target.command(['container','exec','-i','nyt-e-example','python','-c',"print('quoted; literal')"])
    assert args[-2]=='jump-example'
    assert 'BatchMode=yes' in args and 'StrictHostKeyChecking=yes' in args
    assert shlex.split(args[-1])[1:]==['docker','--host','unix:///var/run/docker.sock',
        'container','exec','-i','nyt-e-example','python','-c',"print('quoted; literal')"]


@pytest.mark.parametrize('container,host',[('--all',None),('x; rm',None),('valid','-oProxyCommand=x')])
def test_unsafe_target_rejected_before_spawn(container,host):
    from scripts.sandbox_probe.target import Target
    with pytest.raises(ValueError):Target(container,ssh_target=host)


def test_metrics_cannot_claim_success_for_an_empty_series():
    from scripts.sandbox_probe.measure import distribution
    assert distribution([])=={'count':0,'p50_ms':None,'p95_ms':None,'max_ms':None}
    assert distribution([.1,.2,.3])=={'count':3,'p50_ms':200.,'p95_ms':300.,'max_ms':300.}
