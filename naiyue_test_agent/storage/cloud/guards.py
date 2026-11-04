"""Called only while CloudOwner holds the OS lock, before opening any database."""
import os
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.storage.files import open_store_readonly,check_store_directory
from .schema import private_file


def read_guard(root,name):
    check_store_directory(root);path=root/name
    try:
        private_file(path)
        with os.fdopen(open_store_readonly(path),'rb') as stream:return parse_object(stream.read(16385),byte_limit=16384,depth_limit=8)
    except FileNotFoundError:return None


def check_guards(root,*,allow_restore=False,allow_gc=False):
    for name,code,allowed in (('restore-guard.json','cloud_restore_guard',allow_restore),('gc-guard.json','cloud_gc_guard',allow_gc)):
        value=read_guard(root,name)
        if value is not None and not allowed and value.get('phase') not in ('active','completed'):raise CloudError(code)
