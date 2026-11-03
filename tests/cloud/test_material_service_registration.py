"""The actual default service supplies material reading only to project Hosts."""
import asyncio
from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.service.app import create_service_app
from test_async_application import StreamingToolModel
from test_service_ownership import options


def test_default_service_keeps_material_tool_out_of_chat_and_temporary_hosts(monkeypatch,tmp_path):
    monkeypatch.setattr(application,'load_model_catalog',lambda _:ModelCatalog('A',{'A':StreamingToolModel(replies=[])}))
    assets=tmp_path/'assets';assets.mkdir();(assets/'index.html').write_text('<html>fixture</html>')
    async def scenario():
        web=create_service_app(options(tmp_path))
        async with web.router.lifespan_context(web):
            root=web.state.application;source=tmp_path/'project';source.mkdir()
            project=await root.projects.add_local(source,trusted=True,operation_id='material-project')
            host=await root.projects.open(project.project_id)
            assert 'cloud_result_read' in {spec.tool.name for spec in host.tools.list()}
            # This also assembles the graph with the actual registered tool.
            sid=await host.sessions.create('A')
            assert 'cloud_result_read' in (await host.sessions.status(sid))['active_tools']
            chat=await root.chats.open()
            temporary=await root.projects._hosts.open_temporary('material-temporary')
            for other in (chat,temporary):assert 'cloud_result_read' not in {s.tool.name for s in other.tools.list()}
    asyncio.run(scenario())
