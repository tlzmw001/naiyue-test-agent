"""Explicit attachments preserve ordinary bytes, independently of source/Git policies."""
import hashlib
import pytest
from naiyue_test_agent.contracts.cloud_inputs import InputLimits,Selection
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_files.archive import validate_package


def test_attachment_roundtrip_includes_hidden_files_and_opaque_content(tmp_path):
    source=tmp_path/'source';source.mkdir()
    content={'.env':b'USER_EXPLICIT_SELECTION=1', '.git/config':b'ordinary bytes',
        'key.pem':b'-----BEGIN PRIVATE KEY-----\nnot-a-real-key', 'archive.zip':b'opaque-archive',
        'script.sh':b'#!/bin/sh\r\necho hello\r\n'}
    for name,value in content.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
    limits=InputLimits();destination=tmp_path/'expanded';destination.mkdir()
    captured=capture(source,{'schema':3,'paths':list(content),'executables':['script.sh']},tmp_path/'batch',limits,codec='tar')
    manifest,selection=validate_package(captured.directory/'archive',captured.codec,limits,destination=destination,
        expected_id=captured.input_id,expected_archive_sha256=captured.archive_sha256,expected_size=captured.size)
    assert manifest.schema_version==3 and selection.schema_version==3
    assert {entry.path:entry.sha256 for entry in manifest.entries}=={name:hashlib.sha256(value).hexdigest() for name,value in content.items()}
    assert {name:(destination/name).read_bytes() for name in content}==content


def test_attachment_budget_defaults_support_twenty_gib():
    limits=InputLimits()
    assert limits.max_file_bytes>=20*1024**3 and limits.max_input_bytes>=20*1024**3
    assert limits.max_archive_bytes>limits.max_input_bytes
    assert limits.max_cache_bytes>=2*limits.max_archive_bytes
    assert limits.chunk_bytes*limits.max_segments>=limits.max_archive_bytes


@pytest.mark.parametrize('path',['../escape','/absolute','folder/../escape','folder\\escape','CON','name:stream'])
def test_explicit_files_still_reject_unsafe_or_nonportable_paths(path):
    with pytest.raises(ValueError):Selection.model_validate({'schema':3,'paths':[path]})


def test_attachments_reject_symlinks_instead_of_following_them(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'actual').write_text('content');(source/'link').symlink_to('actual')
    with pytest.raises(CloudError,match='attachment_regular_files_only'):
        capture(source,{'schema':3,'paths':['actual','link']},tmp_path/'batch',InputLimits())


def test_attachment_extraction_rejects_symlink_parent_without_writing_outside(tmp_path):
    source=tmp_path/'source';(source/'folder').mkdir(parents=True);(source/'folder'/'data').write_text('selected')
    limits=InputLimits();captured=capture(source,{'schema':3,'paths':['folder/data']},tmp_path/'batch',limits)
    destination=tmp_path/'expanded';destination.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (destination/'folder').symlink_to(outside,target_is_directory=True)
    with pytest.raises(CloudError):validate_package(captured.directory/'archive',captured.codec,limits,destination=destination)
    assert list(outside.iterdir())==[]


def test_file_browser_and_folder_selection_include_hidden_without_git(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import tree,select_files
    root=tmp_path/'source';(root/'.hidden').mkdir(parents=True);(root/'.hidden'/'.env').write_text('chosen')
    (root/' visible.txt').write_text('keep name')
    listing=tree(root,InputLimits(git_executable='absent'),ordinary=True)
    assert {entry['name'] for entry in listing['entries']}=={'.hidden',' visible.txt'}
    selection=select_files(root,InputLimits(),['.hidden',' visible.txt'])
    assert selection.schema_version==3 and selection.paths==[' visible.txt','.hidden/.env']
    assert listing['excluded_count']==0
