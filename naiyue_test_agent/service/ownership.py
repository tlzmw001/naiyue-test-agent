"""A noninherited OS lock owns private, replaceable service discovery metadata."""
import logging
import os
import secrets
import stat
import tempfile
from uuid import uuid4

from naiyue_test_agent.storage.files import (acquire_store_lock, release_store_lock,
    open_store_file, open_store_readonly, check_store_directory, check_store_file, sync_store_directory)
from .settings import ServiceError, ServiceRecord

logger = logging.getLogger(__name__)


def _directory(options):
    path = options.data_dir / 'service'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    check_store_directory(path)
    if os.name != 'nt':
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise OSError('service_directory_not_private')
    return path


def read_discovery(options):
    path = options.data_dir / 'service/discovery.json'
    try:
        check_store_directory(path.parent)
        fd = open_store_readonly(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        logger.warning('service_discovery_read_failed error_type=%s', type(error).__name__)
        raise ServiceError('service_discovery_invalid') from None
    try:
        info = os.fstat(fd)
        if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('discovery_not_private')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError('discovery_too_large')
        return ServiceRecord.model_validate_json(content)
    except (OSError, ValueError) as error:
        logger.warning('service_discovery_invalid error_type=%s', type(error).__name__)
        raise ServiceError('service_discovery_invalid') from None
    finally:
        if fd is not None:
            os.close(fd)


def service_running(options):
    """Probe ownership, never infer it from a leftover discovery/lock file."""
    path = options.data_dir / 'service/owner.lock'
    try:
        check_store_directory(path.parent)
        if not path.exists():
            return False
        fd = open_store_file(path)
    except FileNotFoundError:
        return False
    except OSError as error:
        logger.warning('service_lock_probe_failed error_type=%s', type(error).__name__)
        raise ServiceError('service_storage_unavailable') from None
    try:
        try:
            acquire_store_lock(fd)
        except BlockingIOError:
            return True
        release_store_lock(fd)
        return False
    finally:
        os.close(fd)


class ServiceOwner:
    def __init__(self, options):
        self.options, self.fd, self.record = options, None, None
        self._published = False
        self._published_identity = None

    def __enter__(self):
        try:
            path = _directory(self.options) / 'owner.lock'
            self.fd = open_store_file(path)
            acquire_store_lock(self.fd)
        except OSError as error:
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError as failure:
                    logger.error('service_handle_close_failed error_type=%s', type(failure).__name__)
                finally:
                    self.fd = None
            if isinstance(error, BlockingIOError):
                raise ServiceError('service_in_use') from None
            logger.error('service_owner_open_failed error_type=%s', type(error).__name__)
            raise ServiceError('service_storage_unavailable') from None
        try:
            self.record = ServiceRecord(instance_id=uuid4().hex, identity=self.options.identity,
                origin=self.options.origin, access_token=secrets.token_urlsafe(32))
        except BaseException:
            try:
                os.close(self.fd)
            except OSError as failure:
                logger.error('service_handle_close_failed error_type=%s', type(failure).__name__)
            finally:
                self.fd = None
            raise
        return self

    def publish(self):
        if self.fd is None:
            raise ServiceError('service_not_owned')
        directory = _directory(self.options)
        destination = directory / 'discovery.json'
        temporary = None
        try:
            try:
                check_store_file(destination)
            except FileNotFoundError:
                pass
            fd, temporary = tempfile.mkstemp(prefix='.discovery-', dir=directory)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(self.record.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
            info = destination.lstat()
            self._published_identity = info.st_dev, info.st_ino
            self._published = True
            sync_store_directory(directory)
        except OSError as error:
            logger.error('service_discovery_publish_failed error_type=%s', type(error).__name__)
            raise ServiceError('service_storage_unavailable') from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError as error:
                    logger.error('service_discovery_temp_cleanup_failed error_type=%s', type(error).__name__)

    def __exit__(self, kind, error, traceback):
        cleanup = None
        try:
            if self._published:
                path = self.options.data_dir / 'service/discovery.json'
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    info = None
                if info is not None and (info.st_dev, info.st_ino) == self._published_identity:
                    check_store_file(path)
                    path.unlink()
                    sync_store_directory(self.options.data_dir / 'service')
                elif info is not None:
                    logger.warning('service_discovery_replaced_during_ownership')
        except (OSError, ServiceError) as failure:
            cleanup = failure
            logger.error('service_discovery_cleanup_failed error_type=%s', type(failure).__name__)
        finally:
            if self.fd is not None:
                try:
                    release_store_lock(self.fd)
                except BaseException as failure:
                    if cleanup is None:
                        cleanup = failure
                    logger.error('service_unlock_failed error_type=%s', type(failure).__name__)
                finally:
                    try:
                        os.close(self.fd)
                    except BaseException as failure:
                        if cleanup is None:
                            cleanup = failure
                        logger.error('service_handle_close_failed error_type=%s', type(failure).__name__)
                    finally:
                        self.fd = None
        if cleanup is not None and error is None:
            raise ServiceError('service_storage_unavailable') from cleanup
