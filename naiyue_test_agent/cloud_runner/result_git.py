"""Raw private trees built in batches; never git-add the writable workspace."""
import hashlib,json,zlib
from pathlib import Path
from types import SimpleNamespace
from naiyue_test_agent.execution.git_repository import GitRepository
from naiyue_test_agent.contracts.git_native import GitInputError
from naiyue_test_agent.contracts.cloud_tasks import CloudError


class ResultGit:
    def __init__(self,directory,limits,*,existing=False,alternate=None):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700,exist_ok=existing)
        self.max_stream_bytes=limits.max_result_bytes
        self.path=self.directory/'objects.git';self.calls=0;self.alternate=alternate
        self.repo=GitRepository(self.path,SimpleNamespace(executable=limits.git_executable,
            timeout_seconds=limits.git_timeout_seconds,max_command_bytes=max(limits.max_metadata_bytes*4,limits.max_patch_bytes)))
        if not existing:self.run(['init','--bare','--quiet','--object-format=sha1',str(self.path)],cwd=self.directory)

    def run(self,args,*,index=None,**kwargs):
        self.calls+=1
        env={'GIT_INDEX_FILE':str(index)} if index is not None else {}
        if self.alternate is not None:env['GIT_ALTERNATE_OBJECT_DIRECTORIES']=json.dumps(str(self.alternate),ensure_ascii=False)
        try:return self.repo.run(['-c','core.protectNTFS=false','-c','core.protectHFS=false',
            '--git-dir='+str(self.path),*args],extra_env=env,**kwargs)
        except GitInputError as error:raise CloudError('result_git_failed') from error

    def tree(self,entries,blobs,name):
        hashes=self.hashes(blobs);index=self.directory/(name+'.index')
        data=b''.join((('120000' if e.type=='symlink' else '100755' if e.mode==0o755 else '100644')+' '+hashes[e.path]+'\t'+e.path).encode()+b'\0'
            for e in entries)
        self.run(['read-tree','--empty'],index=index)
        if data:self.run(['update-index','-z','--index-info'],index=index,data=data)
        return self.run(['write-tree'],index=index).decode().strip()

    def hashes(self,blobs):
        raw=b''.join((json.dumps(str(path),ensure_ascii=False)+'\n').encode() for path in blobs.values())
        hashes=self.run(['hash-object','-w','--no-filters','--stdin-paths'],data=raw).decode().splitlines() if blobs else []
        if len(hashes)!=len(blobs) or any(len(h)!=40 or any(c not in '0123456789abcdef' for c in h) for h in hashes):
            raise CloudError('result_git_failed')
        return dict(zip(blobs,hashes))

    def tree_oids(self,entries,hashes,*,known_objects=()):
        """Write only new raw trees: native write-tree freshens alternate objects."""
        root={};written=set()
        for entry in entries:
            node=root;parts=entry.path.split('/')
            for part in parts[:-1]:
                node=node.setdefault(part,{})
                if not isinstance(node,dict):raise CloudError('result_git_failed')
            if parts[-1] in node:raise CloudError('result_git_failed')
            node[parts[-1]]=('120000' if entry.type=='symlink' else '100755' if entry.mode==0o755 else '100644',hashes[entry.path])
        def write(node):
            payload=bytearray()
            for name,item in sorted(node.items(),key=lambda pair:pair[0].encode()+(b'/' if isinstance(pair[1],dict) else b'')):
                mode,oid=('40000',write(item)) if isinstance(item,dict) else item
                payload.extend(mode.encode()+b' '+name.encode()+b'\0'+bytes.fromhex(oid))
            raw=b'tree '+str(len(payload)).encode()+b'\0'+payload;oid=hashlib.sha1(raw).hexdigest()
            # Git synthesizes the canonical empty tree, as native write-tree does.
            if payload and oid not in known_objects and oid not in written:
                parent=self.path/'objects'/oid[:2];parent.mkdir(mode=0o700,exist_ok=True)
                with (parent/oid[2:]).open('xb') as target:target.write(zlib.compress(raw))
                written.add(oid)
            return oid
        return write(root)

    def entries(self,tree):
        result={}
        for entry in self.run(['ls-tree','-rz','--full-tree',tree]).split(b'\0'):
            if not entry:continue
            head,path=entry.split(b'\t',1);mode,kind,oid=head.decode().split(' ')
            if kind!='blob':raise CloudError('result_baseline_changed')
            result[path.decode()]=(mode,oid)
        return result

    def patch(self,old,new,destination,limit):
        with destination.open('xb') as stream:
            size=0
            def bounded_write(chunk):
                nonlocal size
                remaining=max(0,limit-size);size+=len(chunk)
                if remaining:stream.write(chunk[:remaining])
            self.run(['diff','--binary','--full-index','--no-renames','--no-ext-diff','--no-textconv',old,new,'--'],
                output_stream=SimpleNamespace(write=bounded_write),output_limit=self.max_stream_bytes)
            stream.flush()
        if size>limit:raise CloudError('result_patch_budget')


def ignored_paths(policy,paths,directory,limits):
    if policy is None or not paths:return set()
    git=ResultGit(directory,limits)
    work=git.directory/'work';work.mkdir(mode=0o700)
    global_ignore=git.directory/'global-ignore';global_ignore.write_text('')
    for rule in policy.rules:
        if rule.kind!='ignore':continue
        if rule.name=='global/ignore':target=global_ignore
        elif rule.name=='repository/info/exclude':target=git.path/'info/exclude'
        elif rule.name.startswith('worktree/') and rule.name.endswith('.gitignore'):
            target=work/rule.name.removeprefix('worktree/')
        else:continue
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);target.write_text(rule.content,encoding='utf-8',newline='')
    raw=git.run(['-c','core.excludesFile='+str(global_ignore),'--work-tree='+str(work),'check-ignore','--no-index','-z','--stdin'],
        data=b''.join(p.encode()+b'\0' for p in paths),check=False)
    status,body=raw
    if status not in (0,1):raise CloudError('result_policy_failed')
    return {p.decode() for p in body.split(b'\0') if p}
