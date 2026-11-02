"""S00 command quoting and cleanup ownership are hard boundaries."""
import asyncio
import json
import shlex
from types import SimpleNamespace

import pytest


def test_command_text_roundtrips_shell_metacharacters_without_interpolation():
    from scripts.cloud_probe.runtime import command_text
    payload={'kind':'seed','marker':"value'; $(touch /tmp/never) `false`\n"}
    args=shlex.split(command_text(payload))
    assert args[:3]==['python','-I','-c']
    assert json.loads(args[-1])==payload
    assert 'def main(args)' in args[3]


class Sandbox:
    def __init__(self,owner):self.owner=owner;self.calls=[]
    async def get_info(self):return SimpleNamespace(metadata={'nyt-s00':self.owner})
    async def destroy(self):self.calls.append('destroy')
    async def close(self):self.calls.append('close')


def test_cleanup_only_destroys_proven_current_run_resources():
    from scripts.cloud_probe.runtime import close_owned
    sandbox=Sandbox('this-run')
    asyncio.run(close_owned(sandbox,'this-run'))
    assert sandbox.calls==['destroy']


def test_cleanup_closes_but_never_destroys_foreign_resource():
    from scripts.cloud_probe.checks import GateError
    from scripts.cloud_probe.runtime import close_owned
    sandbox=Sandbox('other-run')
    with pytest.raises(GateError,match='cleanup_ownership_mismatch'):
        asyncio.run(close_owned(sandbox,'this-run'))
    assert sandbox.calls==['close']


def test_failed_local_close_cannot_replace_ownership_failure(caplog):
    from scripts.cloud_probe.checks import GateError
    from scripts.cloud_probe.runtime import close_owned
    class Broken(Sandbox):
        async def close(self):raise OSError('private diagnostic')
    with pytest.raises(GateError,match='cleanup_ownership_mismatch'):
        asyncio.run(close_owned(Broken('other-run'),'this-run'))
    assert 's00_local_close_failed' in caplog.text
    assert 'private diagnostic' not in caplog.text
