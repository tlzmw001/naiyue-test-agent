"""Private SSH profile ownership, bounded handshake and no cloud task shutdown."""
import asyncio
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field,field_validator,model_validator
from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking,await_settled,run_blocking
from naiyue_test_agent.contracts.cloud_tasks import CloudRecord,CloudError
from .client import CloudClient,CloudConnection
from .configuration import read_settings

logger=logging.getLogger(__name__)
REQUIRED={'projects:create','commands:read','tasks:create','tasks:read','inputs:import','environment:commands'}


class CloudProfile(CloudRecord):
    network_id:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
    reconnect_seconds:float=Field(default=5.0,gt=0,le=300)
    profile_id:str=Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
    connection_file:Path
    template_ids:list[str]=Field(min_length=1,max_length=64)
    default_template:str=Field(min_length=1,max_length=128)
    tunnel:Literal['external','owned']='external'
    ssh_host:str|None=None
    ssh_executable:str=Field(default='ssh',min_length=1)
    connect_timeout_seconds:float=Field(default=15.0,gt=0,le=120)
    keepalive_seconds:int=Field(default=15,gt=0,le=300)
    keepalive_count:int=Field(default=3,gt=0,le=10)
    poll_seconds:float=Field(default=0.1,gt=0,le=5)
    shutdown_seconds:float=Field(default=5.0,gt=0,le=30)

    @field_validator('connection_file')
    @classmethod
    def private_path(cls,value):
        if not value.is_absolute():raise ValueError('absolute_connection_file_required')
        return value

    @model_validator(mode='after')
    def check(self):
        if self.default_template not in self.template_ids or len(set(self.template_ids))!=len(self.template_ids):
            raise ValueError('invalid_profile_templates')
        if any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',v) for v in self.template_ids):
            raise ValueError('invalid_profile_templates')
        if self.ssh_host is not None and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',self.ssh_host):
            raise ValueError('invalid_ssh_alias')
        if (self.tunnel=='owned' or self.network_id) and self.ssh_host is None:raise ValueError('ssh_alias_required')
        return self


class ProfileBook(CloudRecord):
    profiles:list[CloudProfile]=Field(default_factory=list,max_length=64)

    @model_validator(mode='after')
    def unique(self):
        if len({p.profile_id for p in self.profiles})!=len(self.profiles):raise ValueError('duplicate_cloud_profile')
        return self

    @classmethod
    def read(cls,path):return cls() if path is None else read_settings(path,cls)

    def require(self,profile_id):
        for profile in self.profiles:
            if profile.profile_id==profile_id:return profile
        raise CloudError('cloud_profile_not_found',404)

    def public(self):
        return [dict(profile_id=p.profile_id,template_ids=p.template_ids,default_template=p.default_template,
            tunnel=p.tunnel,network_id=p.network_id,connection='not_connected') for p in self.profiles]


def ssh_argv(profile,connection):
    local=urlsplit(connection.endpoint);remote=urlsplit('http://'+connection.owner_authority)
    if local.hostname!='127.0.0.1' or remote.hostname!='127.0.0.1':raise CloudError('cloud_ssh_ipv4_loopback_required',422)
    return [profile.ssh_executable,'-N','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none',
        '-o',f'ConnectTimeout={max(1,int(profile.connect_timeout_seconds))}',
        '-o',f'ServerAliveInterval={profile.keepalive_seconds}','-o',f'ServerAliveCountMax={profile.keepalive_count}',
        '-L',f'127.0.0.1:{local.port}:127.0.0.1:{remote.port}',profile.ssh_host]


class ConnectedProfile:
    def __init__(self,profile,client,process=None):
        self.profile,self.client,self.process=profile,client,process
        self.handshake=None

    @classmethod
    def open(cls,profile,*,transport=None):
        connection=read_settings(profile.connection_file,CloudConnection)
        value=cls(profile,CloudClient(connection,transport=transport))
        try:
            if profile.tunnel=='owned':
                args=ssh_argv(profile,connection);endpoint=urlsplit(connection.endpoint)
                with socket.socket() as probe:
                    try:probe.bind((endpoint.hostname,endpoint.port))
                    except OSError:raise CloudError('cloud_ssh_port_in_use',409) from None
                try:value.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                except OSError as error:
                    logger.warning('cloud_ssh_start_failed error_type=%s',type(error).__name__)
                    raise CloudError('cloud_ssh_start_failed',503) from None
            deadline=time.monotonic()+profile.connect_timeout_seconds
            while True:
                if value.process is not None and value.process.poll() is not None:
                    logger.warning('cloud_ssh_exited returncode=%s',value.process.returncode)
                    raise CloudError('cloud_ssh_exited',503)
                try:
                    value.check(timeout=min(connection.timeout_seconds,max(0.001,deadline-time.monotonic())));return value
                except CloudError as error:
                    if value.process is None or error.code!='cloud_connection_failed' or time.monotonic()>=deadline:raise
                    time.sleep(min(profile.poll_seconds,max(0,deadline-time.monotonic())))
        except BaseException:
            try:value.close()
            except BaseException as error:logger.error('cloud_connection_cleanup_failed error_type=%s',type(error).__name__)
            raise

    def check(self,expected_instance=None,*,timeout=None):
        raw=self.client.request('GET','/instance',timeout=timeout or self.profile.connect_timeout_seconds)
        if not isinstance(raw,dict):raise CloudError('cloud_protocol_incompatible',409)
        expected=expected_instance or self.client.settings.instance_id
        if raw.get('instance_id')!=expected:raise CloudError('cloud_instance_mismatch',409)
        capabilities=raw.get('capabilities')
        if (type(raw.get('protocol')) is not int or raw['protocol']!=1 or not isinstance(capabilities,list)
                or not all(isinstance(v,str) for v in capabilities) or not REQUIRED.issubset(capabilities)):
            raise CloudError('cloud_protocol_incompatible',409)
        self.handshake=raw

    def close(self):
        try:self.client.close()
        finally:
            process=self.process
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=self.profile.shutdown_seconds)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=self.profile.shutdown_seconds)
            self.process=None


class ProfileConnections:
    def __init__(self,book):
        self.book=book;self.connections={};self.opening={};self.closing=False;self.retired=[];self.borrowed={}

    async def get(self,profile_id,expected_instance=None):
        if self.closing:raise CloudError('cloud_client_closing',503)
        profile=self.book.require(profile_id)
        previous=self.connections.get(profile_id)
        if previous is not None and previous.process is not None and previous.process.poll() is not None:
            logger.warning('cloud_ssh_retired returncode=%s',previous.process.poll())
            # Other accepted readers may still own the HTTP client. Retire it at
            # shutdown rather than closing their shared resource underneath them.
            self.retired.append(self.connections.pop(profile_id))
            if not self.borrowed.get(previous):
                self.retired.remove(previous);await run_blocking(previous.close)
        if profile_id not in self.connections:
            if profile_id not in self.opening:
                async def open_one():
                    async def dispose(value):await run_blocking(value.close)
                    value=await acquire_blocking(ConnectedProfile.open,dispose,profile)
                    self.connections[profile_id]=value
                    return value
                self.opening[profile_id]=asyncio.create_task(open_one())
            flight=self.opening[profile_id]
            try:await await_settled(flight)
            finally:
                if flight.done() and self.opening.get(profile_id) is flight:del self.opening[profile_id]
        value=self.connections[profile_id]
        self.borrowed[value]=self.borrowed.get(value,0)+1
        try:await run_blocking(value.check,expected_instance)
        except BaseException:
            await self.release(value.client)
            raise
        return value.client

    async def release(self,client):
        values=(*tuple(self.connections.values()),*self.retired)
        for value in values:
            if value.client is not client:continue
            count=self.borrowed.get(value,0)
            if count<=1:self.borrowed.pop(value,None)
            else:self.borrowed[value]=count-1
            if count<=1 and value in self.retired:
                self.retired.remove(value);await run_blocking(value.close)
            return
        raise RuntimeError('Unknown cloud connection lease')

    async def close(self):
        self.closing=True;first=None
        for task in tuple(self.opening.values()):
            try:await await_settled(task)
            except BaseException as error:
                logger.error('cloud_connection_open_failed error_type=%s',type(error).__name__)
        for value in (*tuple(self.connections.values()),*self.retired):
            try:await run_blocking(value.close)
            except BaseException as error:
                logger.error('cloud_connection_close_failed error_type=%s',type(error).__name__)
                if first is None:first=error
        self.connections.clear()
        self.retired.clear()
        self.borrowed.clear()
        if first is not None:raise first
