"""Startup only observes restore evidence; explicit restore owns its lifecycle."""

import logging
from pathlib import Path

from .files import check_store_directory


def check_restore_marker(database_path):
    directory = Path(database_path).with_name(Path(database_path).name + '.maintenance')
    try:
        try:
            directory.lstat()
        except FileNotFoundError:
            return
        check_store_directory(directory)
        try:
            (directory / 'restore.json').lstat()
        except FileNotFoundError:
            return
    except OSError as error:
        logging.getLogger(__name__).error('restore_guard_failed error_type=%s', type(error).__name__)
        raise ValueError('restore_incomplete') from error
    logging.getLogger(__name__).error('restore_incomplete')
    raise ValueError('restore_incomplete')
