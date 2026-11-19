"""The retired ticket route cannot serve bytes, and authenticated download remains covered separately."""
import asyncio
from uuid import uuid4

from test_manual_commands_routes import configured


def test_browser_ticket_routes_are_retired(monkeypatch,tmp_path):
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            fid,rid=map(str,(uuid4(),uuid4()))
            issue=f'{t.base}/cloud-tasks/{t.bid}/results/{rid}/files/{fid}/download-link'
            receipt=await t.client.post(issue,params=t.query,headers=t.headers,json={})
            assert receipt.status_code==404
            assert 'set-cookie' not in receipt.headers
            unknown=await t.client.get(t.base+'/cloud-downloads/'+str(uuid4()),headers={'Authorization':''})
            assert unknown.status_code==404
    asyncio.run(scenario())
