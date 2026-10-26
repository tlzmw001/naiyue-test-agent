"""Files require an application-injected environment, not just a local path."""
import asyncio
from dataclasses import replace

from naiyue_test_agent.contracts.run_context import RunIdentity, RunScope, RunCapabilities, RuntimeContext
from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool, create_session_edit_tool
from tool_feedback_helpers import invoke_tool_feedback


def test_execution_path_without_file_capability_cannot_read(tmp_path):
    (tmp_path / 'private.txt').write_text('MUST_NOT_READ_WITHOUT_ENVIRONMENT')
    async def scenario():
        context = RuntimeContext(RunScope(RunIdentity('S', execution_root=ExecutionRoot(tmp_path))))
        result = await invoke_tool_feedback(create_session_read_tool(), {'path': 'private.txt'},
                                           call_id='read', context=context)
        assert result.status == 'error'
        assert 'MUST_NOT_READ_WITHOUT_ENVIRONMENT' not in str(result.content)
    asyncio.run(scenario())


def test_file_capability_from_another_session_is_rejected(tmp_path):
    from naiyue_test_agent.execution.host_files import HostFileSystem
    (tmp_path / 'private.txt').write_text('MUST_NOT_READ_FROM_ANOTHER_SESSION')
    async def scenario():
        files = HostFileSystem(ExecutionRoot(tmp_path), None, session_id='owner')
        scope = RunScope(RunIdentity('other', execution_root=ExecutionRoot(tmp_path)),
                         capabilities=RunCapabilities(files=files), environment=files.environment)
        result = await invoke_tool_feedback(create_session_read_tool(), {'path': 'private.txt'},
                                           call_id='read', context=RuntimeContext(scope))
        assert result.status == 'error'
        assert 'MUST_NOT_READ_FROM_ANOTHER_SESSION' not in str(result.content)
    asyncio.run(scenario())


def test_real_host_write_edit_read_use_one_frozen_environment(tmp_path):
    from naiyue_test_agent.execution.host_files import HostFileSystem
    async def scenario():
        files = HostFileSystem(ExecutionRoot(tmp_path), None, session_id='S')
        scope = RunScope(RunIdentity('S', execution_root=ExecutionRoot(tmp_path)),
                         capabilities=RunCapabilities(files=files), environment=files.environment)
        context = RuntimeContext(scope)
        calls = [(create_session_write_tool(), {'path': 'note.txt', 'content': 'before'}),
                 (create_session_edit_tool(), {'path': 'note.txt', 'edits': [{'oldText': 'before', 'newText': 'after'}]}),
                 (create_session_read_tool(), {'path': 'note.txt'})]
        for index, (tool, args) in enumerate(calls):
            result = await invoke_tool_feedback(tool, args, call_id=str(index), context=context)
            assert result.status == 'success', result.content
        assert result.content == 'after' and (tmp_path / 'note.txt').read_text() == 'after'
        # A changed target descriptor must not borrow an existing capability.
        changed = replace(scope, environment=replace(files.environment, binding_id='different'))
        denied = await invoke_tool_feedback(create_session_read_tool(), {'path': 'note.txt'},
            call_id='changed', context=RuntimeContext(changed))
        assert denied.status == 'error'
    asyncio.run(scenario())


def test_project_resources_use_file_port_without_local_path_io():
    from naiyue_test_agent.contracts.environment import EnvironmentRef, FileResource
    from naiyue_test_agent.application.sessions.skills import prepare_run_resources
    root = '/virtual/project'
    content = {root + '/AGENTS.md': b'project rule',
        root + '/.agents/skills/check/SKILL.md': b'---\nname: check\ndescription: inspect code\n---\nbody'}
    directories = {root, root + '/.agents', root + '/.agents/skills', root + '/.agents/skills/check'}
    class Files:
        environment = EnvironmentRef('virtual-binding', 'sandbox', None, root, 'linux')
        checks = 0
        def validate(self): self.checks += 1
        def resource_info(self, path):
            return FileResource(path in content or path in directories, False,
                'file' if path in content else 'directory' if path in directories else 'missing')
        def resolve_resource(self, path): return path
        def list_resources(self, path):
            return tuple(item for item in content.keys() | directories if item.startswith(path + '/') and '/' not in item[len(path)+1:])
        def read_resource(self, path, max_bytes): return content[path][:max_bytes + 1]
    files = Files()
    result = prepare_run_resources(files)
    assert files.checks > 0
    assert 'project rule' in result['project_instructions']
    assert [part['id'] for part in result['prompt_parts']] == ['project', 'skills']
    assert '/virtual/project/.agents/skills/check/SKILL.md' in result['prompt_parts'][1]['text']
    assert tuple(map(str, result['read_resource_roots'])) == (root + '/.agents/skills/check',)


def test_non_directory_skill_source_does_not_block_project_run_resources(tmp_path):
    from naiyue_test_agent.execution.host_files import HostFileSystem
    from naiyue_test_agent.application.sessions.skills import prepare_run_resources
    (tmp_path / 'AGENTS.md').write_text('PROJECT_RULE')
    for name in ('.agents', '.claude'):
        (tmp_path / name).write_text('ordinary file, not a skills directory')
    files = HostFileSystem(ExecutionRoot(tmp_path), None)
    resources = prepare_run_resources(files)
    assert 'PROJECT_RULE' in resources['project_instructions']
    assert resources['read_resource_roots'] == ()
    assert [part['id'] for part in resources['prompt_parts']] == ['project']



def test_registered_run_and_restored_run_use_same_environment_binding(monkeypatch, tmp_path):
    from test_project_admission import opened
    from test_subagent_execution import ChildModel
    from test_async_application import call
    from langchain_core.messages import AIMessage, ToolMessage
    async def scenario():
        model = ChildModel(replies=[call('read', {'path': 'doc.txt'}, 'first'), AIMessage(content='one'),
                                    call('read', {'path': 'doc.txt'}, 'second'), AIMessage(content='two')])
        async with opened(monkeypatch, tmp_path, model, tools=[create_session_read_tool()]) as (app, project, host, sid):
            (project.source.path / 'doc.txt').write_text('REGISTERED_CONTENT')
            result = await host.runs.start(sid, [('user', 'first')])
            assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).content == 'REGISTERED_CONTENT'
            first = model.services[0]
            assert first.environment.binding_id == 'host:project:' + project.project_id
            assert first.environment.root == str(project.source.path)
            assert first.environment.project_revision == project.revision
            assert first.capabilities.files.owner_session_id == sid
            await app.projects.close(project.project_id)
            restored = await app.projects.open(project.project_id)
            await restored.sessions.restore(sid)
            result = await restored.runs.start(sid, [('user', 'second')])
            assert [m for m in result['messages'] if isinstance(m, ToolMessage)][-1].content == 'REGISTERED_CONTENT'
            second = model.services[2]
            assert second.environment == first.environment
            assert second.capabilities.files is not first.capabilities.files
            assert second.capabilities.files.owner_session_id == sid
    asyncio.run(scenario())
