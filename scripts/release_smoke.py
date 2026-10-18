"""Runs in the installed environment, outside the checkout, without credentials."""

import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4


async def web_scenario(root):
    import httpx
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
    from langchain_core.outputs import ChatGenerationChunk
    from pydantic import Field
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.resources import frontend_directory
    from naiyue_test_agent.settings import AppSettings
    from naiyue_test_agent.web.app import create_web_app
    from naiyue_test_agent.web.settings import WebSettings

    class OfflineModel(BaseChatModel):
        replies: list = Field(default_factory=list)
        seen: list = Field(default_factory=list)

        @property
        def _llm_type(self):
            return 'installed-offline-smoke'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            raise AssertionError('Unexpected synchronous model request')

        async def _astream(self, messages, **kwargs):
            self.seen.append(list(messages))
            reply = self.replies.pop(0)
            yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content,
                tool_calls=reply.tool_calls, id=uuid4().hex,
                response_metadata={'finish_reason': 'tool_calls' if reply.tool_calls else 'stop'}))

    workspace = root / 'workspace'
    workspace.mkdir()
    (workspace / 'sample.py').write_text('needle installed\n', encoding='utf-8')
    outside = root / 'outside.txt'
    outside.write_text('approved outside content', encoding='utf-8')
    model = OfflineModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'find', 'args': {'pattern': '*.py'}, 'id': 'find-installed'},
        {'name': 'grep', 'args': {'pattern': 'needle', 'glob': '*.py'}, 'id': 'grep-installed'},
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'read-approved'},
    ]), AIMessage(content='installed-ok')])
    application.load_model_catalog = lambda _: ModelCatalog('offline', {'offline': model})
    settings = WebSettings(config_path=root / 'unused.json', database_path=root / 'sessions.sqlite',
        frontend_dist=frontend_directory(), workspace_root=workspace,
        runtime_settings=AppSettings(), access_token='offline-install-smoke', port=8123)
    origin = settings.origin
    headers = {'Authorization': 'Bearer offline-install-smoke', 'Origin': origin}
    app = create_web_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin,
                                    headers=headers) as client:
            assert (await client.get('/api/models', headers={'Authorization': ''})).status_code == 401
            page = await client.get('/')
            assert page.status_code == 200 and '<html' in page.text.lower()
            import re
            assets = re.findall(r'(?:src|href)="(/assets/[^"?]+)', page.text)
            assert assets
            for asset in assets:
                assert (await client.get(asset)).status_code == 200
            response = await client.post('/api/sessions', json={'model_id': 'offline'})
            assert response.status_code == 201, response.text
            session = response.json()['session_id']
            response = await client.post(f'/api/sessions/{session}/runs',
                json={'request_id': str(uuid4()), 'text': 'inspect installed tools'})
            assert response.status_code == 202, response.text
            first_run = response.json()['run_id']
            events = await client.get(f'/api/runs/{first_run}/events')
            assert events.status_code == 200 and 'run_finished' in events.text
            view = (await client.get(f'/api/sessions/{session}')).json()
            batch, = view['approval_batches']
            assert len(model.seen) == 1
            response = await client.post(f'/api/sessions/{session}/approvals', json={
                'request_id': str(uuid4()), 'origin_run_id': batch['origin_run_id'],
                'approval_batch_id': batch['approval_batch_id'],
                'decisions': [{'approval_id': item['approval_id'], 'decision': 'approve'} for item in batch['items']],
            })
            assert response.status_code == 202, response.text
            resumed = response.json()['run_id']
            await client.get(f'/api/runs/{resumed}/events')
            assert (await client.get(f'/api/runs/{resumed}')).json()['status'] == 'completed'
            returned = {message.tool_call_id: message for message in model.seen[1] if isinstance(message, ToolMessage)}
            assert set(returned) == {'find-installed', 'grep-installed', 'read-approved'}
            assert all(message.status == 'success' for message in returned.values())
            assert 'sample.py' in returned['find-installed'].content
            assert 'needle installed' in returned['grep-installed'].content
            assert returned['read-approved'].content == 'approved outside content'
    reopened = create_web_app(settings)
    async with reopened.router.lifespan_context(reopened):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url=origin,
                                    headers=headers) as client:
            assert (await client.post(f'/api/sessions/{session}/prepare')).status_code == 200
            restored = (await client.get(f'/api/sessions/{session}')).json()
            assert restored['can_run'] and not restored['busy']
            assert any(message['content'] == 'installed-ok' for message in restored['messages'])
            assert len(model.seen) == 2, 'Reopen executed model/tool work'
    return workspace, model


def main():
    if sys.flags.optimize:
        raise RuntimeError('Smoke assertions must remain enabled')
    import naiyue_test_agent
    from naiyue_test_agent.resources import ASSETS, resolve_tool_binary

    root = Path.cwd()
    installed = Path(naiyue_test_agent.__file__).resolve()
    assert installed.is_relative_to(Path(sys.prefix).resolve()), 'Package came from checkout'
    assert not shutil.which('rg') and not shutil.which('fd'), 'Smoke PATH contains system tools'
    native = json.loads((ASSETS / 'native.json').read_text(encoding='utf-8'))
    for name, tool in native['tools'].items():
        binary = Path(resolve_tool_binary(name))
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == tool['sha256']
        version = subprocess.run([str(binary), '--version'], capture_output=True, text=True, check=True, timeout=10)
        assert tool['version'] in version.stdout
    workspace, _ = asyncio.run(web_scenario(root))
    from naiyue_test_agent.application import cli
    output = io.StringIO()
    with patch('builtins.input', return_value=':quit'), redirect_stdout(output):
        assert cli.main(['--workspace', str(workspace), '--no-session', '--config', 'unused']) == 0
    assert '当前模型' in output.getvalue()
    print(json.dumps({'target': native['target'], 'version': naiyue_test_agent.__version__,
        'installed_package': str(installed), 'native_tools': 'passed', 'frontend_api': 'passed',
        'find_grep': 'passed', 'approval_resume': 'passed', 'sqlite_reopen': 'passed', 'cli': 'passed',
        'provider_requests': 0}))


if __name__ == '__main__':
    main()
