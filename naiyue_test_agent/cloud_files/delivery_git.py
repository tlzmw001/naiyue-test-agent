"""Private delivery indexes use the existing bounded native Git process runner."""
import json,os,re
from pathlib import Path
from types import SimpleNamespace
from naiyue_test_agent.execution.git_repository import GitRepository
from naiyue_test_agent.contracts.git_native import GitInputError
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def quoted(value):return json.dumps(str(value),ensure_ascii=False)


def oid(value):
    text=value.decode().strip() if isinstance(value,bytes) else value
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',text):raise CloudError('cloud_delivery_git_invalid')
    return text


class DeliveryGit:
    def __init__(self,directory,limits,*,object_format='sha1',alternate=None):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700)
        self.path=self.directory/'objects.git';self.index=self.directory/'private.index'
        self.env={'GIT_INDEX_FILE':str(self.index)}
        if alternate is not None:self.env['GIT_ALTERNATE_OBJECT_DIRECTORIES']=quoted(alternate)
        self.repo=self.repository(self.directory,limits)
        self.run(['init','--bare','--quiet','--object-format='+object_format,str(self.path)],cwd=self.directory)

    @staticmethod
    def repository(path,limits):
        return GitRepository(path,SimpleNamespace(executable=limits.git_executable,timeout_seconds=limits.git_timeout_seconds,
            max_command_bytes=max(limits.max_metadata_bytes*4,limits.max_patch_bytes)))

    def run(self,args,*,env=None,**kwargs):
        try:
            return self.repo.run(['--git-dir='+str(self.path),*args],extra_env=self.env|(env or {}),**kwargs)
        except GitInputError as error:raise CloudError('cloud_delivery_git_failed',409) from error

    def hashes(self,paths,*,raw=True,cwd=None,args=()):
        if not paths:return []
        body=b''.join((quoted(p)+'\n').encode() for p in paths)
        result=self.run([*args,'hash-object','-w',*(['--no-filters'] if raw else []),'--stdin-paths'],data=body,cwd=cwd)
        values=[oid(value) for value in result.splitlines()]
        if len(values)!=len(paths):raise CloudError('cloud_delivery_git_invalid')
        return values

    def update(self,entries):
        body=b''.join((mode+' '+value+'\t'+path).encode()+b'\0' for path,mode,value in entries)
        if body:self.run(['update-index','-z','--index-info'],data=body)

    def listing(self,tree):
        result={}
        for raw in self.run(['ls-tree','-rz','--full-tree',tree]).split(b'\0'):
            if not raw:continue
            metadata,path=raw.split(b'\t',1);mode,kind,value=metadata.decode().split()
            result[path.decode('utf-8')]=(mode,kind,oid(value))
        return result
