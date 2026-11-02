"""Private source evidence: bounded metadata hashes and directory membership."""
import hashlib
import os
from pathlib import Path

from naiyue_test_agent.contracts.cloud_sources import SourceFact,SourceDirectory
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_readonly,check_store_directory
from .paths import identity


def fact(path,limit):
    path=Path(path).absolute();resolved=path.resolve(strict=False)
    parent=resolved.parent
    while not parent.exists():parent=parent.parent
    check_store_directory(parent)
    try:fd=open_store_readonly(resolved)
    except FileNotFoundError:return SourceFact(path=path,resolved=resolved,identity=None,sha256=None),b''
    with os.fdopen(fd,'rb') as stream:
        before=identity(os.fstat(stream.fileno()));raw=stream.read(limit+1)
        if len(raw)>limit:raise CloudError('input_metadata_budget')
        if identity(os.fstat(stream.fileno()))!=before:raise CloudError('source_plan_changed')
    return SourceFact(path=path,resolved=resolved,identity=list(before),sha256=hashlib.sha256(raw).hexdigest()),raw


def directory(path):
    check_store_directory(path);info=path.lstat()
    members='\0'.join(sorted(os.listdir(path))).encode('utf-8')
    return SourceDirectory(path=path,identity=[info.st_dev,info.st_ino],members_sha256=hashlib.sha256(members).hexdigest())


def verify(root,receipt,limits):
    if Path(root).absolute()!=receipt.root:raise CloudError('source_plan_changed')
    try:
        for value in receipt.facts:
            if fact(value.path,limits.max_metadata_bytes)[0]!=value:raise CloudError('source_plan_changed')
        for value in receipt.directories:
            if directory(value.path)!=value:raise CloudError('source_plan_changed')
        for name,mode in receipt.source_modes.items():
            if (Path(root)/name).lstat().st_mode!=mode:raise CloudError('source_plan_changed')
    except (OSError,ValueError):raise CloudError('source_plan_changed') from None
