"""Explicit shared-service entry; existing nyt adapters migrate in later slices."""
import argparse
import logging
import os
from pathlib import Path
import socket

import uvicorn
from naiyue_test_agent.diagnostics import SafeDiagnosticFormatter

from naiyue_test_agent.config_paths import get_agent_dir, get_data_dir, get_models_path
from naiyue_test_agent.resources import frontend_directory
from naiyue_test_agent.settings import load_settings
from .app import create_service_app
from .settings import ServiceOptions
from .server import ServiceServer

logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=get_models_path())
    parser.add_argument('--settings', type=Path, default=get_agent_dir() / 'settings.toml')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--frontend-dist', type=Path)
    parser.add_argument('--host', default=os.environ.get('NAIYUE_WEB_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('NAIYUE_WEB_PORT', '0')))
    parser.add_argument('--max-receipts', type=int, default=ServiceOptions.model_fields['max_receipts'].default)
    parser.add_argument('--max-frame-bytes', type=int, default=ServiceOptions.model_fields['max_frame_bytes'].default)
    parser.add_argument('--receipt-cache-bytes', type=int, default=ServiceOptions.model_fields['receipt_cache_bytes'].default)
    args = parser.parse_args(argv)
    handler = logging.StreamHandler()
    handler.setFormatter(SafeDiagnosticFormatter())
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    try:
        runtime = load_settings(args.settings)
        options = ServiceOptions(data_dir=args.data_dir or get_data_dir(runtime.storage),
            config_path=args.config, settings_path=args.settings,
            frontend_dist=args.frontend_dist or frontend_directory(), host=args.host, port=args.port,
            max_receipts=args.max_receipts, max_frame_bytes=args.max_frame_bytes,
            receipt_cache_bytes=args.receipt_cache_bytes)
        family = socket.AF_INET6 if ':' in options.host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((options.host, options.port))
            listener.listen(128)
            options = options.model_copy(update={'port': listener.getsockname()[1]})
            app = create_service_app(options, shutdown=lambda: setattr(server, 'should_exit', True))
            # Uvicorn traceback logging may include raw provider/configuration exceptions.
            # Application boundaries and this entry log only safe codes and error types.
            server = ServiceServer(uvicorn.Config(app, host=options.host, port=options.port,
                workers=1, log_level='critical', access_log=False))
            try:
                server.run(sockets=[listener])
            except SystemExit:
                logger.error('service_startup_failed')
                return 1
            if not server.started:
                logger.error('service_startup_failed')
            return 0 if server.started else 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        logger.error('service_entry_failed error_type=%s', type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
