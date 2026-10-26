"""Folder browsing reads metadata; confirmation keeps the observed directory identity."""
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from test_project_navigation import directory_client
from test_service_protocol import command


def test_browse_any_folder_without_project_database_or_model(monkeypatch, tmp_path):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, calls):
            root = tmp_path / 'outside-default'; root.mkdir()
            for name in ('b', 'A', '.hidden'): (root / name).mkdir()
            (root / 'file.txt').write_text('not a directory')
            response = await client.get('/api/v1/directories', params={'path': str(root), 'limit': 1})
            assert response.status_code == 200
            page = response.json()
            assert page['path'] == str(root) and page['parent'] == str(tmp_path)
            assert len(page['identity']) == 64 and page['items'] == [{'name': 'A', 'path': str(root / 'A'), 'symlink': False}]
            assert page['next_offset'] == 1
            rest = (await client.get('/api/v1/directories', params={'path': str(root), 'offset': 1})).json()
            assert [row['name'] for row in rest['items']] == ['b']
            hidden = (await client.get('/api/v1/directories', params={'path': str(root), 'show_hidden': 'true'})).json()
            assert [row['name'] for row in hidden['items']] == ['.hidden', 'A', 'b']
            assert calls == [] and app.state.scopes.services == {}
            assert not list(app.state.application.projects._service.data_dir.rglob('*.sqlite'))
            assert (await client.get('/api/v1/directories', headers={'Authorization': ''})).status_code == 401
            assert (await client.get('/api/v1/directories', headers={'Host': 'evil.example'})).status_code == 403
            assert (await client.get('/api/v1/directories', headers={'X-Service-Instance': '0' * 32})).status_code == 409
    asyncio.run(scenario())


@pytest.mark.parametrize('path,code', [('relative/path', 'project_invalid_path'), ('missing', 'project_directory_missing'), ('file', 'project_directory_not_directory')])
def test_explicit_invalid_location_never_falls_back(monkeypatch, tmp_path, path, code):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, _, _):
            (tmp_path / 'file').write_text('file')
            target = path if path.startswith('relative') else str(tmp_path / path)
            response = await client.get('/api/v1/directories', params={'path': target})
            assert response.status_code == 409 and response.json()['error']['code'] == code
    asyncio.run(scenario())


def test_missing_default_is_reported_and_does_not_restrict_other_locations(monkeypatch, tmp_path):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, _):
            client.headers['Origin'] = str(client.base_url).rstrip('/')
            from naiyue_test_agent.project_settings import ProjectsSettings
            service = app.state.application.projects._service
            service.settings = ProjectsSettings(default_parent_dir=tmp_path / 'gone')
            monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
            page = (await client.get('/api/v1/directories')).json()
            assert page['path'] == str(tmp_path)
            assert page['default_error'] == 'project_directory_missing'
            assert page['default_parent_dir'] == str(tmp_path / 'gone')
            assert (await client.get('/api/v1/directories', params={'path': str(tmp_path.parent)})).status_code == 200
    asyncio.run(scenario())


@pytest.mark.parametrize('create', [False, True])
def test_confirmation_refuses_replaced_directory_before_any_registration(monkeypatch, tmp_path, create):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, _):
            client.headers['Origin'] = str(client.base_url).rstrip('/')
            folder = tmp_path / 'chosen'; folder.mkdir()
            identity = (await client.get('/api/v1/directories', params={'path': str(folder)})).json()['identity']
            folder.rename(tmp_path / 'old'); folder.mkdir()
            body = {'parent': str(folder), 'folder_name': 'new'} if create else {'root': str(folder)}
            response = await client.post('/api/v1/projects/' + ('create' if create else 'local'),
                json={**body, 'trusted': True, 'expected_directory': identity}, headers=command())
            assert response.status_code == 409 and response.json()['error']['code'] == 'project_location_changed'
            assert await app.state.application.projects.list() == ()
            assert not (folder / 'new').exists()
    asyncio.run(scenario())


def test_create_replay_and_explicit_targeted_reconciliation_do_not_repeat_mkdir(monkeypatch, tmp_path):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, _):
            client.headers['Origin'] = str(client.base_url).rstrip('/')
            parent = tmp_path / 'parent'; parent.mkdir()
            identity = (await client.get('/api/v1/directories', params={'path': str(parent)})).json()['identity']
            body = {'parent': str(parent), 'folder_name': 'created', 'trusted': True, 'expected_directory': identity}
            headers = command()
            first = await client.post('/api/v1/projects/create', json=body, headers=headers)
            again = await client.post('/api/v1/projects/create', json=body, headers=headers)
            assert first.status_code == again.status_code == 201 and first.json() == again.json()
            root = first.json()['source']['path']
            observed = (await client.get('/api/v1/directories', params={'path': root})).json()['identity']
            existing = await client.post('/api/v1/projects/local', json={'root': root, 'trusted': True, 'expected_directory': observed}, headers=command())
            assert existing.json()['project_id'] == first.json()['project_id']
            reconciled = await client.post('/api/v1/projects/reconcile', json={'operation_id': headers['X-Request-ID']}, headers=command())
            assert reconciled.status_code == 200 and reconciled.json()['items'] == [{'operation_id': headers['X-Request-ID'], 'error': None}]
            assert len(list(parent.iterdir())) == 1
            assert len(await app.state.application.projects.list()) == 1
    asyncio.run(scenario())


def test_unreadable_directory_is_an_explicit_error(monkeypatch, tmp_path):
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, _, _):
            from naiyue_test_agent.application.projects import directories
            original = directories.os.scandir
            def denied(path):
                if Path(path) == tmp_path: raise PermissionError('private diagnostic body')
                return original(path)
            monkeypatch.setattr(directories.os, 'scandir', denied)
            response = await client.get('/api/v1/directories', params={'path': str(tmp_path)})
            assert response.status_code == 409 and response.json()['error']['code'] == 'project_directory_unreadable'
            assert 'private diagnostic body' not in response.text
    asyncio.run(scenario())


def test_directory_identity_uses_full_precision_and_canonical_location(tmp_path):
    from naiyue_test_agent.application.projects.locations import directory_identity
    base = {'device': 2**60, 'inode': 2**63 + 100}
    assert directory_identity(tmp_path, base) != directory_identity(tmp_path, {**base, 'inode': base['inode'] + 1})
    assert directory_identity(tmp_path, base) != directory_identity(tmp_path / 'other', base)


def test_targeted_reconcile_finishes_only_the_selected_pending_creation(monkeypatch, tmp_path):
    from contextlib import contextmanager
    async def scenario():
        async with directory_client(monkeypatch, tmp_path) as (client, app, _):
            client.headers['Origin'] = str(client.base_url).rstrip('/')
            from naiyue_test_agent.application.projects import operations
            original = operations.initialize_reserved_store
            @contextmanager
            def fail(*args, **kwargs):
                raise OSError('disk unavailable')
                yield
            monkeypatch.setattr(operations, 'initialize_reserved_store', fail)
            identity = (await client.get('/api/v1/directories', params={'path': str(tmp_path)})).json()['identity']
            ids = []
            for name in ('left', 'right'):
                headers = command(); ids.append(headers['X-Request-ID'])
                response = await client.post('/api/v1/projects/create', json={'parent': str(tmp_path), 'folder_name': name,
                    'trusted': True, 'expected_directory': identity}, headers=headers)
                assert response.status_code == 409
                assert (tmp_path / name).is_dir()
            before = {p.name: p.stat().st_ino for p in (tmp_path / 'left', tmp_path / 'right')}
            monkeypatch.setattr(operations, 'initialize_reserved_store', original)
            response = await client.post('/api/v1/projects/reconcile', json={'operation_id': ids[0]}, headers=command())
            assert response.json()['items'] == [{'operation_id': ids[0], 'error': None}]
            rows = {row.operation_id: row for row in await app.state.application.projects.operations()}
            assert rows[ids[0]].stage == 'done' and rows[ids[1]].stage == 'directory_created'
            assert before == {p.name: p.stat().st_ino for p in (tmp_path / 'left', tmp_path / 'right')}
            assert len(await app.state.application.projects.list()) == 1
    asyncio.run(scenario())
