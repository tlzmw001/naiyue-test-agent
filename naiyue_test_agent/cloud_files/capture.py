"""Freeze explicit source bytes once, with bounded memory and no Git mutations."""
from contextlib import ExitStack
import gzip
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import stat
import tarfile
import tempfile

from naiyue_test_agent.contracts.cloud_inputs import CapturedInput,InputEntry,InputManifest,Selection,InputPackage,check_input_content
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory,sync_store_directory
from .paths import source_handle,identity,check_links

logger=logging.getLogger(__name__)


def capture(source,selection,output,limits,*,codec='gzip',source_receipt=None):
    source=Path(source).absolute();output=Path(output).absolute();selection=Selection.model_validate(selection)
    if codec not in ('gzip','tar'):raise CloudError('input_codec_invalid')
    if output.is_relative_to(source) or source.is_relative_to(output):raise CloudError('input_capture_overlap')
    if output.exists() or output.is_symlink():raise CloudError('input_capture_exists')
    if len(selection.paths)>limits.max_files or len(selection.canonical)>limits.max_metadata_bytes:raise CloudError('input_budget')
    check_store_directory(source);output.parent.mkdir(parents=True,exist_ok=True);check_store_directory(output.parent)
    if selection.policy is not None:
        from .source_facts import verify
        if source_receipt is None:raise CloudError('source_plan_required')
        verify(source,source_receipt,limits)
    try:
        parents={source}
        before={}
        for path in selection.paths:
            target=source/path;check_store_directory(target.parent)
            parents.update(p for p in (target.parent,*target.parent.parents) if p.is_relative_to(source))
            before[path]=identity(target.lstat())
        directories={p:(identity(p.lstat())[:2],sorted(os.listdir(p))) for p in parents}
        with tempfile.TemporaryDirectory(prefix='.nyt-capture-',dir=output.parent) as temporary:
            staging=Path(temporary);frozen=staging/'frozen';frozen.mkdir();entries=[];total=0
            for number,path in enumerate(selection.paths):
                with source_handle(source,path,before[path]) as (stream,info):
                    sha=hashlib.sha256();size=0
                    if info is None:
                        content=stream
                        if len(content)>4096:raise CloudError('input_link_outside')
                        total+=len(content)
                        if total>limits.max_input_bytes:raise CloudError('input_budget')
                        entry=InputEntry(path=path,type='symlink',mode=511,target=content.decode('utf-8'),size=len(content),sha256=hashlib.sha256(content).hexdigest())
                    else:
                        if info.st_size>limits.max_file_bytes:raise CloudError('input_budget')
                        mode=493 if (path in selection.executables or selection.policy is None
                            and os.name!='nt' and info.st_mode & stat.S_IXUSR) else 420
                        prefix=b'';tail=b''
                        with (frozen/str(number)).open('xb') as out:
                            while chunk:=stream.read(limits.io_bytes):
                                tail=check_input_content(tail,chunk,first=size<128)
                                size+=len(chunk);total+=len(chunk)
                                if size>limits.max_file_bytes or total>limits.max_input_bytes:raise CloudError('input_budget')
                                if len(prefix)<512:prefix=(prefix+chunk)[:512]
                                sha.update(chunk);out.write(chunk)
                            out.flush();os.fsync(out.fileno())
                        if size!=info.st_size:raise CloudError('input_source_changed')
                        if mode==493 and prefix.startswith(b'#!') and b'\r' in prefix.split(b'\n',1)[0]:raise CloudError('input_crlf_executable')
                        entry=InputEntry(path=path,type='file',mode=mode,size=size,sha256=sha.hexdigest())
                    entries.append(entry)
            check_links(entries)
            for path,fact in before.items():
                check_store_directory((source/path).parent)
                if identity((source/path).lstat())!=fact:raise CloudError('input_source_changed')
            for directory,fact in directories.items():
                check_store_directory(directory)
                if (identity(directory.lstat())[:2],sorted(os.listdir(directory)))!=fact:raise CloudError('input_source_changed')
            if selection.policy is not None:verify(source,source_receipt,limits)
            manifest=InputManifest(selection={'policy_sha256':hashlib.sha256(selection.canonical).hexdigest()},entries=entries)
            if len(manifest.canonical)>limits.max_metadata_bytes:raise CloudError('input_budget')
            bundle=staging/'result';bundle.mkdir(mode=0o700)
            (bundle/'manifest.json').write_bytes(manifest.canonical);(bundle/'selection.json').write_bytes(selection.canonical)
            with ExitStack() as stack:
                raw=stack.enter_context((bundle/'archive').open('xb'))
                encoded=stack.enter_context(gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=limits.gzip_level,mtime=0,filename='')) if codec=='gzip' else raw
                archive=stack.enter_context(tarfile.open(fileobj=encoded,mode='w|',format=tarfile.PAX_FORMAT))
                for name,data in (('manifest.json',manifest.canonical),('selection.json',selection.canonical)):
                    member=tarfile.TarInfo(name);member.size=len(data);member.mode=0o600;archive.addfile(member,io.BytesIO(data))
                for number,entry in enumerate(entries):
                    member=tarfile.TarInfo('data/'+entry.path);member.mode=entry.mode
                    if entry.type=='symlink':member.type=tarfile.SYMTYPE;member.linkname=entry.target;archive.addfile(member)
                    else:
                        member.size=entry.size
                        with (frozen/str(number)).open('rb') as stream:archive.addfile(member,stream)
            size=(bundle/'archive').stat().st_size
            if size>limits.max_archive_bytes:raise CloudError('input_budget')
            sha=hashlib.sha256()
            with (bundle/'archive').open('rb') as stream:
                while chunk:=stream.read(limits.io_bytes):sha.update(chunk)
            result=CapturedInput(input_id=manifest.input_id,archive_sha256=sha.hexdigest(),size=size,codec=codec,directory=output,
                source_receipt=output/'source.json' if source_receipt is not None else None)
            description=InputPackage(input_id=result.input_id,archive_sha256=result.archive_sha256,size=size,
                codec=codec,manifest=manifest,selection=selection)
            (bundle/'input.json').write_text(description.model_dump_json(by_alias=True),encoding='utf-8')
            (bundle/'receipt.json').write_text(result.model_dump_json(),encoding='utf-8')
            if source_receipt is not None:(bundle/'source.json').write_text(source_receipt.model_dump_json(),encoding='utf-8')
            for file in bundle.iterdir():
                file.chmod(0o600)
                with file.open('r+b') as stream:os.fsync(stream.fileno())
            sync_store_directory(bundle);os.rename(bundle,output);sync_store_directory(output.parent)
            return result
    except (OSError,UnicodeError,ValueError) as error:
        logger.warning('cloud_input_capture_failed error_type=%s',type(error).__name__)
        raise CloudError('input_source_unavailable') from None
