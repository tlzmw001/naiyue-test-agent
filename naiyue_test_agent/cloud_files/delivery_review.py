"""Content preflight, approved local snapshots, and per-target result baselines."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat
from naiyue_test_agent.contracts.cloud_inputs import InputEntry
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .delivery_apply import matches
from .delivery_paths import parent,lstat,directory_identity
from .paths import source_handle,identity


def observed_entry(root,path,maximum,*,ordinary=False):
    try:
        with parent(root,path,ordinary=ordinary) as (fd,directory,name):
            try:info=lstat(fd,directory,name)
            except FileNotFoundError:return None
            if ordinary and (not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400):raise CloudError('cloud_delivery_path_unsafe')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise CloudError('cloud_delivery_path_unsafe')
            if info.st_size>maximum:raise CloudError('cloud_delivery_capacity',413)
            with source_handle(Path(root),path,identity(info)) as (stream,opened):
                sha=hashlib.sha256();size=0;target=None
                if isinstance(stream,bytes):
                    sha.update(stream);size=len(stream);target=stream.decode()
                else:
                    while chunk:=stream.read(262144):
                        size+=len(chunk)
                        if size>maximum:raise CloudError('cloud_delivery_capacity',413)
                        sha.update(chunk)
                    if identity(os.fstat(stream.fileno()))!=identity(opened):raise CloudError('cloud_delivery_preflight_changed')
            return InputEntry(path=path,type='symlink' if target is not None else 'file',size=size,sha256=sha.hexdigest(),
                mode=0o777 if target is not None else 0o755 if info.st_mode&0o111 else 0o644,target=target)
    except FileNotFoundError:return None


def review(root,verified,maximum):
    observed={};conflicts=[]
    for change in verified.changes:
        entry=observed_entry(root,change['path'],maximum,ordinary=verified.ordinary)
        observed[change['path']]=entry.model_dump() if entry else None
        if not matches(root,change['path'],change['old'],ordinary=verified.ordinary) and not matches(root,change['path'],change['new'],ordinary=verified.ordinary):
            conflicts.append(change['path'])
    return observed,conflicts


def approved(root,verified,observed,force,*,continuing=False):
    changes=[]
    if set(observed)!={c['path'] for c in verified.changes}:raise CloudError('cloud_delivery_not_prepared')
    for change in verified.changes:
        raw=observed[change['path']];old=InputEntry.model_validate(raw) if raw else None
        if not continuing and not matches(root,change['path'],old,ordinary=verified.ordinary):raise CloudError('cloud_delivery_preflight_changed')
        if not force and not matches(root,change['path'],change['old'],ordinary=verified.ordinary) and not matches(root,change['path'],change['new'],ordinary=verified.ordinary):raise CloudError('cloud_delivery_conflict')
        changes.append(change|dict(old=old))
    return replace(verified,changes=changes)


def rebased(verified,entries):
    old={e.path:e for e in (InputEntry.model_validate(raw) for raw in entries)}
    new={e.path:InputEntry.model_validate(e.model_dump()) for e in verified.output_manifest.entries}
    changes=[dict(path=p,old=old.get(p),new=new.get(p)) for p in sorted(old.keys()|new.keys()) if old.get(p)!=new.get(p)]
    return replace(verified,changes=changes)


def directory_scope(root):
    # Compare filesystem identity, including ancestors, on case-insensitive volumes.
    # Common ancestors alone do not conflict: one target root must contain the other.
    return [directory_identity(path) for path in (root,*root.parents)]
