"""Reservations bound pending commands and errors remain repeatable."""
import asyncio

from naiyue_test_agent.service.receipts import CommandReceipts
from test_service_ownership import options


def test_pending_reservation_and_duplicate_share_one_operation(tmp_path):
    async def scenario():
        receipts = CommandReceipts(options(tmp_path, max_frame_bytes=1024, receipt_cache_bytes=1024))
        entered, release = asyncio.Event(), asyncio.Event()
        effects = []
        async def operation():
            effects.append(True)
            entered.set()
            await release.wait()
            return 201, [], b'{"ok":true}'
        first = asyncio.create_task(receipts.execute('one', 'same', operation))
        await asyncio.wait_for(entered.wait(), 5)
        assert receipts.reserved == 1024
        assert (await receipts.execute('two', 'other', operation))[0] == 409
        duplicate = asyncio.create_task(receipts.execute('one', 'same', operation))
        release.set()
        a, b = await asyncio.gather(first, duplicate)
        assert a == b == (201, [], b'{"ok":true}')
        assert effects == [True] and receipts.reserved == 0 and receipts.bytes == 11
    asyncio.run(scenario())


def test_failed_operation_releases_reservation_and_preserves_safe_result(tmp_path, caplog):
    async def scenario():
        receipts = CommandReceipts(options(tmp_path))
        effects = []
        async def operation():
            effects.append(True)
            raise OSError('PRIVATE_PROVIDER_INPUT')
        first = await receipts.execute('one', 'same', operation)
        assert await receipts.execute('one', 'same', operation) == first
        assert first[0] == 500 and b'PRIVATE_PROVIDER_INPUT' not in first[2]
        assert receipts.reserved == 0 and receipts.bytes == len(first[2])
        assert effects == [True]
        assert 'service_command_failed' in caplog.text and 'PRIVATE_PROVIDER_INPUT' not in caplog.text
    asyncio.run(scenario())


def test_confirmed_rejection_releases_id_even_after_http_caller_cancels(tmp_path):
    async def scenario():
        receipts = CommandReceipts(options(tmp_path))
        entered, release = asyncio.Event(), asyncio.Event()
        effects = []
        async def rejected():
            entered.set()
            await release.wait()
            return 409, [], b'{"error":{"code":"run_capacity_exceeded"}}'
        caller = asyncio.create_task(receipts.execute('one', 'old', rejected))
        await asyncio.wait_for(entered.wait(), 5)
        producer = receipts.entries['one'][1]
        caller.cancel()
        try:
            await caller
        except asyncio.CancelledError:
            pass
        release.set()
        await producer
        assert receipts.entries == receipts.scopes == {}
        assert receipts.reserved == receipts.bytes == 0
        async def accepted():
            effects.append(True)
            return 202, [], b'{"accepted":true}'
        first = await receipts.execute('one', 'new', accepted)
        assert await receipts.execute('one', 'new', accepted) == first
        assert effects == [True]
    asyncio.run(scenario())


def test_rejection_finishing_during_scope_cleanup_does_not_double_remove_or_charge(tmp_path):
    async def scenario():
        receipts = CommandReceipts(options(tmp_path))
        entered, release, cleanup_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def rejected():
            entered.set()
            await release.wait()
            return 409, [], b'{"error":{"code":"session_busy"}}'
        caller = asyncio.create_task(receipts.execute('one', 'old', rejected, scope_id='temp-test'))
        await asyncio.wait_for(entered.wait(), 5)
        # Signal the precise cleanup wait, independent of wall-clock scheduling.
        original_shield = asyncio.shield
        def shield(task):
            if asyncio.current_task().get_name() == 'scope-forget':
                cleanup_waiting.set()
            return original_shield(task)
        from unittest.mock import patch
        with patch('asyncio.shield', shield):
            cleanup = asyncio.create_task(receipts.forget_scope('temp-test'), name='scope-forget')
            await asyncio.wait_for(cleanup_waiting.wait(), 5)
            release.set()
            await asyncio.gather(caller, cleanup)
        assert receipts.entries == receipts.scopes == {}
        assert receipts.bytes == receipts.reserved == 0
    asyncio.run(scenario())


def test_scope_cleanup_cannot_remove_a_later_key_reused_by_another_scope(tmp_path):
    async def scenario():
        receipts = CommandReceipts(options(tmp_path))
        first_entered, second_entered = asyncio.Event(), asyncio.Event()
        first_release, second_release, cleanup_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        effects = []
        async def first():
            first_entered.set()
            await first_release.wait()
            return 202, [], b'{"first":true}'
        async def second():
            second_entered.set()
            await second_release.wait()
            return 409, [], b'{"error":{"code":"session_busy"}}'
        a = asyncio.create_task(receipts.execute('first', 'first', first, scope_id='temp-A'))
        await asyncio.wait_for(first_entered.wait(), 5)
        b = asyncio.create_task(receipts.execute('reused', 'old', second, scope_id='temp-A'))
        await asyncio.wait_for(second_entered.wait(), 5)
        original_shield = asyncio.shield
        def shield(task):
            if asyncio.current_task().get_name() == 'scope-forget': cleanup_waiting.set()
            return original_shield(task)
        from unittest.mock import patch
        with patch('asyncio.shield', shield):
            cleanup = asyncio.create_task(receipts.forget_scope('temp-A'), name='scope-forget')
            await asyncio.wait_for(cleanup_waiting.wait(), 5)
            second_release.set()
            await b
            async def accepted():
                effects.append(True)
                return 202, [], b'{"accepted":true}'
            cached = await receipts.execute('reused', 'new', accepted, scope_id='project-B')
            replacement = receipts.entries['reused'][1]
            first_release.set()
            await asyncio.gather(a, cleanup)
        assert receipts.entries['reused'][1] is replacement
        assert receipts.scopes == {'reused': 'project-B'}
        assert receipts.bytes == len(cached[2]) and receipts.reserved == 0
        assert await receipts.execute('reused', 'new', accepted, scope_id='project-B') == cached
        assert effects == [True]
    asyncio.run(scenario())


def test_image_lease_outlives_cancelled_caller_and_rejects_extra_work(tmp_path):
    from naiyue_test_agent.image_input_settings import ImageInputSettings
    from naiyue_test_agent.web.errors import WebError
    import pytest
    async def scenario():
        receipts=CommandReceipts(options(tmp_path), image_settings=ImageInputSettings(max_in_flight_requests=1))
        scope={'method':'POST','path':'/api/v1/scopes/chats/sessions/one/runs','headers':[(b'x-image-input',b'1')]}
        lease=receipts.images.reserve(scope)
        entered, release=asyncio.Event(),asyncio.Event()
        async def operation():
            entered.set(); await release.wait()
            return 202,[],b'{}'
        caller=asyncio.create_task(receipts.execute('image','hash',operation,image_lease=lease))
        await asyncio.wait_for(entered.wait(),5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError): await caller
        assert lease.transferred and receipts.images.active==1
        with pytest.raises(WebError,match='image_input_busy'): receipts.images.reserve(scope)
        release.set(); await receipts.entries['image'][1]
        assert lease.released and receipts.images.active==0
        lease.release()
        assert receipts.images.active==0
    asyncio.run(scenario())
