"""Only inactive uncommitted transfer staging expires; published inputs stay owned."""
import asyncio
import time
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from cloud_helpers import settings
from test_input_formats import package
from test_input_import import transfer_request
from test_input_recovery import uploaded


def test_expiry_skips_active_and_committed_and_retains_receipts(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path,input_limits=InputLimits(staging_ttl_seconds=1.0))) as r:
        receipt=r.inputs.create(transfer_request(captured));ident=receipt.resource_id
        r.inputs.staging(ident).joinpath('archive').write_bytes(b'partial')
        committed=uploaded(r,captured);r.inputs.commit(committed.transfer_id)
        async def scenario():
            with r.inputs.exclusive(ident):
                assert await r.inputs.sweep_expired(now=time.time()+10)==[]
            result=await r.inputs.sweep_expired(now=time.time()+10)
            assert result==[ident] and r.inputs.transfer(ident).state=='cancelled'
            assert not r.inputs.root.joinpath('transfers',ident).exists()
            assert r.registry.get_command(receipt.command_id)==receipt
            assert await r.inputs.sweep_expired(now=time.time()+20)==[]
            assert r.inputs.get_input(captured.input_id).input_id==captured.input_id
            assert r.inputs.transfer(committed.transfer_id).state=='committed'
        asyncio.run(scenario())
