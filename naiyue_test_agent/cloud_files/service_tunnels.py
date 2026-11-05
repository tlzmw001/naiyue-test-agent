"""Owned OpenSSH reverse forwarding, separate from the control connection."""
import logging
import os
import socket
import subprocess
import threading
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


def windows_flags():return subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def reverse_argv(profile,endpoint,slot,target):
    # Match the control transport hardening; each slot has independent lifetime.
    host='['+target.host+']' if ':' in target.host else target.host
    return [profile.ssh_executable,'-v','-N','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none',
        '-o','LogLevel=DEBUG1','-o','ForwardAgent=no','-o','ForwardX11=no',
        '-o',f'ConnectTimeout={max(1,int(profile.connect_timeout_seconds))}',
        '-o',f'ServerAliveInterval={profile.keepalive_seconds}','-o',f'ServerAliveCountMax={profile.keepalive_count}',
        '-R',f'{endpoint.bind_host}:{endpoint.port(slot)}:{host}:{target.port}',profile.ssh_host]


def probe_target(target,timeout):
    try:
        with socket.create_connection((target.host,target.port),timeout=timeout):return dict(tcp_reachable=True)
    except OSError as error:
        logger.warning('cloud_target_probe_failed error_type=%s',type(error).__name__)
        raise CloudError('cloud_target_unreachable',503) from None


class ReverseProcess:
    def __init__(self,args,shutdown_seconds):
        self.shutdown_seconds=shutdown_seconds;self.acknowledged=threading.Event()
        bind,port,target=args[args.index('-R')+1].split(':',2)
        host,target_port=target.rsplit(':',1)
        host=host.strip('[]')
        self.expected_ack=f'debug1: remote forward success for: listen {bind}:{port}, connect {host}:{target_port}'.encode()
        try:self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,close_fds=True,creationflags=windows_flags())
        except OSError as error:
            logger.warning('cloud_reverse_start_failed error_type=%s',type(error).__name__)
            raise CloudError('cloud_ssh_start_failed',503) from None

        self.reader=threading.Thread(target=self._read_status,daemon=True,name='nyt-ssh-status')
        self.reader.start()

    def _read_status(self):
        try:
            while line:=self.process.stderr.readline(4096):
                if line.rstrip()==self.expected_ack:self.acknowledged.set()
        except (OSError,ValueError) as error:
            logger.warning('cloud_reverse_status_failed error_type=%s',type(error).__name__)

    @property
    def ready(self):return self.alive and self.acknowledged.is_set()

    @property
    def alive(self):return self.process.poll() is None

    def close(self):
        try:self._close()
        except OSError as error:
            logger.warning('cloud_reverse_stop_failed error_type=%s',type(error).__name__)
            raise CloudError('cloud_ssh_stop_failed',503) from None

    def _close(self):
        if self.alive:
            self.process.terminate()
            try:self.process.wait(timeout=self.shutdown_seconds)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=self.shutdown_seconds)
        self.reader.join(timeout=self.shutdown_seconds)
        if self.reader.is_alive():raise CloudError('cloud_ssh_stop_failed',503)
        self.process.stderr.close()
