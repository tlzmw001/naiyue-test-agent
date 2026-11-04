"""Bounded FD-anchored raw trees shared by backup, restore and managed cleanup."""
import hashlib,os,stat,sys
from pathlib import Path,PurePosixPath
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly,sync_store_directory


def facts(value):return (value.st_dev,value.st_ino,value.st_mode,value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def children(fd):
    with os.scandir(fd) as entries:
        return {item.name:facts(item.stat(follow_symlinks=False)) for item in entries}


def _copy(source,destination,prefix,records,budget,max_bytes,max_files):
    before=facts(os.fstat(source));names=children(source)
    for name,expected in sorted(names.items()):
        budget['entries']+=1
        if budget['entries']>max_files:raise CloudError('upgrade_backup_budget')
        mode=expected[2]
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or stat.S_ISREG(mode) and expected[3]!=1:
            raise CloudError('upgrade_backup_unsafe_file')
        flags=os.O_RDONLY|os.O_NOFOLLOW|getattr(os,'O_NONBLOCK',0)
        if stat.S_ISDIR(mode):flags|=os.O_DIRECTORY
        fd=os.open(name,flags,dir_fd=source)
        try:
            if facts(os.fstat(fd))!=expected:raise CloudError('upgrade_backup_source_changed')
            path=prefix+'/'+name
            if len(path.encode('utf-8'))>4096 or path.count('/')>64:raise CloudError('upgrade_backup_budget')
            if stat.S_ISDIR(mode):
                os.mkdir(name,mode=0o700,dir_fd=destination)
                target=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=destination)
                try:_copy(fd,target,path,records,budget,max_bytes,max_files)
                finally:os.close(target)
                records.append(dict(path=path,kind='directory',mode=stat.S_IMODE(mode),source_facts=expected))
            else:
                out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=destination)
                sha=hashlib.sha256();size=0
                try:
                    while data:=os.read(fd,256*1024):
                        size+=len(data);budget['bytes']+=len(data)
                        if budget['bytes']>max_bytes:raise CloudError('upgrade_backup_budget')
                        sha.update(data);view=memoryview(data)
                        while view:
                            written=os.write(out,view)
                            if not written:raise OSError('backup_write_no_progress')
                            view=view[written:]
                    os.fsync(out)
                finally:os.close(out)
                if size!=expected[4]:raise CloudError('upgrade_backup_source_changed')
                budget['files']+=1
                records.append(dict(path=path,kind='file',mode=stat.S_IMODE(mode),size=size,sha256=sha.hexdigest(),source_facts=expected))
            if facts(os.fstat(fd))!=expected or facts(os.stat(name,dir_fd=source,follow_symlinks=False))!=expected:
                raise CloudError('upgrade_backup_source_changed')
        finally:os.close(fd)
    if facts(os.fstat(source))!=before or children(source)!=names:raise CloudError('upgrade_backup_source_changed')
    os.fsync(destination)


def _verify_source(fd,prefix,expected,seen):
    before=children(fd)
    for name,value in before.items():
        path=prefix+'/'+name
        if expected.get(path)!=value:raise CloudError('upgrade_backup_source_changed')
        seen.add(path)
        if stat.S_ISDIR(value[2]):
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:_verify_source(child,path,expected,seen)
            finally:os.close(child)
    if children(fd)!=before:raise CloudError('upgrade_backup_source_changed')


def copy_roots(roots,*,max_bytes,max_files):
    records=[];budget=dict(entries=0,files=0,bytes=0);opened=[]
    try:
        for name,source,destination in roots:
            check_store_directory(source);check_store_directory(destination)
            fd=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            opened.append((Path(source),fd,facts(os.fstat(fd))))
            target=os.open(destination,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:_copy(fd,target,name,records,budget,max_bytes,max_files)
            finally:os.close(target)
        expected={item['path']:item.pop('source_facts') for item in records};seen=set()
        for (name,_,_), (path,fd,before) in zip(roots,opened):
            if facts(os.fstat(fd))!=before or facts(path.lstat())!=before:raise CloudError('upgrade_backup_source_changed')
            _verify_source(fd,name,expected,seen)
        if seen!=set(expected):raise CloudError('upgrade_backup_source_changed')
        return records
    finally:
        for _,fd,_ in opened:os.close(fd)


def verify_tree(roots,entries,*,max_bytes,max_files,verify_modes=False):
    if len(entries)>max_files:raise CloudError('backup_invalid')
    expected=set();size=0
    for item in entries:
        path=PurePosixPath(item['path'])
        if path.is_absolute() or len(path.parts)<2 or '..' in path.parts or path.parts[0] not in roots or path.as_posix() in expected:
            raise CloudError('backup_invalid')
        expected.add(path.as_posix());target=roots[path.parts[0]].joinpath(*path.parts[1:])
        if item['kind']=='directory':
            check_store_directory(target)
            if verify_modes and stat.S_IMODE(target.lstat().st_mode)!=item.get('mode'):raise CloudError('backup_invalid')
        elif item['kind']=='file':
            check_store_directory(target.parent);sha=hashlib.sha256();amount=0
            with os.fdopen(open_store_readonly(target),'rb') as stream:
                before=facts(os.fstat(stream.fileno()))
                if verify_modes and stat.S_IMODE(before[2])!=item.get('mode'):raise CloudError('backup_invalid')
                while data:=stream.read(256*1024):
                    size+=len(data);amount+=len(data);sha.update(data)
                    if size>max_bytes:raise CloudError('backup_invalid')
                if facts(os.fstat(stream.fileno()))!=before:raise CloudError('backup_invalid')
            if amount!=item['size'] or sha.hexdigest()!=item['sha256']:raise CloudError('backup_invalid')
        else:raise CloudError('backup_invalid')
    actual=set()
    for name,root in roots.items():
        check_store_directory(root)
        for p in root.rglob('*'):
            actual.add(name+'/'+p.relative_to(root).as_posix())
            if len(actual)>max_files:raise CloudError('backup_invalid')
    if actual!=expected:raise CloudError('backup_invalid')


def restore_modes(roots,entries):
    for item in sorted(entries,key=lambda v:v['path'].count('/'),reverse=True):
        parts=PurePosixPath(item['path']).parts;path=roots[parts[0]].joinpath(*parts[1:])
        mode=item['mode']
        if type(mode) is not int or mode&~0o777:raise CloudError('backup_invalid_mode')
        check_store_directory(path.parent)
        fd=(os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW) if item['kind']=='directory' else open_store_readonly(path))
        try:os.fchmod(fd,mode)
        finally:os.close(fd)


def describe_tree(roots,*,max_bytes,max_files):
    records=[];total=0
    for prefix,root in roots.items():
        check_store_directory(root)
        for path in sorted(root.rglob('*')):
            if len(records)>=max_files:raise CloudError('upgrade_backup_budget')
            check_store_directory(path.parent);info=path.lstat();name=prefix+'/'+path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                check_store_directory(path);records.append(dict(path=name,kind='directory',mode=stat.S_IMODE(info.st_mode)))
            else:
                sha=hashlib.sha256();size=0
                with os.fdopen(open_store_readonly(path),'rb') as stream:
                    before=facts(os.fstat(stream.fileno()))
                    while data:=stream.read(256*1024):
                        total+=len(data);size+=len(data);sha.update(data)
                        if total>max_bytes:raise CloudError('upgrade_backup_budget')
                    if facts(os.fstat(stream.fileno()))!=before or facts(path.lstat())!=before:raise CloudError('upgrade_backup_source_changed')
                records.append(dict(path=name,kind='file',mode=stat.S_IMODE(info.st_mode),size=size,sha256=sha.hexdigest()))
    return records


def tree_inventory(path,*,max_files):
    """GC proves names and inode/stat identity without reading large file bodies."""
    path=Path(path);check_store_directory(path);before=path.lstat();result=[]
    def visit(fd,prefix,mount,depth):
        if depth>64 or _mount(fd)!=mount:raise CloudError('managed_tree_mount_unknown')
        names=children(fd)
        for name,value in sorted(names.items()):
            if len(result)>=max_files:raise CloudError('managed_tree_budget')
            relative=prefix+name;mode=value[2]
            if stat.S_ISDIR(mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    if facts(os.fstat(child))!=value:raise CloudError('managed_tree_changed')
                    result.append(dict(path=relative,kind='directory',identity=[value[0],value[1],value[2]]))
                    visit(child,relative+'/',mount,depth+1)
                finally:os.close(child)
            elif stat.S_ISREG(mode) and value[3]==1:
                result.append(dict(path=relative,kind='file',identity=list(value),size=value[4]))
            else:raise CloudError('managed_tree_unsafe')
        if children(fd)!=names:raise CloudError('managed_tree_changed')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:visit(fd,'',_mount(fd),0)
    finally:os.close(fd)
    after=path.lstat()
    if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise CloudError('managed_tree_changed')
    return result


def _mount(fd):
    if sys.platform!='linux':return os.fstat(fd).st_dev
    with open('/proc/self/fdinfo/'+str(fd),'rb') as stream:raw=stream.read(4097)
    if len(raw)<=4096:
        for line in raw.splitlines():
            if line.startswith(b'mnt_id:'):return int(line.split(b':',1)[1])
    raise CloudError('managed_tree_mount_unknown')


def remove_tree(path,*,max_files,expected=None,expected_inventory=None):
    path=Path(path);check_store_directory(path.parent);base=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    count=0;allowed={v['path']:v for v in expected_inventory} if expected_inventory is not None else None
    def confirmed(relative,info):
        if not relative:
            if expected is not None and [info.st_dev,info.st_ino]!=expected:raise CloudError('managed_tree_changed')
            return
        if allowed is None:return
        value=allowed.get(relative);kind='directory' if stat.S_ISDIR(info.st_mode) else 'file'
        identity=list(facts(info)[:3]) if kind=='directory' else list(facts(info))
        if value is None or value['kind']!=kind or value['identity']!=identity:raise CloudError('managed_tree_changed')
    def remove(parent,name,mount,depth,relative):
        nonlocal count
        count+=1
        if count>max_files+1 or depth>64:raise CloudError('managed_tree_budget')
        try:before=os.stat(name,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:return
        if not stat.S_ISDIR(before.st_mode) and not (stat.S_ISREG(before.st_mode) and before.st_nlink==1):raise CloudError('managed_tree_unsafe')
        confirmed(relative,before)
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_DIRECTORY if stat.S_ISDIR(before.st_mode) else 0),dir_fd=parent)
        try:
            if facts(os.fstat(fd))!=facts(before) or _mount(fd)!=mount:raise CloudError('managed_tree_changed')
            confirmed(relative,os.fstat(fd))
            if stat.S_ISDIR(before.st_mode):
                with os.scandir(fd) as items:names=sorted(item.name for item in items)
                for child in names:confirmed((relative+'/' if relative else '')+child,os.stat(child,dir_fd=fd,follow_symlinks=False))
                for child in names:remove(fd,child,mount,depth+1,(relative+'/' if relative else '')+child)
                os.fsync(fd)
            current=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if (current.st_dev,current.st_ino)!=(before.st_dev,before.st_ino):raise CloudError('managed_tree_changed')
            confirmed(relative,current)
            if not stat.S_ISDIR(before.st_mode) and facts(current)!=facts(before):raise CloudError('managed_tree_changed')
            if stat.S_ISDIR(before.st_mode):os.rmdir(name,dir_fd=parent)
            else:os.unlink(name,dir_fd=parent)
            os.fsync(parent)
        finally:os.close(fd)
    try:
        if expected is not None:
            before=path.lstat()
            if [before.st_dev,before.st_ino]!=expected:raise CloudError('managed_tree_changed')
        remove(base,path.name,_mount(base),0,'')
    finally:os.close(base)
