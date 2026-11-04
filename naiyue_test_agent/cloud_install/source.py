"""Bounded trusted release index, pinned manifests and verified artifact cache."""
import hashlib,json,os
import shutil
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit,urljoin,unquote

import httpx
from naiyue_test_agent.contracts.cloud_tasks import CloudError,ContentId
from pydantic import TypeAdapter
from naiyue_test_agent.contracts.cloud_releases import CloudRelease,ReleaseArtifact,ReleaseIndex
from naiyue_test_agent.storage.files import open_store_readonly,open_store_file,check_store_directory,sync_store_directory
from .store import directory,read


def authorize_url(settings,url,*,redirect=False):
    parsed=urlsplit(url);decoded=unquote(parsed.path)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.query and not redirect or len(url)>8192
            or any(ord(c)<33 or ord(c)==127 for c in url) or decoded!=parsed.path
            or '..' in PurePosixPath(decoded).parts or '\\' in decoded):
        raise CloudError('release_source_forbidden')
    for prefix in settings.trusted_prefixes:
        allowed=urlsplit(prefix)
        if (parsed.scheme,parsed.netloc)==(allowed.scheme,allowed.netloc) and parsed.path.startswith(allowed.path):return url
    raise CloudError('release_source_forbidden')


def _bytes(settings,url,limit,*,local_name=None):
    if Path(settings.index).is_absolute():
        path=Path(settings.index) if local_name is None else Path(settings.index).parent/local_name
        check_store_directory(path.parent)
        with os.fdopen(open_store_readonly(path),'rb') as stream:
            while data:=stream.read(min(settings.io_bytes,limit+1)):
                limit-=len(data)
                if limit<0:raise CloudError('release_artifact_too_large')
                yield data
        return
    current=authorize_url(settings,url)
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=settings.request_timeout_seconds,
                proxy=settings.download_proxy.get_secret_value() if settings.download_proxy else None) as client:
            for _ in range(6):
                with client.stream('GET',current,headers={'Accept-Encoding':'identity'}) as response:
                    if response.status_code in (301,302,303,307,308):
                        current=authorize_url(settings,urljoin(current,response.headers.get('location','')),redirect=True);continue
                    if response.status_code!=200 or response.headers.get('content-encoding','identity') not in ('','identity'):
                        raise CloudError('release_download_failed',503)
                    for data in response.iter_bytes(settings.io_bytes):
                        limit-=len(data)
                        if limit<0:raise CloudError('release_artifact_too_large')
                        yield data
                    return
            raise CloudError('release_redirect_limit')
    except httpx.HTTPError:raise CloudError('release_download_failed',503) from None


def artifact_file(settings,artifact):
    artifact=ReleaseArtifact.model_validate(artifact)
    authorize_url(settings,artifact.url)
    if artifact.size>settings.max_artifact_bytes:raise CloudError('release_artifact_too_large')
    root=directory(directory(directory(settings.work_dir)/'artifacts')/artifact.sha256);path=root/artifact.filename
    def valid():
        try:
            with os.fdopen(open_store_readonly(path),'rb') as f:
                before=os.fstat(f.fileno());sha=hashlib.sha256();size=0
                while data:=f.read(settings.io_bytes):sha.update(data);size+=len(data)
                after=os.fstat(f.fileno())
            return before==after and size==artifact.size and sha.hexdigest()==artifact.sha256
        except FileNotFoundError:return False
    if valid():return path
    ensure_capacity(settings,artifact.size)
    temporary=path.with_name(path.name+'.part');fd=open_store_file(temporary)
    with os.fdopen(fd,'wb') as f:
        sha=hashlib.sha256();size=0
        for data in _bytes(settings,artifact.url,artifact.size,local_name=artifact.filename):
            f.write(data);sha.update(data);size+=len(data)
        if size!=artifact.size or sha.hexdigest()!=artifact.sha256:raise CloudError('release_artifact_mismatch')
        f.flush();os.fsync(f.fileno())
    os.replace(temporary,path);sync_store_directory(root)
    return path


def ensure_capacity(settings,additional_bytes):
    root=directory(settings.work_dir);cache=root/'artifacts';size=0
    if cache.exists():
        check_store_directory(cache)
        for parent in cache.iterdir():
            check_store_directory(parent)
            for path in parent.iterdir():
                from naiyue_test_agent.storage.files import check_store_file
                check_store_file(path);size+=path.stat().st_size
                if size+additional_bytes>settings.max_cache_bytes:raise CloudError('update_cache_budget')
    if size+additional_bytes>settings.max_cache_bytes:raise CloudError('update_cache_budget')
    if shutil.disk_usage(root).free<settings.min_free_bytes+additional_bytes:raise CloudError('update_disk_budget')


def load_index(settings):
    if Path(settings.index).is_absolute():value=read(Path(settings.index),settings.max_index_bytes)
    else:
        raw=b''.join(_bytes(settings,settings.index,settings.max_index_bytes))
        from naiyue_test_agent.contracts.cloud_json import parse_object
        value=parse_object(raw,byte_limit=settings.max_index_bytes,depth_limit=32)
    return ReleaseIndex.model_validate(value)


def cached_release(settings,release_id,sha):
    sha=TypeAdapter(ContentId).validate_python(sha);cached=settings.work_dir/'artifacts'/sha
    if cached.exists():
        check_store_directory(cached)
        for path in sorted(cached.iterdir())[:16]:
            if path.name.endswith('.part'):continue
            with os.fdopen(open_store_readonly(path),'rb') as stream:raw=stream.read(settings.max_index_bytes+1)
            if len(raw)<=settings.max_index_bytes and hashlib.sha256(raw).hexdigest()==sha:
                manifest=CloudRelease.model_validate_json(raw)
                if manifest.release_id!=release_id:raise CloudError('release_identity_conflict')
                return manifest,sha
    return None


def load_release(settings,release_id,*,expected_sha256=None,cache=True):
    if expected_sha256 is not None:
        expected_sha256=TypeAdapter(ContentId).validate_python(expected_sha256)
        found=cached_release(settings,release_id,expected_sha256)
        if found is not None:return found
    index=load_index(settings)
    value=index.releases.get(release_id)
    if value is None:raise CloudError('release_not_found',404)
    artifact=ReleaseArtifact.model_validate(value)
    if expected_sha256 is not None and artifact.sha256!=expected_sha256:raise CloudError('release_identity_conflict')
    if cache:
        path=artifact_file(settings,artifact)
        with os.fdopen(open_store_readonly(path),'rb') as stream:raw=stream.read(artifact.size+1)
    else:
        authorize_url(settings,artifact.url)
        if artifact.size>settings.max_index_bytes:raise CloudError('release_artifact_too_large')
        raw=b''.join(_bytes(settings,artifact.url,artifact.size,local_name=artifact.filename))
    if len(raw)!=artifact.size or hashlib.sha256(raw).hexdigest()!=artifact.sha256:raise CloudError('release_artifact_mismatch')
    manifest=CloudRelease.model_validate_json(raw)
    if manifest.release_id!=release_id:raise CloudError('release_identity_conflict')
    return manifest,artifact.sha256
