"""Persist byte ranges under an owned local archive, independent of remote cwd."""
import base64
import os
from pathlib import Path
import stat
from naiyue_test_agent.contracts.sandbox import require_operation_id,SandboxError
from naiyue_test_agent.tools.output import _directory,_GLOBAL_LOCK


def _identity(info):
    return [info.st_dev,info.st_ino,getattr(info,'st_birthtime',None)]


class SandboxOutput:
    def __init__(self,root,ident):
        self.root,self.ident=Path(root),require_operation_id(ident)

    def _path(self):
        try:
            with _GLOBAL_LOCK:return _directory(self.root,create=True)/(self.ident+'-raw.log')
        except (ValueError,UnicodeError) as error:
            raise OSError('Archive directory identity changed') from error

    def prepare(self,metadata):
        result=dict(metadata)
        path=self._path()
        if not result.get('archive_identity'):
            fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            try:result['archive_identity']=_identity(os.fstat(fd))
            finally:os.close(fd)
            result['archive_path']=str(path)
        return result

    def _open(self,metadata,flags):
        path=self._path()
        if str(path)!=metadata['archive_path']:raise OSError('Archive path changed')
        fd=os.open(path,flags|getattr(os,'O_NOFOLLOW',0))
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or _identity(info)!=metadata['archive_identity']
                or info.st_size<metadata['saved_bytes']):
            os.close(fd);raise OSError('Archive identity changed')
        return fd

    def append(self,metadata,frame):
        result=dict(metadata);data=frame['data']
        if (not isinstance(data,bytes) or any(type(frame[key]) is not int or not 0<=frame[key]<2**63
                for key in ('offset','next_offset','observed_bytes'))
                or frame['offset']<metadata['offset'] or frame['next_offset']!=frame['offset']+len(data)
                or frame['observed_bytes']<frame['next_offset']):raise SandboxError('env_protocol_mismatch')
        allowed=max(0,min(len(data),metadata['max_archive_bytes']-metadata['saved_bytes']))
        fd=self._open(metadata,os.O_RDWR)
        with os.fdopen(fd,'r+b',buffering=0) as target:
            target.seek(metadata['saved_bytes']);view=memoryview(data)[:allowed]
            while view:
                count=target.write(view)
                if not count:raise OSError('Archive write made no progress')
                view=view[count:]
            target.truncate(metadata['saved_bytes']+allowed);os.fsync(target.fileno())
        old_tail=base64.b64decode(metadata.get('tail_base64',''))
        result.update(offset=frame['next_offset'],saved_bytes=metadata['saved_bytes']+allowed,
            gap=bool(metadata.get('gap') or frame['offset']>metadata['offset']),
            truncated=bool(metadata.get('truncated') or allowed<len(data)),
            tail_base64=base64.b64encode((old_tail+data)[-metadata['tail_limit']:]).decode())
        return result

    def read(self,metadata):
        fd=self._open(metadata,os.O_RDONLY)
        with os.fdopen(fd,'rb') as source:return source.read(metadata['saved_bytes'])
