"""Default registration and owner startup must use the Wiki generation."""
import asyncio,time
from naiyue_test_agent.tools.memory import create_memory_tool
from naiyue_test_agent.tools.memory_wiki_actions import Record
from naiyue_test_agent.settings import AppSettings
from test_history_tool import build
from test_async_application import StreamingToolModel


def test_default_memory_schema_is_wiki():
    spec=create_memory_tool()
    text=str(spec.input_schema.model_json_schema())
    assert 'list_topics' in text and 'destination' in text and "'kind'" not in text


def test_default_host_opens_wiki_and_reopens(monkeypatch,tmp_path):
    async def run():
        model=StreamingToolModel(replies=[])
        settings=AppSettings.model_validate({'memory':{'auto_enabled':False}})
        for _ in range(2):
            async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_memory_tool()],settings=settings) as app:
                p=app.host.sessions._persistence
                assert p.storage.database.connection.execute('SELECT format_version FROM naiyue_memory_info').fetchone()[0]==5
                assert p._memory_jobs is not None
    asyncio.run(run())
    from naiyue_test_agent.storage.backup_schema import check_snapshot
    check_snapshot(tmp_path/'sessions.sqlite')


def test_git_project_identity_can_upgrade(tmp_path):
    from uuid import uuid4
    from naiyue_test_agent.storage.project_store import ProjectStore
    with ProjectStore(tmp_path/'git.sqlite') as store:
        store.sessions.bind_git_project(str(uuid4()))
        result=store.memory.prepare_wiki(now=time.time())
        assert result['generation_id']
