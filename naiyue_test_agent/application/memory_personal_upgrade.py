"""Consistent private format1 snapshot before shared-store format2 migration."""

from contextlib import closing
import os
import sqlite3
from uuid import uuid4

from naiyue_test_agent.storage_files import open_store_file, sync_store_directory
from . import memory_schema


def backup_format1(path):
    # Caller holds BEGIN IMMEDIATE: another writer cannot change this DELETE-mode DB.
    # Backup must read through a separate connection, never the writer transaction itself.
    target=path.with_name(path.name+'.format1-'+uuid4().hex+'.sqlite')
    fd=open_store_file(target,exclusive_create=True)
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('memory_backup_invalid')
                memory_schema.check(destination,version=1)
                destination.commit()
        os.fsync(fd)
        sync_store_directory(target.parent)
    finally:
        os.close(fd)
    return target
