"""One input installer shared by S03 bootstrap and the later Agent Runner."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil

from naiyue_test_agent.cloud_files.archive import strict_json,validate_package
from naiyue_test_agent.cloud_files.paths import identity,source_handle
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory,open_store_file,open_store_readonly,sync_store_directory

logger=logging.getLogger(__name__)


def _write(path,value):
    temporary=path.with_name(path.name+'.next');fd=open_store_file(temporary)
    with os.fdopen(fd,'w') as stream:
        stream.write(json.dumps(value,sort_keys=True));stream.truncate();stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path);sync_store_directory(path.parent)


def _verify_project(project,manifest,limits):
    expected={e.path for e in manifest.entries};actual=set()
    for base,dirs,files in os.walk(project,followlinks=False):
        for name in dirs[:]:
            path=Path(base)/name
            if path.is_symlink():dirs.remove(name);files.append(name)
        actual.update((Path(base)/n).relative_to(project).as_posix() for n in files)
    if expected!=actual:raise CloudError('input_install_incomplete')
    for entry in manifest.entries:
        target=project/entry.path;before=identity(target.lstat());sha=hashlib.sha256();size=0
        with source_handle(project,entry.path,before) as (stream,info):
            if entry.type=='symlink':
                if info is not None or stream.decode()!=entry.target:raise CloudError('input_install_incomplete')
                sha.update(stream);size=len(stream)
            else:
                if info is None or before[2]&0o777!=entry.mode:raise CloudError('input_install_incomplete')
                while chunk:=stream.read(limits.io_bytes):sha.update(chunk);size+=len(chunk)
            if size!=entry.size or sha.hexdigest()!=entry.sha256:raise CloudError('input_install_incomplete')


def install_input(package,workspace,state,input_id,limits):
    package,workspace,state=map(Path,(package,workspace,state))
    for directory in (package,workspace,state):check_store_directory(directory)
    marker=state/'input-install.json';project=workspace/'project';staging=workspace/'.nyt-input-staging'
    try:
        with os.fdopen(open_store_readonly(package/'input.json'),'rb') as stream:
            from naiyue_test_agent.contracts.cloud_inputs import InputPackage
            committed=InputPackage.model_validate_json(stream.read(limits.max_metadata_bytes*3+1))
        if committed.input_id!=input_id:raise CloudError('input_install_identity')
        if marker.exists() or marker.is_symlink():
            with os.fdopen(open_store_readonly(marker),'rb') as stream:value=strict_json(stream.read(4097))
            if set(value)!={'input_id','status'} or value['input_id']!=input_id or value['status'] not in ('installing','prepared','installed'):
                raise CloudError('input_install_identity')
            if value['status']=='installed':
                check_store_directory(project)
                return value
        else:
            if any(workspace.iterdir()):raise CloudError('input_workspace_not_empty')
            value={'input_id':input_id,'status':'installing'};_write(marker,value)
        if project.exists() or project.is_symlink():
            if value['status']!='prepared' or staging.exists() or staging.is_symlink():raise CloudError('input_install_incomplete')
            check_store_directory(project);_verify_project(project,committed.manifest,limits)
        else:
            if value['status']=='prepared':
                check_store_directory(staging);_verify_project(staging,committed.manifest,limits)
                os.rename(staging,project);sync_store_directory(workspace)
                value={'input_id':input_id,'status':'installed'};_write(marker,value);return value
            if staging.exists() or staging.is_symlink():
                check_store_directory(staging)
                if not shutil.rmtree.avoids_symlink_attacks:raise CloudError('input_install_cleanup_unsupported')
                shutil.rmtree(staging)
            staging.mkdir(mode=0o700)
            manifest,selection=validate_package(package/'archive',committed.codec,limits,expected_id=input_id,destination=staging,
                expected_archive_sha256=committed.archive_sha256,expected_size=committed.size)
            if manifest!=committed.manifest or selection!=committed.selection:raise CloudError('input_install_identity')
            for base,dirs,_ in os.walk(staging,topdown=False,followlinks=False):sync_store_directory(Path(base))
            _write(marker,{'input_id':input_id,'status':'prepared'})
            os.rename(staging,project);sync_store_directory(workspace)
        value={'input_id':input_id,'status':'installed'};_write(marker,value);return value
    except (OSError,ValueError,KeyError) as error:
        logger.warning('cloud_input_install_failed error_type=%s',type(error).__name__)
        raise CloudError('input_install_failed') from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-id',required=True);parser.add_argument('--limits-json',required=True)
    args=parser.parse_args()
    try:
        from naiyue_test_agent.contracts.cloud_tasks import ContentId
        from pydantic import TypeAdapter
        ident=TypeAdapter(ContentId).validate_python(args.input_id)
        result=install_input(Path('/input'),Path('/workspace'),Path('/state'),ident,InputLimits.model_validate_json(args.limits_json))
        print(json.dumps(result));return 0
    except CloudError as error:
        print(json.dumps({'status':'failed','code':error.code}));return 1
    except ValueError:
        print(json.dumps({'status':'failed','code':'input_install_invalid_request'}));return 1


if __name__=='__main__':raise SystemExit(main())
