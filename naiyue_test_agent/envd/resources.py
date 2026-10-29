"""Bounded metadata and content batches; all reads remain on protected descriptors."""
import base64
import logging
import json
import os
from pathlib import PurePosixPath
import stat

from naiyue_test_agent.contracts.environment import ResourceBoundaryError
from naiyue_test_agent.contracts.sandbox import SandboxError

logger=logging.getLogger(__name__)
MAX_PATHS=32
MAX_DIRECTORY_ENTRIES=256
MAX_CONTENT_BYTES=65536


def _paths(paths):
    if (not isinstance(paths,list) or len(paths)>MAX_PATHS or
            any(not isinstance(p,str) or not p or len(p)>4096 or '\0' in p for p in paths)):
        raise SandboxError('env_protocol_mismatch')
    return paths


def _failure(error):
    code='outside_workspace' if isinstance(error,ResourceBoundaryError) else 'resource_unavailable'
    logger.warning('resource_batch_item_failed code=%s error_type=%s',code,type(error).__name__)
    return {'error':code}


def _describe(files,path):
    try:
        with files._resource(path,missing=True) as target:
            exists=target.info is not None
            return {'resolved':files.workspace.public(target.relative),'info':{
                'exists':exists,'is_link':target.linked,
                'kind':'missing' if not exists else 'directory' if stat.S_ISDIR(target.info.st_mode) else 'file'}}
    except (OSError,ValueError,RuntimeError) as error:return _failure(error)


def _fits(entries,limit):
    return len(json.dumps({'entries':entries},ensure_ascii=True,separators=(',',':')).encode())<=limit-8192


def resource_index(files,params,*,response_limit=16777216):
    entries={path:_describe(files,path) for path in _paths(params['paths'])}
    directories=params['directories']
    if not isinstance(directories,list) or len(directories)>8:raise SandboxError('env_protocol_mismatch')
    for item in directories:
        original=set(entries)
        path,leaf=item['path'],item['leaf']
        _paths([path])
        if not isinstance(leaf,str) or not leaf or '/' in leaf or '\\' in leaf or leaf in ('.','..') or len(leaf)>255:
            raise SandboxError('env_protocol_mismatch')
        value=entries.setdefault(path,_describe(files,path))
        if 'error' in value or value['info']['kind']!='directory':continue
        try:
            with files._resource(value['resolved']) as target:
                if files.workspace.public(target.relative)!=value['resolved']:raise OSError('Resource changed')
                names=[]
                with os.scandir(target.fd) as directory:
                    for child in directory:
                        names.append(child.name)
                        if len(names)>MAX_DIRECTORY_ENTRIES:break
            if len(names)>MAX_DIRECTORY_ENTRIES:
                value['children_error']='resource_limit'
                logger.warning('resource_directory_limit_reached')
                continue
            value['children']=[str(PurePosixPath(value['resolved'])/name) for name in sorted(names)]
            for child in value['children']:
                info=entries.setdefault(child,_describe(files,child))
                if 'error' not in info and info['info']['kind']=='directory':
                    supplied=str(PurePosixPath(info['resolved'])/leaf)
                    entries.setdefault(supplied,_describe(files,supplied))
        except (OSError,ValueError,RuntimeError) as error:
            value['children_error']=_failure(error)['error']
        if not _fits(entries,response_limit):
            for added in set(entries)-original-{path}:del entries[added]
            value.pop('children',None);value['children_error']='resource_limit'
            logger.warning('resource_index_response_limit_reached')
    if not _fits(entries,response_limit):raise SandboxError('env_message_too_large')
    return {'entries':entries}


def resource_read_batch(files,params,*,response_limit=16777216):
    paths=_paths(params['paths']);maximum=params['max_bytes']
    if type(maximum) is not int or not 0<=maximum<=MAX_CONTENT_BYTES:raise SandboxError('env_message_too_large')
    entries={}
    for path in paths:
        try:
            with files._resource(path) as target:
                if files.workspace.public(target.relative)!=path:
                    entries[path]={'error':'resource_changed'}
                    logger.warning('resource_batch_target_changed')
                    continue
                if not stat.S_ISREG(target.info.st_mode):raise OSError('Resource is not a file')
                with os.fdopen(os.dup(target.fd),'rb') as source:content=source.read(maximum+1)
            entries[path]={'bytes_base64':base64.b64encode(content).decode()}
            if not _fits(entries,response_limit):
                entries[path]={'error':'resource_limit'}
                logger.warning('resource_content_response_limit_reached')
        except (OSError,ValueError,RuntimeError) as error:entries[path]=_failure(error)
    if not _fits(entries,response_limit):raise SandboxError('env_message_too_large')
    return {'entries':entries}
