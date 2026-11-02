"""Preparation writes only a new, private experiment directory."""
from types import SimpleNamespace
import tomllib

import pytest


def settings():
    return SimpleNamespace(network='s00-work',port=23456,fixture_root='/tmp/owned-s00',
        base_image='python@sha256:'+'a'*64,tools_image='s00-tools:test',task_image='s00-task:test',
        execd_image='opensandbox/execd@sha256:'+'b'*64,uid=1000,gid=1000,pids=256)


def test_prepare_generates_private_config_and_nonroot_template(tmp_path):
    from scripts.cloud_probe.prepare import prepare
    output=tmp_path/'experiment'
    prepare(output,settings())
    config=tomllib.loads((output/'server.toml').read_text())
    assert config['server']['host']=='127.0.0.1' and config['server']['port']==23456
    assert len(config['server']['api_key'])>=32
    assert config['docker']['drop_capabilities']==['ALL']
    assert config['docker']['network_mode']=='s00-work'
    assert config['runtime']['execd_image']==settings().execd_image
    assert config['storage']['allowed_host_paths']==['/tmp/owned-s00']
    assert 'USER 1000:1000' in (output/'Dockerfile.task').read_text()
    assert config['server']['api_key'] in (output/'private.credentials').read_text()
    assert (output/'server.toml').stat().st_mode&0o077==0
    assert (output/'private.credentials').stat().st_mode&0o077==0


def test_existing_directory_is_never_reused(tmp_path):
    from scripts.cloud_probe.prepare import prepare
    (tmp_path/'keep').write_text('user data')
    with pytest.raises(FileExistsError):prepare(tmp_path,settings())
    assert (tmp_path/'keep').read_text()=='user data'


@pytest.mark.parametrize('field,value',[
    ('network','net\n[server]'),('base_image','python:latest'),
    ('fixture_root','/'),('fixture_root','/tmp/../etc'),('port',0),
    ('tools_image','image\nRUN false'),
])
def test_unsafe_values_fail_before_directory_creation(tmp_path,field,value):
    from scripts.cloud_probe.prepare import prepare
    args=settings();setattr(args,field,value)
    output=tmp_path/'new'
    with pytest.raises(ValueError):prepare(output,args)
    assert not output.exists()
