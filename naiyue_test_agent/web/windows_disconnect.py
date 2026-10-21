"""Complete the narrow CPython Proactor reset-during-close path on Windows."""

import asyncio
from contextlib import contextmanager
import inspect
import logging
import sys


logger = logging.getLogger(__name__)


def _finish_reset_close(context):
    from asyncio.proactor_events import _ProactorBasePipeTransport
    error = context.get('exception')
    callback = getattr(context.get('handle'), '_callback', None)
    transport = getattr(callback, '__self__', None)
    if (not isinstance(error, ConnectionResetError) or getattr(error, 'winerror', None) != 10054
            or error.__context__ is not None
            or not isinstance(transport, _ProactorBasePipeTransport)
            or getattr(callback, '__func__', None) is not _ProactorBasePipeTransport._call_connection_lost
            or not transport._closing or transport._called_connection_lost or transport._sock is None):
        return False
    try:
        # The original callback already notified the protocol. Resume only the
        # cleanup it did not reach after shutdown() raised; never notify twice.
        transport._sock.close()
        transport._sock = None
        if transport._server is not None:
            detach = transport._server._detach
            # Python 3.12 counts clients; 3.13+ tracks individual transports.
            if inspect.signature(detach).parameters:
                detach(transport)
            else:
                detach()
            transport._server = None
        transport._called_connection_lost = True
    except Exception as failure:
        logger.error('web_disconnect_cleanup_failed error_type=%s', type(failure).__name__)
        return False
    logger.warning('web_peer_disconnected phase=closing error_code=10054')
    return True


@contextmanager
def windows_disconnect_handler():
    if sys.platform != 'win32':
        yield
        return
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handle(current_loop, context):
        if _finish_reset_close(context):
            return
        if previous is not None:
            previous(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handle)
    try:
        yield
    finally:
        if loop.get_exception_handler() is handle:
            loop.set_exception_handler(previous)
