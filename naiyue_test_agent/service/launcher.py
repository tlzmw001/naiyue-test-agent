"""Discover first; bounded concurrent startup leaves only one OS-lock owner."""
import asyncio
import logging
from naiyue_test_agent.diagnostics import safe_log, diagnostic_text
import os
import subprocess
import sys
import time
from threading import Thread

from naiyue_test_agent.storage.files import open_store_file
from .client import ServiceClient
from .ownership import _directory, service_running
from .settings import ServiceError

logger = logging.getLogger(__name__)


def _reap(child):
    try:
        child.wait()
    except OSError as error:
        safe_log(logger.error, 'service_child_reap_failed error_type=%s diagnostic=%s', type(error).__name__,
            diagnostic_text(error=error,phase='reap',pid=child.pid))


def _spawn(options):
    directory = _directory(options)
    fd = open_store_file(directory / 'service.log')
    args = [sys.executable, '-m', 'naiyue_test_agent.service', '--config', str(options.config_path),
        '--settings', str(options.settings_path), '--data-dir', str(options.data_dir),
        '--frontend-dist', str(options.frontend_dist), '--host', options.host, '--port', str(options.port),
        '--max-receipts', str(options.max_receipts), '--max-frame-bytes', str(options.max_frame_bytes),
        '--receipt-cache-bytes', str(options.receipt_cache_bytes)]
    flags = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
             if os.name == 'nt' else {'start_new_session': True})
    try:
        os.lseek(fd, 0, os.SEEK_END)
        with os.fdopen(fd, 'ab', buffering=0) as log:
            fd = None
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     close_fds=True, **flags)
            Thread(target=_reap, args=(child,), name='naiyue-service-reaper', daemon=True).start()
            return child
    finally:
        if fd is not None:
            os.close(fd)


async def ensure_service(options):
    deadline = time.monotonic() + options.startup_timeout
    child = None
    while time.monotonic() < deadline:
        if service_running(options):
            try:
                remaining = max(0.01, deadline - time.monotonic())
                limited = options.model_copy(update={'request_timeout': min(options.request_timeout, remaining)})
                return await ServiceClient.connect(limited)
            except ServiceError as error:
                if error.code not in ('service_unavailable', 'service_connection_failed'):
                    raise
        elif child is None:
            try:
                child = _spawn(options)
            except OSError as error:
                safe_log(logger.error, 'service_spawn_failed error_type=%s diagnostic=%s', type(error).__name__,
                    diagnostic_text(error=error,phase='spawn'))
                raise ServiceError('service_start_failed') from None
        elif child.poll() is not None:
            logger.error('service_start_failed exit_code=%s', child.returncode)
            raise ServiceError('service_start_failed')
        await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise ServiceError('service_start_timeout')
