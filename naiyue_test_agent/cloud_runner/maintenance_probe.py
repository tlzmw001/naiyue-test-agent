"""Import the actual fixed maintenance tools in an unmounted immutable image."""
import json,os
from .upgrade_backup import backup
from .restore_backup import restore


def report():
    return dict(maintenance_protocol=1,raw_backup=callable(backup),raw_restore=callable(restore),uid=os.geteuid(),gid=os.getegid())


if __name__=='__main__':print(json.dumps(report()))
