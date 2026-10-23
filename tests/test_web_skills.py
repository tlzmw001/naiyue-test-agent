"""The Skill picker lists metadata without running or loading Skill bodies."""

import asyncio
import logging

from langchain_core.messages import AIMessage

from skill_helpers import write_skill
from test_async_application import StreamingToolModel
from test_web_api import request, web_client
from test_web_tools import calls


def test_skills_catalog_is_authenticated_metadata_only_and_refreshes(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        write_skill(root, name='case-review', description='Review cases', body='PRIVATE_BODY')
        write_skill(root, source='claude', name='case-review')
        write_skill(root, name='compact')
        write_skill(root, name='case-review', directory='zz-duplicate')
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            endpoint = '/api/skills'
            assert (await client.get(endpoint, headers={'Authorization': ''})).status_code == 401
            assert (await client.get(endpoint, headers={'Origin': 'https://evil.example'})).status_code == 403
            response = await client.get(endpoint)
            assert response.status_code == 200
            value = response.json()
            assert value == {'entries': [
                {'source_id': 'agents', 'name': 'case-review', 'description': 'Review cases', 'command': '/case-review'},
                {'source_id': 'agents', 'name': 'compact', 'description': 'Use for a controlled test task.', 'command': '/agents:compact'},
                {'source_id': 'claude', 'name': 'case-review', 'description': 'Use for a controlled test task.', 'command': '/claude:case-review'},
            ], 'default_source': 'agents', 'diagnostics': [{'source_id': 'agents', 'reason': 'name_collision'}],
                'read_registered': True}
            assert 'PRIVATE_BODY' not in response.text and str(root) not in response.text
            assert (await client.get('/api/sessions')).json() == [] and model.seen == []
            write_skill(root, name='new-skill', description='New description')
            assert any(row['command'] == '/new-skill' for row in (await client.get(endpoint)).json()['entries'])
            other = tmp_path / 'other'
            write_skill(other, name='foreign-skill', body='FOREIGN_BODY')
            assert 'foreign-skill' not in (await client.get(endpoint, params={'workspace_root': str(other)})).text
            monkeypatch.setattr(app.state.service.application.tools, 'list', lambda: ())
            unavailable = (await client.get(endpoint)).json()
            assert unavailable['read_registered'] is False
            assert {row['command'] for row in unavailable['entries']} == {
                '/case-review', '/agents:compact', '/claude:case-review', '/new-skill'}
    asyncio.run(scenario())


def test_empty_catalog_and_discovery_failure_are_safe(monkeypatch, tmp_path, caplog):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
            assert (await client.get('/api/skills')).json() == {
                'entries': [], 'default_source': None, 'diagnostics': [], 'read_registered': True}

            async def fail(_root):
                raise OSError('PRIVATE_PATH_AND_BODY')

            monkeypatch.setattr(app.state.service.application.sessions, 'skills', fail)
            with caplog.at_level(logging.ERROR):
                response = await client.get('/api/skills')
            assert response.status_code == 503
            assert response.json() == {'error': {'code': 'skill_catalog_unavailable'}}
            assert 'skill_catalog_read_failed' in caplog.text
            assert 'PRIVATE_PATH_AND_BODY' not in response.text + caplog.text
    asyncio.run(scenario())


def test_skill_catalog_delivery_limit_is_enforced(monkeypatch, tmp_path):
    async def scenario():
        write_skill(tmp_path, name='large', description='x' * 1024)
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]),
                              workspace_root=tmp_path, max_frame_bytes=1024) as (client, app):
            response = await client.get('/api/skills')
            assert response.status_code == 409
            assert response.json() == {'error': {'code': 'view_too_large'}}
    asyncio.run(scenario())


def test_skill_commands_keep_source_identity_when_default_source_disappears(tmp_path):
    from naiyue_test_agent.application.sessions.skills import discover_project_skills

    primary = write_skill(tmp_path, name='case-review')
    write_skill(tmp_path, source='claude', name='case-review')
    before = discover_project_skills(tmp_path)
    selected = before.command_for(next(row for row in before.entries if row.source_id == 'agents'))
    assert selected == '/case-review'
    primary.unlink()
    after = discover_project_skills(tmp_path)
    assert after.default_source == 'claude'
    assert [after.command_for(row) for row in after.entries] == ['/claude:case-review']
    assert selected not in [after.command_for(row) for row in after.entries]


def test_explicit_skill_command_keeps_source_and_uses_normal_read_chain(monkeypatch, tmp_path):
    async def scenario():
        write_skill(tmp_path, name='case-review', body='OTHER_SOURCE_BODY')
        selected = write_skill(tmp_path, source='claude', name='case-review', body='SELECTED_BODY')
        model = StreamingToolModel(replies=[calls(('read', {'path': str(selected)}, 'skill-read')), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path) as (client, app):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            text = '/claude:case-review inspect failing tests\nwith details'
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request(text))).json()['run_id']
            await client.get(f'/api/runs/{run}/events')
            first = '\n'.join(str(m.content) for m in model.seen[0] if m.type == 'system')
            assert '<command>/case-review</command>' in first
            assert '<command>/claude:case-review</command>' in first
            assert 'explicitly selects' in first and 'SELECTED_BODY' not in first
            assert next(m.content for m in model.seen[0] if m.type == 'human') == text
            output = next(m for m in model.seen[1] if m.type == 'tool')
            assert output.tool_call_id == 'skill-read' and output.status == 'success'
            assert 'SELECTED_BODY' in output.content and 'OTHER_SOURCE_BODY' not in output.content
    asyncio.run(scenario())
