"""Real filesystem attacks against the privileged file API, with deterministic hooks."""
import io
import os
from pathlib import Path

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.contracts.environment import EnvironmentRef,ResourceBoundaryError
from naiyue_test_agent.envd.files import EnvdFiles
from naiyue_test_agent.tools.settings import ReadSettings, WriteSettings


def workspace(tmp_path):
    root = tmp_path/'workspace'; root.mkdir()
    state = tmp_path/'state'; state.mkdir()
    (state/'report.txt').write_text('CONTROL SECRET')
    return root, state, EnvdFiles(EnvironmentRef('B','sandbox','P',str(root),'linux'),'S')


@pytest.mark.parametrize('tool', [False, True])
def test_read_cannot_follow_parent_replaced_after_check(tmp_path, monkeypatch, tool):
    root, state, files = workspace(tmp_path)
    parent = root/'reports'; parent.mkdir()
    (parent/'report.txt').write_text('workspace content')
    subject = files.prepare_target('reports/report.txt', 'read')
    original_open, original_io = os.open, io.open
    swapped = []

    def attack(path):
        if isinstance(path, (str, Path)) and Path(path).name == 'report.txt' and not swapped:
            parent.rename(root/'original')
            parent.symlink_to(state, target_is_directory=True)
            swapped.append(True)

    def fd_open(path, *args, **kwargs):
        attack(path)
        return original_open(path, *args, **kwargs)

    def stream_open(path, *args, **kwargs):
        attack(path)
        return original_io(path, *args, **kwargs)

    monkeypatch.setattr(os, 'open', fd_open)
    monkeypatch.setattr(io, 'open', stream_open)
    try:
        result = (files.read(subject,None,None,settings=ReadSettings(),control=None,progress=None)
                  if tool else files.read_resource(str(parent/'report.txt'),100))
    except (OSError, RuntimeError) as error:
        assert 'SECRET' not in str(error)
    except ToolException as error:
        assert 'outside' in str(error) or 'changed' in str(error)
    else:
        assert 'CONTROL SECRET' not in str(result)
        assert 'workspace content' in str(result)
    assert swapped == [True], 'attack must execute, not merely test an unvisited hook'


def test_internal_links_work_but_escape_cycle_and_special_files_fail(tmp_path):
    root, state, files = workspace(tmp_path)
    (root/'report.txt').write_text('safe')
    (root/'alias').symlink_to('report.txt')
    assert files.read_resource(str(root/'alias'),100) == b'safe'
    (root/'escape').symlink_to(state/'report.txt')
    with pytest.raises(ResourceBoundaryError): files.read_resource(str(root/'escape'),100)
    (root/'loop').symlink_to('loop')
    with pytest.raises(OSError,match='unavailable'): files.read_resource(str(root/'loop'),100)


def test_resource_read_rejects_hardlinked_file(tmp_path):
    root, state, files = workspace(tmp_path)
    os.link(state/'report.txt', root/'alias')
    with pytest.raises(OSError,match='unavailable'): files.read_resource(str(root/'alias'),100)


def test_write_rejects_hardlinked_file_and_preserves_outside(tmp_path):
    root, state, files = workspace(tmp_path)
    os.link(state/'report.txt', root/'alias')
    with pytest.raises(ToolException,match='hard link'):
        subject = files.prepare_target('alias','write')
        subject.update(args={'path':'alias'},path_field='path',tool_name='write')
        files.write(subject,'bad',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
    assert (state/'report.txt').read_text() == 'CONTROL SECRET'


def test_skills_discovery_diagnoses_escape_without_aborting(tmp_path):
    from naiyue_test_agent.application.sessions.skills import discover_project_skills
    root,state,files=workspace(tmp_path)
    (root/'.agents').mkdir()
    (root/'.agents'/'skills').symlink_to(state,target_is_directory=True)
    catalog=discover_project_skills(files)
    assert not catalog.entries
    assert catalog.diagnostics


def test_prepared_batch_write_then_read_keeps_path_authorization(tmp_path):
    root,state,files=workspace(tmp_path)
    (root/'report.txt').write_text('before')
    write=files.prepare_target('report.txt','write')
    read=files.prepare_target('report.txt','read')
    write.update(args={'path':'report.txt'},path_field='path',tool_name='write')
    files.write(write,'after',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
    assert files.read(read,None,None,settings=ReadSettings(),control=None,progress=None) == 'after'


def test_write_pins_parent_through_commit_and_does_not_follow_replacement(tmp_path,monkeypatch):
    root,state,files=workspace(tmp_path)
    parent=root/'reports';parent.mkdir();(parent/'report.txt').write_text('before')
    subject=files.prepare_target('reports/report.txt','write')
    subject.update(args={'path':'reports/report.txt'},path_field='path',tool_name='write')
    replace=os.replace;swapped=[]
    def attack(source,destination,*args,**kwargs):
        if kwargs.get('dst_dir_fd') is not None and not swapped:
            parent.rename(root/'original');parent.symlink_to(state,target_is_directory=True)
            swapped.append(True)
        return replace(source,destination,*args,**kwargs)
    monkeypatch.setattr(os,'replace',attack)
    files.write(subject,'after',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
    assert swapped==[True]
    assert (state/'report.txt').read_text()=='CONTROL SECRET'
    assert (root/'original'/'report.txt').read_text()=='after'


def test_internal_link_write_and_prepared_edit_preserve_permissions(tmp_path):
    from naiyue_test_agent.tools.settings import EditSettings
    from naiyue_test_agent.tools.edit import EditReplacement
    root,state,files=workspace(tmp_path)
    path=root/'report.txt';path.write_text('before');path.chmod(0o600)
    alias=root/'alias';alias.symlink_to('report.txt')
    write=files.prepare_target('alias','write');edit=files.prepare_target('alias','write')
    for subject in (write,edit):subject.update(args={'path':'alias'},path_field='path',tool_name='write')
    files.write(write,'middle',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
    files.edit(edit,[EditReplacement(oldText='middle',newText='after')],settings=EditSettings(),control=None,progress=None)
    assert alias.is_symlink() and path.read_text()=='after' and path.stat().st_mode&0o777==0o600


def test_missing_parent_creation_is_deferred_until_write(tmp_path):
    root,state,files=workspace(tmp_path)
    subject=files.prepare_target('new/deep/report.txt','write')
    subject.update(args={'path':'new/deep/report.txt'},path_field='path',tool_name='write')
    assert not (root/'new').exists()
    files.write(subject,'created',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
    assert (root/'new/deep/report.txt').read_text()=='created'


def test_special_file_and_root_prefix_collision_are_rejected(tmp_path):
    root,state,files=workspace(tmp_path)
    os.mkfifo(root/'pipe')
    with pytest.raises(OSError):files.read_resource(str(root/'pipe'),100)
    collision=root.with_name('workspace-other');collision.mkdir();(collision/'report.txt').write_text('outside')
    with pytest.raises(ResourceBoundaryError):files.read_resource(str(collision/'report.txt'),100)


def test_native_find_accepts_image_workspace_alias(tmp_path):
    import asyncio
    from naiyue_test_agent.envd.dispatch import FileDispatcher
    from naiyue_test_agent.tools.permission_rules import PermissionSettings
    from naiyue_test_agent.tools.settings import FindSettings
    physical=tmp_path.resolve()/'work'/'workspace';physical.mkdir(parents=True)
    (physical/'result.txt').write_text('needle')
    alias=tmp_path.resolve()/'workspace';alias.symlink_to(physical,target_is_directory=True)
    environment=EnvironmentRef('B','sandbox','P',str(alias),'linux')
    dispatcher=FileDispatcher(environment,'S');files=dispatcher.files
    subject=files.prepare_target('.','find')
    subject.update(args={'path':'.'},path_field='path',tool_name='find',execution_environment=environment.public())
    permit=dict(session_id='S',agent_path='root',call_id='C',subject=subject)
    runtime=dispatcher.runtime({'access':dict(permit=permit,permission_rules=PermissionSettings().model_dump(mode='json'),
        session_allow=[],permission_mode='NoAsk')})
    result=asyncio.run(files.find(subject,'*.txt',10,runtime,settings=FindSettings(),process_settings=None,progress=None,timeout=10))
    assert result=='result.txt'
