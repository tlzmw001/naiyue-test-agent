"""Private atomic receipts. Caller owns the process-wide output lock."""
import os
from naiyue_test_agent.cloud_install.store import read,write


class Journal:
    def __init__(self,path,initial):
        self.path=path
        self.value=read(path,16*1024*1024) if os.path.lexists(path) else initial
        self.value.setdefault('commands',{})
        self.save()

    def save(self):write(self.path,self.value)
