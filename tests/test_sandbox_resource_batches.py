"""Batch discovery preserves metadata boundaries without per-file round trips."""
import asyncio
from types import SimpleNamespace

import pytest

from skill_helpers import write_skill
from test_envd_file_boundary import workspace


def test_twenty_skills_and_instructions_use_two_round_trips(tmp_path):
    from naiyue_test_agent.application.sessions.run_resources import prepare_resources
    from naiyue_test_agent.application.sessions.skills import prepare_run_resources
    from naiyue_test_agent.execution.docker_files import DockerFileSystem
    from naiyue_test_agent.envd.resources import resource_index,resource_read_batch
    root,_,local=workspace(tmp_path)
    (root/'AGENTS.md').write_text('Project rules')
    for source in ('agents','claude'):
        for number in range(10):write_skill(root,source,directory=f'skill-{number}',name=f'skill-{number}')
    calls=[]
    async def request(binding,operation,params,**kwargs):
        calls.append(operation)
        return {'resource_index':resource_index,'resource_read_batch':resource_read_batch}[operation](local,params)
    remote=DockerFileSystem(SimpleNamespace(arequest=request,check=lambda _:None),SimpleNamespace(environment=local.environment,session_id='S',settings={'max_message_bytes':16777216}))
    assert asyncio.run(prepare_resources(remote))==prepare_run_resources(local)
    assert calls==['resource_index','resource_read_batch']


def test_batch_read_rejects_path_replaced_by_internal_sensitive_link(tmp_path):
    from naiyue_test_agent.envd.resources import resource_index,resource_read_batch
    root,_,files=workspace(tmp_path)
    path=write_skill(root)
    info=resource_index(files,{'paths':[str(path)],'directories':[]})
    assert info['entries'][str(path)]['resolved']==str(path)
    path.unlink();secret=root/'.secret';secret.write_text('PRIVATE');path.symlink_to(secret)
    result=resource_read_batch(files,{'paths':[str(path)],'max_bytes':65536})
    assert result['entries'][str(path)]['error']=='resource_changed'
    assert 'PRIVATE' not in str(result)


def test_directory_budget_is_explicit_and_does_not_advertise_partial_catalog(tmp_path):
    from naiyue_test_agent.envd.resources import resource_index
    root,_,files=workspace(tmp_path)
    source=root/'.agents/skills';source.mkdir(parents=True)
    for number in range(257):(source/str(number)).mkdir()
    result=resource_index(files,{'paths':[],'directories':[{'path':str(source),'leaf':'SKILL.md'}]})
    assert result['entries'][str(source)]['children_error']=='resource_limit'
    assert 'children' not in result['entries'][str(source)]


def test_batch_discovery_does_not_read_rejected_links(tmp_path):
    from naiyue_test_agent.application.sessions.run_resources import prepare_resources
    from naiyue_test_agent.execution.docker_files import DockerFileSystem
    from naiyue_test_agent.envd.resources import resource_index,resource_read_batch
    root,_,local=workspace(tmp_path)
    path=write_skill(root);path.rename(path.parent/'.private');path.symlink_to('.private')
    reads=[]
    async def request(binding,operation,params,**kwargs):
        if operation=='resource_read_batch':reads.extend(params['paths'])
        return {'resource_index':resource_index,'resource_read_batch':resource_read_batch}[operation](local,params)
    remote=DockerFileSystem(SimpleNamespace(arequest=request,check=lambda _:None),SimpleNamespace(environment=local.environment,session_id='S',settings={'max_message_bytes':16777216}))
    result=asyncio.run(prepare_resources(remote))
    assert result['read_resource_roots']==() and reads==[]


def test_small_message_budget_splits_readable_files_without_failing_the_run(tmp_path):
    from naiyue_test_agent.application.sessions.run_resources import prepare_resources
    from naiyue_test_agent.execution.docker_files import DockerFileSystem
    from naiyue_test_agent.envd.resources import resource_index,resource_read_batch
    from naiyue_test_agent.envd.protocol import encode
    root,_,local=workspace(tmp_path)
    for number in range(2):write_skill(root,directory=f'skill-{number}',name=f'skill-{number}',body='x'*32768)
    batches=[]
    async def request(binding,operation,params,**kwargs):
        if operation=='resource_read_batch':batches.append(params['paths'])
        value={'resource_index':resource_index,'resource_read_batch':resource_read_batch}[operation](local,params)
        encode({'protocol':2,'result':value},65536)
        return value
    remote=DockerFileSystem(SimpleNamespace(arequest=request,check=lambda _:None),
        SimpleNamespace(environment=local.environment,session_id='S',settings={'max_message_bytes':65536}))
    result=asyncio.run(prepare_resources(remote))
    assert len(result['read_resource_roots'])==2 and len(batches)==2
