"""RAM persistence must not turn root or child execution into a project session."""
import asyncio
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.tools.history import create_history_tool
from naiyue_test_agent.tools.memory import create_memory_tool
from naiyue_test_agent.tools.permissions import ToolPermission
from test_async_application import call
from test_subagent_execution import ChildModel, role
from test_subagent_approval_resume import approval_notifications, next_approval, submit


@pytest.mark.parametrize('with_directory', [False, True])
@pytest.mark.parametrize('approval', [False, True])
def test_temporary_root_child_and_approval_resume_have_no_project_ports(monkeypatch, tmp_path, approval, with_directory):
    async def scenario():
        root = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'query'}, 'delegate'), AIMessage(content='root done')])
        child = ChildModel(replies=[call('history', {'action': 'list_sessions'}, 'h'),
                                   call('memory', {'action': 'list'}, 'm'), AIMessage(content='child done')])
        history = create_history_tool()
        if approval:
            history = replace(history, permissions=ToolPermission(default='ask'))
        settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')},
            'subagents': (role(tool_names=('history', 'memory')), )})
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        app = await build_async_local_application('unused', tools=[create_subagent_tool(), history, create_memory_tool()],
            initial_active_tools=['subagent'], system_prompt='', response_format=None, settings=settings)
        sid = (await app.sessions.create_workspace('A', tmp_path) if with_directory
               else await app.sessions.create('A'))
        notifications = approval_notifications(monkeypatch, app)
        task = asyncio.create_task(app.runs.start(sid, [('user', 'delegate')]))
        try:
            if approval:
                pending = await next_approval(app, sid, notifications)
                await submit(app, sid, pending)
            result = await task
            assert result['messages'][-1].content == 'root done'
            assert len(child.services) == 3 and len(root.services) == 2
            for scope in [*root.services, *child.services]:
                assert scope.identity.project_id is None
                assert scope.capabilities.history is None and scope.capabilities.memory is None
            child_messages = child.seen[-1]
            errors = [m.content for m in child_messages if m.type == 'tool']
            assert len(errors) == 2 and 'history_unavailable' in errors[0] and 'memory_unavailable' in errors[1]
            assert app.sessions._persistence.storage.subagents.get_subagent_references(sid)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
        assert not (tmp_path / '.naiyue').exists() and not (tmp_path / 'data' / 'personal').exists()
    asyncio.run(scenario())
