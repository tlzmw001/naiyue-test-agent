"""Strict streaming tar validation; optional writes only into a fresh private tree."""
from contextlib import ExitStack
import gzip
import hashlib
import json
import logging
import os
import tarfile

from naiyue_test_agent.contracts.cloud_inputs import InputManifest,Selection,check_input_content
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_readonly
from .paths import check_links
from .paths import identity

logger=logging.getLogger(__name__)


def strict_json(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('duplicate_json')
            result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite')))


class _Headers(tarfile.TarInfo):
    def _proc_member(self,tar):
        if self.type in (tarfile.GNUTYPE_SPARSE,tarfile.XGLTYPE):raise CloudError('input_tar_type')
        if self.type in (tarfile.XHDTYPE,tarfile.GNUTYPE_LONGNAME,tarfile.GNUTYPE_LONGLINK) and self.size>4096:
            raise CloudError('input_tar_metadata_budget')
        result=super()._proc_member(tar)
        if result is not None and set(result.pax_headers)-{'path','linkpath'}:raise CloudError('input_tar_metadata')
        return result


class _BudgetReader:
    def __init__(self,stream,budget):self.stream,self.remaining=stream,budget
    def read(self,size):
        # A large PAX read cannot request an unbounded allocation.
        value=self.stream.read(min(size,self.remaining+1))
        self.remaining-=len(value)
        if self.remaining<0:raise CloudError('input_expansion_budget')
        return value


class _ArchiveReader:
    def __init__(self,stream,limit):self.stream,self.limit,self.sha,self.size=stream,limit,hashlib.sha256(),0
    def read(self,size=-1):
        value=self.stream.read(min(size,self.limit+1) if size>=0 else self.limit+1)
        self.size+=len(value)
        if self.size>self.limit:raise CloudError('input_budget')
        self.sha.update(value);return value


def validate_package(path,codec,limits,*,expected_id=None,destination=None,expected_archive_sha256=None,expected_size=None):
    try:
        with ExitStack() as stack:
            raw=stack.enter_context(os.fdopen(open_store_readonly(path),'rb'))
            before=identity(os.fstat(raw.fileno()));hashed=_ArchiveReader(raw,limits.max_archive_bytes)
            if os.fstat(raw.fileno()).st_size>limits.max_archive_bytes:raise CloudError('input_budget')
            if codec not in ('gzip','tar'):raise CloudError('input_codec_invalid')
            decoded=stack.enter_context(gzip.GzipFile(fileobj=hashed)) if codec=='gzip' else hashed
            budget=limits.max_input_bytes+limits.max_metadata_bytes*2+limits.max_files*8192+32768
            bounded=_BudgetReader(decoded,budget)
            tar=stack.enter_context(tarfile.open(fileobj=bounded,mode='r|',tarinfo=_Headers,bufsize=limits.io_bytes))
            metadata=[]
            for name in ('manifest.json','selection.json'):
                member=tar.next()
                if member is None or member.name!=name or not member.isfile() or member.size>limits.max_metadata_bytes:
                    raise CloudError('input_metadata_invalid')
                metadata.append(tar.extractfile(member).read(limits.max_metadata_bytes+1))
            manifest=InputManifest.model_validate(strict_json(metadata[0]));selection=Selection.model_validate(strict_json(metadata[1]))
            if (manifest.schema_version==3)!=(selection.schema_version==3):raise CloudError('input_manifest_mismatch')
            if metadata!=[manifest.canonical,selection.canonical]:raise CloudError('input_metadata_not_canonical')
            if (manifest.selection['policy_sha256']!=hashlib.sha256(selection.canonical).hexdigest()
                    or [e.path for e in manifest.entries]!=selection.paths or len(manifest.entries)>limits.max_files
                    or expected_id is not None and manifest.input_id!=expected_id):raise CloudError('input_manifest_mismatch')
            check_links(manifest.entries);total=0;links=[]
            for entry in manifest.entries:
                member=tar.next()
                if (member is None or member.name!='data/'+entry.path or member.mode!=entry.mode
                        or member.uid!=0 or member.gid!=0):raise CloudError('input_member_mismatch')
                target=destination/entry.path if destination is not None else None
                if target is not None and manifest.schema_version!=3:target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                if entry.type=='symlink':
                    content=entry.target.encode('utf-8')
                    if (not member.issym() or member.linkname!=entry.target or member.size!=0
                            or len(content)!=entry.size or hashlib.sha256(content).hexdigest()!=entry.sha256):raise CloudError('input_link_mismatch')
                    if target is not None:links.append((target,entry.target))
                    total+=len(content)
                else:
                    if not member.isfile() or member.size!=entry.size or entry.size>limits.max_file_bytes:raise CloudError('input_member_mismatch')
                    sha=hashlib.sha256();count=0;prefix=b'';tail=b''
                    with ExitStack() as files:
                        stream=files.enter_context(tar.extractfile(member))
                        out=None
                        if target is not None:
                            if manifest.schema_version==3:
                                from .delivery_paths import parent
                                fd,directory,name=files.enter_context(parent(destination,entry.path,create=True,ordinary=True))
                                flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
                                descriptor=os.open(name,flags,entry.mode,dir_fd=fd) if fd is not None else os.open(directory/name,flags,entry.mode)
                                out=files.enter_context(os.fdopen(descriptor,'wb'))
                                if os.name!='nt':os.fchmod(out.fileno(),entry.mode)
                            else:out=files.enter_context(target.open('xb'))
                        while chunk:=stream.read(limits.io_bytes):
                            if manifest.schema_version!=3:tail=check_input_content(tail,chunk,first=count<128)
                            count+=len(chunk);total+=len(chunk)
                            if total>limits.max_input_bytes:raise CloudError('input_budget')
                            prefix=(prefix+chunk)[:512] if len(prefix)<512 else prefix
                            sha.update(chunk)
                            if out is not None:out.write(chunk)
                        if out is not None:out.flush();os.fsync(out.fileno())
                    if count!=entry.size or sha.hexdigest()!=entry.sha256:raise CloudError('input_file_hash_mismatch')
                    if manifest.schema_version!=3 and entry.mode==493 and prefix.startswith(b'#!') and b'\r' in prefix.split(b'\n',1)[0]:raise CloudError('input_crlf_executable')
                    if target is not None and manifest.schema_version!=3:target.chmod(entry.mode)
                if total>limits.max_input_bytes:raise CloudError('input_budget')
            if tar.next() is not None:raise CloudError('input_extra_member')
            # Include the tar stream's own read-ahead buffer, not only raw gzip.
            while chunk:=tar.fileobj.read(limits.io_bytes):
                if any(chunk):raise CloudError('input_trailing_data')
            if identity(os.fstat(raw.fileno()))!=before:raise CloudError('input_archive_changed')
            if ((expected_archive_sha256 is not None and hashed.sha.hexdigest()!=expected_archive_sha256)
                    or expected_size is not None and hashed.size!=expected_size):raise CloudError('input_archive_hash_mismatch')
            for target,value in links:target.symlink_to(value)
            return manifest,selection
    except (OSError,EOFError,tarfile.TarError,ValueError,RecursionError) as error:
        logger.warning('cloud_input_archive_invalid error_type=%s',type(error).__name__)
        raise CloudError('input_archive_invalid') from None
