"""Real builtin IO reports stages without exposing file or image bodies."""

import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent import tools
from naiyue_test_agent.tools.permissions import execution_environment
from naiyue_test_agent.tools.processes import ProcessOutcome


@pytest.mark.parametrize('name', ['read', 'ls', 'find', 'grep', 'write', 'edit', 'bash', 'powershell'])
def test_each_builtin_reports_actual_work_with_one_configured_budget(monkeypatch, tmp_path, name):
    secret = 'PRIVATE_FILE_BODY'
    target = tmp_path / 'file.txt'
    target.write_text(secret + '\n')
    if name == 'powershell':
        from naiyue_test_agent.tools.powershell import PowerShellAdapter
        from naiyue_test_agent.tools.processes import HostProcessRunner
        monkeypatch.setattr(PowerShellAdapter, 'discover', lambda *_: '/synthetic/pwsh')
        async def run(self, request, consume, control=None):
            await consume('stdout', b'7.4.0' if '-EncodedCommand' not in request.argv else b'\x1b[31mSHELL_OUTPUT\x1b[0m\n')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        monkeypatch.setattr(HostProcessRunner, 'run', run)
    settings = AppSettings.model_validate({'progress': {'min_interval_milliseconds': 0, 'max_text_bytes': 6}})
    spec = next(item for item in tools.create_builtin_tools(settings.tools, process_settings=settings.process, shell_task_settings=settings.shell_tasks, progress_settings=settings.progress) if item.tool.name == name)
    args = {'read': {'path': target.name}, 'ls': {}, 'find': {'pattern': '*.txt'},
            'grep': {'pattern': 'PRIVATE', 'path': target.name},
            'write': {'path': target.name, 'content': 'PRIVATE_NEW_BODY'},
            'edit': {'path': target.name, 'edits': [{'oldText': secret, 'newText': 'PRIVATE_NEW_BODY'}]},
            'bash': {'command': "printf 'SHELL_OUTPUT\\n'"},
            'powershell': {'command': 'Write-Output SHELL_OUTPUT'}}[name]
    validated = spec.validate_input(args)
    kwargs = {key: getattr(validated, key) for key in spec.input_schema.model_fields}
    context = {'session_id': 's', 'workspace_root': tmp_path,
               '_permission_rules': settings.permissions, '_session_allow': []}
    subject = spec.permissions.prepare(kwargs, context)
    subject.update(args=kwargs, tool_name=name, path_field='path', execution_environment=execution_environment())
    context['_execution_permit'] = {'call_id': 'c', 'session_id': 's', 'agent_path': 'root',
                                  'batch_id': 'b', 'tool_name': name, 'subject': subject}
    reports = []
    runtime = SimpleNamespace(context=context, tool_call_id='c', stream_writer=reports.append)
    if spec.tool.coroutine:
        result = asyncio.run(spec.tool.coroutine(**kwargs, runtime=runtime))
    else:
        result = spec.tool.func(**kwargs, runtime=runtime)
    assert result and reports
    assert all(p['batch_id'] == 'b' and p['tool_name'] == name and p['tool_call_id'] == 'c' for p in reports)
    assert any(p['activity'] in ('running', 'settling') for p in reports)
    assert any(p['completed'] is not None and p['completed'] > 0 for p in reports)
    assert all(len(p['text'].encode()) <= 6 for p in reports)
    if name in ('bash', 'powershell'):
        assert any(p['phase'] == 'process_output' and p['text'] for p in reports)
        assert all('\x1b' not in p['text'] for p in reports)
    else:
        assert all(p['text'] == '' for p in reports)
    assert secret not in str(reports) and 'PRIVATE_NEW_BODY' not in str(reports)
    assert 'runtime' not in spec.input_schema.model_fields


def test_image_and_fuzzy_edit_have_specific_internal_stages(tmp_path):
    # Capture the real writer while reusing the file-tool permit setup.
    image = tmp_path / 'image.png'
    with Image.new('RGB', (10, 10), 'red') as frame:
        frame.save(image)
    specs = [tools.create_session_read_tool(), tools.create_session_edit_tool()]
    target = tmp_path / 'file.txt'
    target.write_text('“old”\n')
    for spec, args, expected in [(specs[0], {'path': image.name}, {'image_decode', 'image_resize', 'image_encode'}),
        (specs[1], {'path': target.name, 'edits': [{'oldText': '"old"', 'newText': 'new'}]},
         {'scan_exact', 'scan_normalized', 'temp_copy', 'diff', 'commit'})]:
        reports = []
        context = {'session_id': 's', 'workspace_root': tmp_path}
        subject = spec.permissions.prepare(args, context)
        subject.update(args=args, path_field='path', execution_environment=execution_environment())
        context['_execution_permit'] = {'call_id': 'c', 'session_id': 's', 'agent_path': 'root',
            'batch_id': 'b', 'tool_name': spec.tool.name, 'subject': subject}
        runtime = SimpleNamespace(context=context, tool_call_id='c', stream_writer=reports.append)
        values = spec.validate_input(args)
        spec.tool.func(**{key: getattr(values, key) for key in args}, runtime=runtime)
        assert expected <= {p['phase'] for p in reports}
        assert all(not p['text'] for p in reports)
