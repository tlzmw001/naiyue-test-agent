"""Raw private trees built in batches; never git-add the writable workspace."""
import json,os
from pathlib import Path
from types import SimpleNamespace
from naiyue_test_agent.execution.git_repository import GitRepository
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.cloud_tasks import CloudError


class ResultGit:
    def __init__(self,directory,limits):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700)
        self.max_stream_bytes=limits.max_result_bytes
        self.path=self.directory/'objects.git';self.calls=0
        self.repo=GitRepository(self.path,SimpleNamespace(executable=limits.git_executable,
            timeout_seconds=limits.git_timeout_seconds,max_command_bytes=max(limits.max_metadata_bytes*4,limits.max_patch_bytes)))
        self.run(['init','--bare','--quiet',str(self.path)],cwd=self.directory)

    def run(self,args,*,index=None,**kwargs):
        self.calls+=1
        env={'GIT_INDEX_FILE':str(index)} if index is not None else {}
        try:return self.repo.run(['-c','core.protectNTFS=false','-c','core.protectHFS=false',
            '--git-dir='+str(self.path),*args],extra_env=env,**kwargs)
        except GitInputError as error:raise CloudError('result_git_failed') from error

    def tree(self,entries,blobs,name):
        index=self.directory/(name+'.index')
        raw=b''.join((json.dumps(str(blobs[e.path]),ensure_ascii=False)+'\n').encode() for e in entries)
        hashes=self.run(['hash-object','-w','--no-filters','--stdin-paths'],data=raw).decode().splitlines() if entries else []
        if len(hashes)!=len(entries) or any(len(h)!=40 for h in hashes):raise CloudError('result_git_failed')
        data=b''.join((('120000' if e.type=='symlink' else '100755' if e.mode==0o755 else '100644')+' '+h+'\t'+e.path).encode()+b'\0'
            for e,h in zip(entries,hashes))
        self.run(['read-tree','--empty'],index=index)
        if data:self.run(['update-index','-z','--index-info'],index=index,data=data)
        return self.run(['write-tree'],index=index).decode().strip()

    def patch(self,old,new,destination,limit):
        with destination.open('xb') as stream:
            size=0
            def bounded_write(chunk):
                nonlocal size
                remaining=max(0,limit-size);size+=len(chunk)
                if remaining:stream.write(chunk[:remaining])
            self.run(['diff','--binary','--full-index','--no-renames','--no-ext-diff','--no-textconv',old,new,'--'],
                output_stream=SimpleNamespace(write=bounded_write),output_limit=self.max_stream_bytes)
            stream.flush();os.fsync(stream.fileno())
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
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);target.write_text(rule.content)
    raw=git.run(['-c','core.excludesFile='+str(global_ignore),'--work-tree='+str(work),'check-ignore','--no-index','-z','--stdin'],
        data=b''.join(p.encode()+b'\0' for p in paths),check=False)
    status,body=raw
    if status not in (0,1):raise CloudError('result_policy_failed')
    return {p.decode() for p in body.split(b'\0') if p}
