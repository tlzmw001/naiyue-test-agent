"""The destructive probe must remain confined to an explicit test sandbox."""
import asyncio
import pytest


def test_exercise_rejects_readonly_channel_before_any_submission():
    from scripts.sandbox_probe.exercise import exercise
    class Channel:
        ready={'owned':False}
        closed=False
        async def close(self):self.closed=True
        async def request(self,*_):raise AssertionError('must reject before IO')
    channel=Channel()
    async def connect():return channel
    with pytest.raises(ValueError,match='owned'):
        asyncio.run(exercise(connect,{}))
    assert channel.closed


def test_stop_requires_reaped_process_not_just_terminal_label():
    from scripts.sandbox_probe.exercise import confirmed_stop
    assert confirmed_stop({'state':'stopped','result':{'ok':True,'value':{'process_reaped':True}}})
    assert not confirmed_stop({'state':'stopped','result':{'ok':True,'value':{'process_reaped':False}}})
    assert not confirmed_stop({'state':'unknown','result':None})


def test_output_is_base64_decoded_and_bounded_without_recording_content():
    from scripts.sandbox_probe.exercise import output_bytes
    assert output_bytes({'bytes_base64':'b25jZQ=='})==b'once'
    with pytest.raises(ValueError):output_bytes({'bytes_base64':'?'})
