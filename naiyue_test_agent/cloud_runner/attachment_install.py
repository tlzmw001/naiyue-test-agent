"""Publish each verified batch once, outside any previous batch or working file."""
from contextlib import ExitStack
import logging
import os
from pathlib import Path
import shutil
from uuid import uuid4
from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,CloudError
from naiyue_test_agent.cloud_files.archive import validate_package
from naiyue_test_agent.cloud_files.delivery_paths import parent,rename_new,directory_identity
from naiyue_test_agent.storage.files import check_store_directory,sync_store_directory
from .input_install import _verify_project
from .state import read_json,write_json

logger=logging.getLogger(__name__)


def install(package,root,state,attachment_id,input_id,limits):
    TypeAdapter(UUIDText).validate_python(attachment_id)
    package,root,state=map(Path,(package,root,state))
    record=InputPackage.model_validate(read_json(package/'input.json',limits.max_metadata_bytes*3))
    if record.input_id!=input_id or record.manifest.schema_version!=3:raise CloudError('attachment_install_identity')
    relative='.nyt-input/'+attachment_id
    for path in (package,root,state):check_store_directory(path)
    journal=state/'attachment-installs';journal.mkdir(mode=0o700,exist_ok=True);check_store_directory(journal)
    marker=journal/(attachment_id+'.json');project=directory_identity(root)
    expected=dict(attachment_id=attachment_id,input_id=input_id,project=project)
    stage=None;stage_identity=None;prepared=False
    try:
        value=read_json(marker) if os.path.lexists(marker) else None
        if value is not None and any(value.get(k)!=v for k,v in expected.items()):raise CloudError('attachment_install_identity')
        with ExitStack() as stack:
            # The source is a sibling of project on the /workspace mount, not /state.
            source_fd,source_parent,_=stack.enter_context(parent(root.parent,'.nyt-attachment',ordinary=True))
            target_fd,target_parent,name=stack.enter_context(parent(root,relative,create=True,ordinary=True))
            target=root/relative;target_parent_identity=directory_identity(target_parent)
            def check():
                if directory_identity(root)!=project or directory_identity(target_parent)!=target_parent_identity:
                    raise CloudError('attachment_install_identity')
                if target_fd is not None:
                    opened=os.fstat(target_fd)
                    if target_parent_identity!={'device':opened.st_dev,'inode':opened.st_ino}:raise CloudError('attachment_install_identity')
            if value is not None:
                if os.path.lexists(target):
                    if directory_identity(target)!=value['batch']:raise CloudError('attachment_install_identity')
                    check()
                    if value['phase']!='installed':
                        _verify_project(target,record.manifest,limits)
                        write_json(marker,value|{'phase':'installed'})
                    return relative
                if value['phase']=='installed':raise CloudError('attachment_install_identity')
                stage_name=value['staging'];stage=root.parent/stage_name
                if directory_identity(stage)!=value['batch']:raise CloudError('attachment_install_identity')
                stage_identity=value['batch'];prepared=True
                _verify_project(stage,record.manifest,limits)
            else:
                if os.path.lexists(target):raise CloudError('attachment_install_identity')
                required=sum(entry.size for entry in record.manifest.entries)
                if required>limits.max_input_bytes or shutil.disk_usage(root.parent).free<required+limits.min_free_bytes:
                    raise CloudError('attachment_disk_capacity',507)
                stage_name='.nyt-attachment-'+uuid4().hex;stage=root.parent/stage_name
                if source_fd is not None:os.mkdir(stage_name,0o700,dir_fd=source_fd)
                else:stage.mkdir(mode=0o700)
                stage_identity=directory_identity(stage)
                manifest,selection=validate_package(package/'archive',record.codec,limits,expected_id=input_id,
                    destination=stage,expected_archive_sha256=record.archive_sha256,expected_size=record.size)
                if manifest!=record.manifest or selection!=record.selection:raise CloudError('attachment_install_identity')
                _verify_project(stage,record.manifest,limits)
                if directory_identity(stage)!=stage_identity:raise CloudError('attachment_install_identity')
                for base,_,_ in os.walk(stage,topdown=False,followlinks=False):sync_store_directory(Path(base))
                value=expected|dict(phase='prepared',staging=stage_name,batch=stage_identity)
                write_json(marker,value);prepared=True
            check()
            if directory_identity(stage)!=stage_identity:raise CloudError('attachment_install_identity')
            rename_new(stage_name if source_fd is not None else stage,name if target_fd is not None else target,
                src_dir_fd=source_fd,dst_dir_fd=target_fd)
            if source_fd is not None:os.fsync(source_fd)
            if target_fd is not None:os.fsync(target_fd)
            check()
            if directory_identity(target)!=stage_identity:raise CloudError('attachment_install_identity')
            write_json(marker,value|{'phase':'published'})
            _verify_project(target,record.manifest,limits)
            write_json(marker,value|{'phase':'installed'})
            return relative
    except (OSError,ValueError,KeyError) as error:
        logger.warning('cloud_attachment_install_failed error_type=%s',type(error).__name__)
        raise CloudError('attachment_install_failed') from None
    finally:
        if stage is not None and not prepared and os.path.lexists(stage):
            try:
                if directory_identity(stage)!=stage_identity:raise CloudError('attachment_install_identity')
                if not shutil.rmtree.avoids_symlink_attacks:raise CloudError('attachment_cleanup_unsupported')
                with parent(root.parent,stage.name,ordinary=True) as (fd,directory,name):
                    if directory_identity(stage)!=stage_identity:raise CloudError('attachment_install_identity')
                    shutil.rmtree(name,dir_fd=fd)
            except (OSError,CloudError) as error:
                logger.warning('cloud_attachment_staging_cleanup_failed error_type=%s',type(error).__name__)
