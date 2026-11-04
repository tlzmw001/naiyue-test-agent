"""Bounded project-local selection; previews never authorize or freeze source bytes."""
import logging
import os
from pathlib import Path
import stat
from naiyue_test_agent.contracts.cloud_inputs import input_path,Selection
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory
from .git_source import SourceGit
from .delivery_paths import parent as pinned_parent,lstat

logger=logging.getLogger(__name__)


def paths_normalized(paths):
    try:
        names=sorted(set(input_path(p.strip().replace('\\','/').rstrip('/')) for p in paths))
        return [p for p in names if not any(p.startswith(q+'/') for q in names if q!=p)]
    except ValueError:raise CloudError('input_path_invalid',422) from None


def require_git(root,limits):
    raw=SourceGit(root,limits).run(['rev-parse','--show-toplevel'],optional=True).decode().strip()
    if not raw:
        if (Path(root)/'.git').exists():raise CloudError('input_git_failed')
        raise CloudError('input_git_required',422)
    if Path(raw).resolve()!=Path(root).resolve():raise CloudError('input_git_root_required',422)


def capabilities(root,limits):
    check_store_directory(root)
    try:require_git(root,limits)
    except CloudError as error:return dict(git=False,git_error=error.code)
    return dict(git=True,git_error=None)


def _entries(root,path,limits):
    rows=[];ignored=0
    with pinned_parent(root,(Path(path)/'scope-entry').as_posix()) as (fd,directory,_), os.scandir(fd if fd is not None else directory) as listing:
        for number,item in enumerate(listing):
            if number>=limits.max_discovery_entries:raise CloudError('input_discovery_budget')
            relative=(Path(path)/item.name).as_posix()
            try:input_path(relative)
            except ValueError:ignored+=1;continue
            info=lstat(fd,directory,item.name)
            kind='directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'symlink' if stat.S_ISLNK(info.st_mode) else None
            if kind is None:ignored+=1;continue
            rows.append(dict(path=relative,name=item.name,kind=kind,size=info.st_size if kind!='directory' else 0))
    return sorted(rows,key=lambda r:(r['kind']!='directory',r['name'])),ignored


def tree(root,limits,*,path='',offset=0,limit=200):
    root=Path(root).absolute()
    if path:path=paths_normalized([path])[0]
    try:rows,ignored=_entries(root,path,limits)
    except OSError as error:
        logger.warning('cloud_source_tree_failed error_type=%s',type(error).__name__)
        raise CloudError('input_source_unavailable') from None
    return dict(**capabilities(root,limits),path=path,entries=rows[offset:offset+limit],
        next_offset=offset+limit if offset+limit<len(rows) else None,excluded_count=ignored,limits=limits.model_dump())


def plan_scope(root,limits,*,mode,paths=(),include=(),exclude=()):
    root=Path(root).absolute();check_store_directory(root);scope=paths_normalized(paths);exclude=paths_normalized(exclude)
    if mode=='git':
        from .sources import select_git
        plan=select_git(root,limits,include=include,exclude=exclude,scope=scope)
        return Selection.model_validate(plan.selection),plan.receipt
    if not scope:raise CloudError('input_selection_required',422)
    pending=list(scope);selected=[];executables=[];seen=0
    try:
        while pending:
            path=pending.pop();seen+=1
            if any(path==p or path.startswith(p+'/') for p in exclude):continue
            if seen>limits.max_discovery_entries:raise CloudError('input_discovery_budget')
            with pinned_parent(root,path) as (fd,directory,name):info=lstat(fd,directory,name)
            if stat.S_ISDIR(info.st_mode):
                rows,_=_entries(root,path,limits);pending.extend(r['path'] for r in rows)
            elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                selected.append(path)
                if os.name!='nt' and info.st_mode & stat.S_IXUSR:executables.append(path)
                if len(selected)>limits.max_files:raise CloudError('input_file_count_budget')
            else:raise CloudError('input_type_unsupported')
    except OSError as error:
        logger.warning('cloud_source_selection_failed error_type=%s',type(error).__name__)
        raise CloudError('input_source_unavailable') from None
    if not selected:raise CloudError('input_selection_required',422)
    return Selection(paths=selected,executables=executables),None


def preview_scope(root,limits,**kwargs):
    selected,_=plan_scope(root,limits,**kwargs);total=0;issues=[]
    try:
        for path in selected.paths:
            with pinned_parent(root,path) as (fd,directory,name):size=lstat(fd,directory,name).st_size
            total+=size
            if size>limits.max_file_bytes:issues.append(dict(code='input_file_budget',path=path,actual=size,limit=limits.max_file_bytes))
    except OSError as error:
        logger.warning('cloud_source_preview_failed error_type=%s',type(error).__name__)
        raise CloudError('input_source_unavailable') from None
    if total>limits.max_input_bytes:issues.append(dict(code='input_byte_budget',actual=total,limit=limits.max_input_bytes))
    return dict(file_count=len(selected.paths),total_bytes=total,issues=issues[:100],limits=limits.model_dump())
