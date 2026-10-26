"""Snapshot selected working-copy bytes into an application-owned object database."""
from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit

from naiyue_test_agent.contracts.git_inputs import (
    GitInputError, InputFile, InputExclusion, InputSnapshot, input_id, input_path, git_path, check_content,
)
from naiyue_test_agent.contracts.projects import LocalSource, GitSource
from naiyue_test_agent.storage.files import check_store_directory, open_store_readonly
from .git_repository import GitRepository, make_bundle, check_cache_budget


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
        if file_identity(before) != file_identity(opened):
            raise GitInputError('git_input_source_changed', (path,))
        if opened.st_size > limit:
            raise GitInputError('git_input_budget', (path,))
        with os.fdopen(os.dup(fd), 'rb') as stream:
            content = stream.read(limit+1)
        if len(content) > limit:
            raise GitInputError('git_input_budget', (path,))
        if file_identity(os.fstat(fd)) != file_identity(opened):
            raise GitInputError('git_input_source_changed', (path,))
        return content, '100755' if opened.st_mode & stat.S_IXUSR else '100644', file_identity(opened)


def check_links(contents):
    try:
        links = {path: data.decode('utf-8') for path, (mode, data) in contents.items() if mode == '120000'}
    except UnicodeError:
        raise GitInputError('git_input_invalid_path') from None
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


def validate_tree(repository, ref):
    contents, manifest, total = {}, [], 0
    for path, mode, oid in repository.tree(ref):
        size = int(repository.run(['cat-file','-s',oid]))
        total += size
        if size > repository.settings.max_file_bytes or total > repository.settings.max_input_bytes:
            raise GitInputError('git_input_budget', (path,))
        data = repository.run(['cat-file','blob',oid],output_limit=repository.settings.max_file_bytes)
        check_content(path, data)
        contents[path] = (mode, data)
        manifest.append(InputFile(path,mode,oid,size))
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
    def __init__(self, directory, settings):
        self.directory, self.settings = Path(directory).absolute(), settings
        self.repository = GitRepository(self.directory/'objects.git',settings)

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
            source_git = GitRepository(root,self.settings)
            status, head = source_git.run(['rev-parse','--verify','HEAD^{commit}'],check=False)
            if status:
                raise GitInputError('git_input_no_head')
            original = head.decode().strip()
            top = Path(source_git.run(['rev-parse','--show-toplevel']).decode().strip())
            if top.resolve() != root.resolve():
                raise GitInputError('git_input_layout_unsupported')
            if source_git.run(['rev-parse','--show-object-format']).strip() != b'sha1':
                raise GitInputError('git_input_unsupported')
            staged = source_git.run(['ls-files','--stage','-z'])
            for raw in staged.split(b'\0'):
                if not raw:
                    continue
                meta, raw_path = raw.split(b'\t',1)
                mode, _oid, stage = meta.decode().split()
                path = git_path(raw_path)
                if stage != '0' or mode not in ('100644','100755','120000'):
                    raise GitInputError('git_input_unsupported',(path,))
                entries.append(path)
            untracked = self._paths(source_git,['ls-files','--others','--exclude-standard','-z'])
            ignored = self._paths(source_git,['ls-files','--others','--ignored','--exclude-standard','-z'])
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
        else:
            require_source_url(source.url,source.ref)
        self.directory.mkdir(mode=0o700,parents=True)
        check_store_directory(self.directory)
        self.repository.run(['init','--bare','--quiet',str(self.repository.path)],cwd=self.directory)
        if local:
            self.repository.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',
                str(root),original+':refs/naiyue/source'])
        else:
            self.repository.run(['fetch','--no-tags','--no-recurse-submodules','--no-write-fetch-head',
                source.url,source.ref+':refs/naiyue/source'],credentials=True)
            original = self.repository.oid('refs/naiyue/source^{commit}')
        check_cache_budget(self.directory,self.settings.max_cache_bytes)
        snapshot = original
        if local:
            index = self.directory/'input.index'
            env = {'GIT_INDEX_FILE':str(index)}
            self.repository.run(['read-tree','--empty'],extra_env=env)
            lines, contents, total = [], {}, 0
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
                check_content(path,content)
                total += len(content)
                if total>self.settings.max_input_bytes:
                    raise GitInputError('git_input_budget',(path,))
                contents[path]=(mode,content); before[path]=identity
                oid = self.repository.run(['hash-object','--no-filters','-w','--stdin'],data=content).decode().strip()
                lines.append(f'{mode} {oid}\t{path}'.encode()+b'\0')
            check_links(contents)
            self.repository.run(['update-index','-z','--index-info'],data=b''.join(lines),extra_env=env)
            tree = self.repository.oid_from_index(env)
            if tree != self.repository.oid(original+'^{tree}'):
                when = datetime.now(timezone.utc).isoformat()
                author = dict(GIT_AUTHOR_NAME='Naiyue Input Snapshot',GIT_AUTHOR_EMAIL='input@invalid',
                    GIT_COMMITTER_NAME='Naiyue Input Snapshot',GIT_COMMITTER_EMAIL='input@invalid',
                    GIT_AUTHOR_DATE=when,GIT_COMMITTER_DATE=when)
                snapshot = self.repository.run(['commit-tree',tree,'-p',original],
                    data=('Naiyue input '+ident+'\n').encode(),extra_env=author).decode().strip()
            if (source_git.oid('HEAD^{commit}') != original or file_identity(root.stat())[:2] != root_identity
                    or source_git.run(['ls-files','--stage','-z']) != staged):
                raise GitInputError('git_input_source_changed')
            for path, identity in before.items():
                try:after = file_identity((root/path).lstat())
                except FileNotFoundError:after = None
                if after != identity:
                    raise GitInputError('git_input_source_changed',(path,))
        manifest,total = validate_tree(self.repository,snapshot)
        self.repository.run(['update-ref','refs/naiyue/input/'+ident,snapshot])
        size,digest = make_bundle(self.repository,self.directory/'input.bundle',ident)
        check_cache_budget(self.directory,self.settings.max_cache_bytes)
        return InputSnapshot(ident,original,snapshot,self.repository.oid(snapshot+'^{tree}'),
            manifest,tuple(excluded),total,digest,size)

    @staticmethod
    def _paths(repository,args):
        return {git_path(value) for value in repository.run(args,source_config=True).split(b'\0') if value}
