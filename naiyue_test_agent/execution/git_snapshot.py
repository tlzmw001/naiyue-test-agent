"""Snapshot selected working-copy bytes into an application-owned object database."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import logging
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import unicodedata
from urllib.parse import urlsplit

from naiyue_test_agent.contracts.git_inputs import (
    GitInputError, InputFile, InputExclusion, InputSnapshot, input_id, input_path, git_path, object_id, check_content,
)
from naiyue_test_agent.contracts.projects import LocalSource, GitSource
from naiyue_test_agent.diagnostics import safe_log
from naiyue_test_agent.storage.files import check_store_directory, open_store_readonly
from .git_repository import GitRepository, make_bundle, check_cache_budget


logger=logging.getLogger(__name__)


def _joined(calls):
    primary=None;values=[]
    with ThreadPoolExecutor(max_workers=len(calls),thread_name_prefix='naiyue-snapshot') as workers:
        futures=[workers.submit(call) for call in calls]
        for future in futures:
            try:values.append(future.result())
            except BaseException as error:
                values.append(None)
                if primary is None:primary=error
                else:safe_log(logger.warning,'snapshot_parallel_secondary_failed error_type=%s',type(error).__name__)
    if primary is not None:raise primary
    return values


def file_identity(info):
    return tuple(getattr(info, name) for name in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns'))


def read_input(root, path, limit):
    """Pin POSIX parent handles; Windows uses the existing reparse-safe file opener."""
    target = root / path
    with ExitStack() as stack:
        if os.open in os.supports_dir_fd:
            parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, parent)
            for part in PurePosixPath(path).parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, parent)
            name = PurePosixPath(path).name
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                content = os.readlink(name, dir_fd=parent).encode('utf-8')
                return content, '120000', file_identity(before)
            if not stat.S_ISREG(before.st_mode):
                raise GitInputError('git_input_unsupported', (path,))
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, fd)
        else:
            check_store_directory(target.parent)
            before = target.lstat()
            if stat.S_ISLNK(before.st_mode):
                return os.readlink(target).encode('utf-8'), '120000', file_identity(before)
            fd = open_store_readonly(target); stack.callback(os.close, fd)
        opened = os.fstat(fd)
        # Windows path stat and descriptor fstat can report different ctime meanings.
        if ((before.st_dev,before.st_ino,before.st_size)
                != (opened.st_dev,opened.st_ino,opened.st_size)):
            raise GitInputError('git_input_source_changed', (path,))
        if opened.st_size > limit:
            raise GitInputError('git_input_budget', (path,))
        with os.fdopen(os.dup(fd), 'rb') as stream:
            content = stream.read(opened.st_size+1)
        after = os.fstat(fd)
        if len(content) > limit or after.st_size > limit:
            raise GitInputError('git_input_budget', (path,))
        if len(content) != opened.st_size or file_identity(after) != file_identity(opened):
            raise GitInputError('git_input_source_changed', (path,))
        return content, '100755' if opened.st_mode & stat.S_IXUSR else '100644', file_identity(before)


def check_links(contents):
    try:
        links = {path: data.decode('utf-8') for path, (mode, data) in contents.items() if mode == '120000'}
    except UnicodeError:
        raise GitInputError('git_input_invalid_path') from None
    spellings = {}
    if links:
        for path in contents:
            parts = PurePosixPath(path).parts
            for length in range(1,len(parts)+1):
                prefix = '/'.join(parts[:length])
                key = unicodedata.normalize('NFD',prefix.casefold())
                spellings.setdefault(key,set()).add(prefix)
    for path in links:
        pending, resolved, count = list(PurePosixPath(path).parts), [], 0
        while pending:
            part = pending.pop(0)
            if part == '.':
                continue
            if part == '..':
                if not resolved:
                    raise GitInputError('git_input_outside', (path,))
                resolved.pop(); continue
            current = '/'.join([*resolved, part])
            # A portable link must not change meaning on a case/normalization-insensitive filesystem.
            variants = spellings.get(unicodedata.normalize('NFD',current.casefold()))
            if variants is not None and variants != {current}:
                raise GitInputError('git_input_outside', (path,))
            if current in links:
                count += 1
                target = links[current]
                if (count > 40 or not target or PurePosixPath(target).is_absolute()
                        or '\\' in target or any(ord(c)<32 for c in target)
                        or any(p.casefold() == '.git' for p in PurePosixPath(target).parts)):
                    raise GitInputError('git_input_outside', (path,))
                pending = list(PurePosixPath(target).parts) + pending
            else:
                resolved.append(part)


def validate_tree(repository, ref, *, expected_tree=None):
    contents, manifest, total = {}, [], 0
    entries = repository.tree(ref)
    if entries:
        queries=b''.join(oid.encode()+b'\n' for _, _, oid in entries)
        if expected_tree is not None:queries+=(ref+'^{tree}\n').encode('ascii')
        listed = repository.run(['cat-file','--batch-check'],
            data=queries, output_limit=len(entries)*100+100)
        rows = listed.splitlines()
        if len(rows) != len(entries)+int(expected_tree is not None):
            raise GitInputError('git_input_invalid_object')
        if expected_tree is not None:
            fields=rows.pop().split(b' ')
            if (len(fields)!=3 or fields[:2]!=[expected_tree.encode('ascii'),b'tree']
                    or not fields[2].isdigit()):raise GitInputError('git_input_invalid_object')
    else:
        rows = []
        if expected_tree is not None and expected_tree!=hashlib.sha1(b'tree 0\0').hexdigest():
            raise GitInputError('git_input_invalid_object')
    sizes = []
    for (path, mode, oid), row in zip(entries, rows, strict=True):
        fields = row.split(b' ')
        if (len(fields) != 3 or fields[0] != oid.encode() or fields[1] != b'blob'
                or not fields[2].isdigit() or len(fields[2]) > 20):
            raise GitInputError('git_input_invalid_object')
        size = int(fields[2])
        total += size
        if size > repository.settings.max_file_bytes or total > repository.settings.max_input_bytes:
            raise GitInputError('git_input_budget', (path,))
        sizes.append(size)
    groups, group, group_bytes = [], [], 0
    for entry, size in zip(entries, sizes, strict=True):
        if group and group_bytes + size > repository.settings.max_command_bytes:
            groups.append(group); group=[]; group_bytes=0
        group.append((entry,size)); group_bytes += size
    if group:
        groups.append(group)
    for group in groups:
        output = repository.run(['cat-file','--batch'],
            data=b''.join(oid.encode()+b'\n' for (_, _, oid), _ in group),
            output_limit=sum(size+100 for _, size in group))
        offset = 0
        for (path, mode, oid), size in group:
            end = output.find(b'\n', offset)
            if end < 0 or output[offset:end] != f'{oid} blob {size}'.encode():
                raise GitInputError('git_input_invalid_object')
            start = end+1; stop = start+size
            if output[stop:stop+1] != b'\n':
                raise GitInputError('git_input_invalid_object')
            data = output[start:stop]
            check_content(path, data)
            contents[path] = (mode, data)
            manifest.append(InputFile(path,mode,oid,size))
            offset = stop+1
        if offset != len(output):
            raise GitInputError('git_input_invalid_object')
    check_links(contents)
    index = repository.path.parent / 'validation.index'
    env = {'GIT_INDEX_FILE':str(index.absolute())}
    repository.run(['read-tree',ref],extra_env=env)
    check_filters(repository, [item.path for item in manifest], extra_env=env, cached=True)
    return tuple(manifest), total


def check_filters(repository, paths, *, extra_env=None, cached=False):
    args = ['check-attr','-z','--stdin'] + (['--cached'] if cached else []) + ['filter']
    result = repository.run(args,data=b''.join(path.encode()+b'\0' for path in paths),extra_env=extra_env)
    fields = result.split(b'\0')
    for index in range(0,len(fields)-1,3):
        if fields[index+2] not in (b'unspecified',b'unset'):
            raise GitInputError('git_input_unsupported',(fields[index].decode('utf-8'),))


def require_source_url(url, ref):
    if (not isinstance(url,str) or re.search(r'[\x00-\x20\x7f]',url)
            or not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*',ref)
            or '..' in ref or ref.endswith(('/', '.'))):
        raise GitInputError('git_input_invalid_source')
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise GitInputError('git_input_invalid_source') from None
    if parsed.scheme not in ('https','ssh','file') or parsed.password or parsed.query or parsed.fragment:
        raise GitInputError('git_input_invalid_source')
    if parsed.scheme == 'https' and parsed.username:
        raise GitInputError('git_input_invalid_source')
    if parsed.scheme != 'file' and not parsed.hostname:
        raise GitInputError('git_input_invalid_source')


class SnapshotBuilder:
    def __init__(self, directory, settings, *, source_runner=None):
        self.directory, self.settings = Path(directory).absolute(), settings
        self.repository = GitRepository(self.directory/'objects.git',settings)
        self.source_runner = source_runner

    def prepare(self, source, ident, *, include_untracked=()):
        input_id(ident)
        if (not isinstance(include_untracked,(list,tuple)) or len(include_untracked)>self.settings.max_files
                or any(not isinstance(path,str) for path in include_untracked)
                or len(set(include_untracked)) != len(include_untracked)):
            raise GitInputError('git_input_invalid_selection')
        selected = tuple(sorted(input_path(path) for path in include_untracked))
        local = isinstance(source, LocalSource)
        if not local and (not isinstance(source,GitSource) or selected):
            raise GitInputError('git_input_invalid_selection')
        root, before, entries, excluded = None, {}, [], []
        if local:
            root = source.path.absolute()
            check_store_directory(root)
            if not (root/'.git').exists():
                raise GitInputError('git_input_no_repository')
            if not (root/'.git').is_dir() or (root/'.git').is_symlink():
                raise GitInputError('git_input_layout_unsupported')
            source_git = GitRepository(root,self.settings,runner=self.source_runner)
            status, metadata = source_git.run(['rev-parse','--show-toplevel','--show-object-format',
                '--verify','HEAD^{commit}'],check=False)
            if status:
                # Git also rejects malformed effective mode config during rev-parse.
                self._modes(source_git)
                raise GitInputError('git_input_no_head')
            parts=metadata.rstrip(b'\n').rsplit(b'\n',2)
            if len(parts)!=3:raise GitInputError('git_input_invalid_object')
            top = Path(parts[0].decode().strip())
            if top.resolve() != root.resolve():
                raise GitInputError('git_input_layout_unsupported')
            if parts[1] != b'sha1':
                raise GitInputError('git_input_unsupported')
            original=object_id(parts[2].decode('ascii'))
        else:
            require_source_url(source.url,source.ref)
        if local:
            source_modes,staged,entries,excluded,index_modes,root_identity=self._inventory(source_git,root,selected)
        self.directory.mkdir(mode=0o700,parents=True)
        check_store_directory(self.directory)
        if local:
            (contents,before),(original,original_tree)=_joined([
                lambda:self._read_local(root,entries,selected,index_modes,source_modes),
                lambda:self._fetch(source,root,source_git,original)])
        else:original,original_tree=self._fetch(source,None,None,None)
        snapshot,tree = original,original_tree
        if local:
            candidate,tree,expected_manifest=self._import(contents,original,ident)
            if tree!=original_tree:snapshot=candidate
            _,(manifest,total)=_joined([
                lambda:self._verify_local(source_git,root,original,source_modes,staged,root_identity,before),
                lambda:validate_tree(self.repository,snapshot,expected_tree=tree)])
        else:manifest,total=validate_tree(self.repository,snapshot,expected_tree=tree)
        if local and {item.path:(item.mode,item.oid,item.size) for item in manifest}!=expected_manifest:
            raise GitInputError('git_input_invalid_object')
        updates=f'update refs/naiyue/input/{ident} {snapshot}\n'
        if local:updates+=f'delete refs/naiyue/pending/{ident}\n'
        self.repository.run(['update-ref','--stdin'],data=updates.encode('ascii'))
        size,digest = make_bundle(self.repository,self.directory/'input.bundle',ident)
        check_cache_budget(self.directory,self.settings.max_cache_bytes)
        return InputSnapshot(ident,original,snapshot,tree,
            manifest,tuple(excluded),total,digest,size)

    def _inventory(self,source_git,root,selected):
        source_modes,staged,untracked,ignored=_joined([
            lambda:self._modes(source_git),lambda:source_git.run(['ls-files','--stage','-z']),
            lambda:self._paths(source_git,['ls-files','--others','--exclude-standard','-z']),
            lambda:self._paths(source_git,['ls-files','--others','--ignored','--exclude-standard','-z'])])
        entries=[]
        index_modes = {}
        for raw in staged.split(b'\0'):
            if not raw:
                continue
            meta, raw_path = raw.split(b'\t',1)
            mode, _oid, stage = meta.decode().split()
            path = git_path(raw_path)
            if stage != '0' or mode not in ('100644','100755','120000'):
                raise GitInputError('git_input_unsupported',(path,))
            index_modes[path] = mode
            entries.append(path)
        for path in selected:
            if path in ignored:
                raise GitInputError('git_input_ignored',(path,))
            if path not in untracked:
                raise GitInputError('git_input_invalid_selection',(path,))
        excluded = ([InputExclusion(path,'untracked') for path in sorted(untracked-set(selected))]
                    + [InputExclusion(path,'ignored') for path in sorted(ignored)])
        entries = sorted(set(entries) | set(selected))
        if len(entries)+len(excluded)>self.settings.max_files:
            raise GitInputError('git_input_budget')
        check_filters(source_git,entries)
        root_identity = file_identity(root.stat())[:2]
        return source_modes,staged,entries,excluded,index_modes,root_identity

    def _fetch(self,source,root,source_git,original):
        local=isinstance(source,LocalSource)
        self.repository.run(['init','--bare','--quiet',str(self.repository.path)],cwd=self.directory)
        if local:
            fetch_source = root
            fetch_ref = original
            if self.source_runner is not None:
                # Never invoke upload-pack in a task-writable repository as the controller.
                fetch_source = self.directory/'source.bundle'
                with fetch_source.open('xb') as bundle:
                    source_git.run(['bundle','create','-','HEAD'],output_stream=bundle,
                                   output_limit=self.settings.max_bundle_bytes)
                    bundle.flush();os.fsync(bundle.fileno())
                fetch_ref = 'HEAD'
            self.repository.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',
                str(fetch_source),fetch_ref+':refs/naiyue/source'])
        else:
            self.repository.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',
                source.url,source.ref+':refs/naiyue/source'],credentials=True)
        refs=self.repository.run(['rev-parse','refs/naiyue/source^{commit}','refs/naiyue/source^{tree}']).splitlines()
        if len(refs)!=2:raise GitInputError('git_input_invalid_object')
        fetched,original_tree=(object_id(value.decode('ascii')) for value in refs)
        if local and fetched!=original:raise GitInputError('git_input_source_changed')
        original=fetched
        check_cache_budget(self.directory,self.settings.max_cache_bytes)
        return original,original_tree

    def _read_local(self,root,entries,selected,index_modes,source_modes):
        before={}
        contents, total = {}, 0
        for path in entries:
            try:
                content,mode,identity = read_input(root,path,self.settings.max_file_bytes)
            except FileNotFoundError:
                if path in selected:
                    raise GitInputError('git_input_source_changed',(path,)) from None
                before[path]=None
                continue
            except OSError:
                raise GitInputError('git_input_outside',(path,)) from None
            if mode != '120000':
                prior = index_modes.get(path)
                if prior == '120000' and not source_modes['symlinks']:
                    mode = '120000'
                elif not source_modes['filemode']:
                    mode = prior if prior in ('100644','100755') else '100644'
            check_content(path,content)
            total += len(content)
            if total>self.settings.max_input_bytes:
                raise GitInputError('git_input_budget',(path,))
            contents[path]=(mode,content); before[path]=identity
        check_links(contents)
        return contents,before

    def _verify_local(self,source_git,root,original,source_modes,staged,root_identity,before):
        if (source_git.oid('HEAD^{commit}') != original or file_identity(root.stat())[:2] != root_identity
                or source_git.run(['ls-files','--stage','-z']) != staged
                or self._modes(source_git)!=source_modes):
            raise GitInputError('git_input_source_changed')
        for path, identity in before.items():
            try:after = file_identity((root/path).lstat())
            except FileNotFoundError:after = None
            if after != identity:
                raise GitInputError('git_input_source_changed',(path,))

    @staticmethod
    def _modes(repository):
        status,raw=repository.run(['config','--bool','--null','--get-regexp',r'^core\.(filemode|symlinks)$'],
            source_config=True,check=False)
        if status not in (0,1):raise GitInputError('git_input_git_failed')
        modes=dict(filemode=True,symlinks=True)
        for entry in raw.split(b'\0'):
            if not entry:continue
            key,separator,value=entry.partition(b'\n')
            if key not in (b'core.filemode',b'core.symlinks') or not separator or value not in (b'true',b'false'):
                raise GitInputError('git_input_invalid_object')
            modes[key.decode().removeprefix('core.')]=value==b'true'
        return modes

    def _import(self,contents,original,ident):
        mark=len(contents)+1;message=f'Naiyue input {ident}\n'.encode()
        with tempfile.TemporaryFile() as stream:
            stream.write(b'feature done\n')
            for number,(_,(_,content)) in enumerate(contents.items(),1):
                stream.write(f'blob\nmark :{number}\ndata {len(content)}\n'.encode())
                stream.write(content);stream.write(b'\n')
            when=int(datetime.now(timezone.utc).timestamp())
            stream.write(f'commit refs/naiyue/pending/{ident}\nmark :{mark}\n'.encode())
            stream.write(f'committer Naiyue Input Snapshot <input@invalid> {when} +0000\ndata {len(message)}\n'.encode())
            stream.write(message);stream.write(f'from {original}\ndeleteall\n'.encode())
            for number,(path,(mode,_)) in enumerate(contents.items(),1):
                quoted=b'"'+path.encode('utf-8').replace(b'"',b'\\"')+b'"'
                stream.write(f'M {mode} :{number} '.encode()+quoted+b'\n')
            stream.write(b'\n')
            for number in range(1,mark+1):stream.write(f'get-mark :{number}\n'.encode())
            stream.write(f'ls :{mark} ""\ndone\n'.encode())
            rows=self.repository.run(['fast-import','--quiet','--done'],input_stream=stream,
                output_limit=len(contents)*41+200).splitlines()
        if len(rows)!=len(contents)+2:raise GitInputError('git_input_invalid_object')
        try:
            candidate=object_id(rows[-2].decode('ascii'))
            prefix,tab,path=rows[-1].partition(b'\t')
            mode,kind,raw_tree=prefix.split(b' ')
            if mode!=b'040000' or kind!=b'tree' or not tab or path:raise ValueError()
            tree=object_id(raw_tree.decode('ascii'));manifest={}
            for (path,(mode,content)),raw_oid in zip(contents.items(),rows[:-2],strict=True):
                oid=object_id(raw_oid.decode('ascii'))
                if oid!=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest():raise ValueError()
                manifest[path]=(mode,oid,len(content))
        except (ValueError,UnicodeError):raise GitInputError('git_input_invalid_object') from None
        return candidate,tree,manifest

    @staticmethod
    def _paths(repository,args):
        return {git_path(value) for value in repository.run(args,source_config=True).split(b'\0') if value}
