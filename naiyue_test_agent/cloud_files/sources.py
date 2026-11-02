"""Freeze Git's file/ignore metadata without Git commits, checkout or source writes."""
import os
from pathlib import Path
import stat

from naiyue_test_agent.contracts.cloud_inputs import Selection,input_path,distinct_paths,check_input_content
from naiyue_test_agent.contracts.cloud_sources import SourcePlan,SourcePolicy,SourceRule,SourceReceipt
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory
from .git_source import SourceGit
from .source_facts import fact,directory,verify


def select_git(root,limits,*,include=(),exclude=()):
    root=Path(root).absolute();check_store_directory(root)
    include=distinct_paths(list(include));exclude=distinct_paths([p.rstrip('/') for p in exclude])
    def excluded(path):return any(path==p or path.startswith(p+'/') for p in exclude)
    git=SourceGit(root,limits)
    roots=git.run(['rev-parse','--show-toplevel','--absolute-git-dir','--git-common-dir']).decode().splitlines()
    if len(roots)!=3 or Path(roots[0]).absolute()!=root:raise CloudError('input_git_root_required')
    gitdir=Path(roots[1]);common=Path(roots[2]);common=common if common.is_absolute() else root/common
    facts={};directories=[];rules=[];count=0
    def remember(path):
        value,raw=fact(path,limits.max_metadata_bytes);facts[str(value.path)]=value;return raw
    def rule(path,name,kind):
        raw=remember(path)
        if raw:
            check_input_content(b'',raw,first=True)
            rules.append(SourceRule(name=name,kind=kind,content=raw.decode('utf-8')))
    # Freeze metadata before inventory; changes during Git reads are rejected below.
    for name in ('index','HEAD','config','config.worktree','commondir'):remember(gitdir/name)
    for name in ('config','packed-refs'):remember(common/name)
    headraw=remember(gitdir/'HEAD').decode().strip()
    if headraw.startswith('ref: '):remember(common/headraw[5:])
    pending=[root]
    while pending:
        parent=pending.pop();directories.append(directory(parent))
        with os.scandir(parent) as listing:
            for item in sorted(listing,key=lambda v:v.name):
                count+=1
                if count>limits.max_discovery_entries:raise CloudError('input_discovery_budget')
                path=Path(item.path);relative=path.relative_to(root).as_posix()
                try:input_path(relative)
                except ValueError:continue
                if excluded(relative):continue
                if item.is_dir(follow_symlinks=False):
                    if not (path/'.git').exists():pending.append(path)
                elif item.name in ('.gitignore','.gitattributes'):
                    rule(path,'worktree/'+relative,'ignore' if item.name=='.gitignore' else 'attributes')
    rule(common/'info/exclude','repository/info/exclude','ignore')
    rule(common/'info/attributes','repository/info/attributes','attributes')
    config={}
    raw=git.run(['config','--show-origin','-z','--list'])
    tokens=raw.split(b'\0');tokens=tokens[:-1] if tokens[-1:]==[b''] else tokens
    if len(tokens)%2:raise CloudError('input_git_metadata_invalid')
    for i in range(0,len(tokens),2):
        origin=tokens[i].decode();key,_,value=tokens[i+1].decode().partition('\n')
        if key in ('core.autocrlf','core.eol','core.filemode','core.symlinks','core.excludesfile','core.attributesfile'):
            config[key]=value
        if origin.startswith('file:'):
            path=Path(origin[5:]);remember(path if path.is_absolute() else root/path)
            if key=='include.path' or key.startswith('includeif.') and key.endswith('.path'):
                included=Path(value).expanduser();origin_path=path if path.is_absolute() else root/path
                remember(included if included.is_absolute() else origin_path.parent/included)
    home=Path(os.environ.get('HOME') or os.environ.get('USERPROFILE') or '~').expanduser()
    xdg=Path(os.environ.get('XDG_CONFIG_HOME',str(home/'.config')))
    for key,default,kind in (('core.excludesfile',xdg/'git/ignore','ignore'),('core.attributesfile',xdg/'git/attributes','attributes')):
        path=Path(config.get(key,str(default))).expanduser()
        if not path.is_absolute():path=root/path
        # Missing standard config directories are a recorded absence, not an IO fallback.
        rule(path,'global/'+kind,kind)
    for path in (home/'.gitconfig',xdg/'git/config'):remember(path)
    listing=git.run(['ls-files','-z','--cached','--others','--exclude-standard'])
    staged=git.run(['ls-files','--stage','-z']);modes={};submodules=[]
    for value in staged.split(b'\0'):
        if not value:continue
        meta,name=value.split(b'\t',1);mode,oid,stage=meta.decode().split();path=name.decode('utf-8')
        if stage!='0':raise CloudError('input_git_conflict')
        if mode=='160000':submodules.append(path);continue
        if mode not in ('100644','100755','120000'):raise CloudError('input_git_type')
        modes[path]=mode
    paths=[];deleted=[];omitted=[]
    for path in sorted(set(p.decode('utf-8') for p in listing.split(b'\0') if p)|set(include)):
        try:input_path(path)
        except ValueError:
            if path in include:raise CloudError('input_path_invalid') from None
            omitted.append(path);continue
        in_submodule=any(path==p or path.startswith(p+'/') for p in submodules)
        if in_submodule and path in include:raise CloudError('input_submodule_unsupported')
        if in_submodule or excluded(path):omitted.append(path);continue
        if not os.path.lexists(root/path):deleted.append(path);continue
        paths.append(path)
    if not paths or len(paths)>limits.max_files:raise CloudError('input_budget')
    attributes={}
    attrs=git.run(['check-attr','-z','--stdin','text','eol','working-tree-encoding','filter'],data=b''.join(p.encode()+b'\0' for p in paths))
    parts=attrs.split(b'\0')[:-1]
    if len(parts)%3:raise CloudError('input_git_metadata_invalid')
    for i in range(0,len(parts),3):attributes.setdefault(parts[i].decode(),{})[parts[i+1].decode()]=parts[i+2].decode()
    head=git.run(['rev-parse','--verify','HEAD'],optional=True).decode().strip() or None
    source_modes={p:(root/p).lstat().st_mode for p in paths if p not in modes}
    selection=Selection(schema=2,paths=paths,executables=[p for p in paths if modes.get(p)=='100755'
        or p in source_modes and os.name!='nt' and source_modes[p] & stat.S_IXUSR],
        policy=SourcePolicy(kind='git',include=include,exclude=exclude,rules=sorted(rules,key=lambda r:r.name)))
    receipt=SourceReceipt(root=root,head=head,config=config,attributes=attributes,deleted=deleted,
        submodules=submodules,excluded=omitted,facts=list(facts.values()),directories=directories,git_commands=git.commands,
        source_modes=source_modes)
    if len(selection.canonical)>limits.max_metadata_bytes or len(receipt.model_dump_json().encode())>limits.max_metadata_bytes*4:
        raise CloudError('input_metadata_budget')
    verify(root,receipt,limits)
    return SourcePlan(selection=selection.model_dump(by_alias=True,exclude_none=True),receipt=receipt)
