"""The installed gate must exercise native persistent pipes, custody and readonly IO."""
import asyncio

import pytest


def test_native_sandbox_gate_runs_real_children_and_private_retirement(tmp_path):
    from scripts.release_sandbox_smoke import sandbox_native_scenario, verify_sandbox_native_report
    report=asyncio.run(sandbox_native_scenario(tmp_path))
    verify_sandbox_native_report(report)
    assert report['two_channels']=='passed'
    assert report['late_reply']=='passed'
    assert report['child_tree_cleanup']=='passed'
    assert report['readonly_retirement']=='passed'


@pytest.mark.parametrize('field',['two_channels','late_reply','child_tree_cleanup','readonly_retirement'])
def test_missing_or_failed_native_proof_is_rejected(field):
    from scripts.release_sandbox_smoke import verify_sandbox_native_report
    report=dict(two_channels='passed',late_reply='passed',child_tree_cleanup='passed',readonly_retirement='passed')
    report[field]='failed'
    with pytest.raises(ValueError):verify_sandbox_native_report(report)


def test_native_cleanup_does_not_replace_primary_failure(tmp_path,monkeypatch,caplog):
    from scripts.release_sandbox_smoke import sandbox_native_scenario
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    class PrimaryFailure(Exception):pass
    original=PrimaryFailure('PRIVATE_PRIMARY')
    class Channel:
        async def request(self,*args):raise original
        async def close(self):raise OSError('PRIVATE_CLEANUP')
    async def open_(*args,**kwargs):return Channel()
    monkeypatch.setattr(SandboxChannel,'open',open_)
    with pytest.raises(PrimaryFailure) as caught:asyncio.run(sandbox_native_scenario(tmp_path))
    assert caught.value is original
    assert 'native_channel_cleanup_failed' in caplog.text and 'PRIVATE' not in caplog.text


def test_never_sent_hold_cannot_be_reported_as_a_late_reply_pass(tmp_path,monkeypatch):
    from scripts.release_sandbox_smoke import sandbox_native_scenario
    from naiyue_test_agent.execution.sandbox_channel import SandboxChannel
    request=SandboxChannel.request
    async def not_sent(self,operation,*args,**kwargs):
        if operation=='hold':await asyncio.Event().wait()
        return await request(self,operation,*args,**kwargs)
    monkeypatch.setattr(SandboxChannel,'request',not_sent)
    with pytest.raises(TimeoutError):asyncio.run(sandbox_native_scenario(tmp_path))
