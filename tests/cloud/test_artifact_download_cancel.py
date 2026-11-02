"""Response cancellation drains the physical read before closing its pinned FD."""
import asyncio
import threading
from types import SimpleNamespace

from naiyue_test_agent.application.cloud.artifact_download import ArtifactDownload
from naiyue_test_agent.application.cloud.work import cloud_work


def test_download_close_waits_for_cancelled_read_worker():
    from contextlib import nullcontext
    entered=threading.Event();release=threading.Event();closed=[]
    class Stream:
        def read(self,size):
            entered.set();assert release.wait(5)
            assert not closed
            return b'chunk'
        def close(self):closed.append(True)
    async def scenario():
        service=SimpleNamespace(runtime=SimpleNamespace(operation=nullcontext),limits=SimpleNamespace(io_bytes=8),downloads=set())
        lease=ArtifactDownload(service,'a'*32);lease.stream=Stream();lease.record=SimpleNamespace(size=5);service.downloads.add(lease)
        gen=lease.chunks();reading=asyncio.create_task(anext(gen))
        try:
            assert await asyncio.to_thread(entered.wait,5)
            reading.cancel();closing=asyncio.create_task(lease.aclose())
            await asyncio.sleep(0)
            assert not closed and not closing.done()
        finally:release.set()
        result=await asyncio.gather(reading,return_exceptions=True)
        assert isinstance(result[0],asyncio.CancelledError)
        await closing
        assert closed==[True] and lease.finished.is_set() and not service.downloads
    asyncio.run(scenario())
