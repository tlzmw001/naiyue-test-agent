"""Exercise the configured model through the real workspace tool chain."""

import asyncio
from contextlib import AsyncExitStack

import argparse

from naiyue_test_agent.config_paths import get_models_path
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.tools import (
    create_session_bash_tool, create_session_edit_tool, create_session_find_tool,
    create_session_grep_tool, create_session_ls_tool, create_session_read_tool,
    create_session_write_tool,
)


SAFE_COMMAND = 'printf TOOL_PROBE_OK'
EXPECTED_DRAFT = 'status=reviewed\nmarker=CASE_PROBE\n'


def _trace(label: str, messages: list, results: list[ToolMessage]) -> None:
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                detail = f' args={call["args"]!r}' if call['name'] == 'read' else ''
                print(f'{label} call={call["name"]} id={call["id"]}{detail}')
        elif isinstance(message, ToolMessage):
            results.append(message)
            print(f'{label} result={message.name} id={message.tool_call_id} '
                  f'status={message.status}')


def _check_results(label: str, messages: list, required: set[str]) -> None:
    calls = [call for message in messages if isinstance(message, AIMessage)
             for call in message.tool_calls]
    results = [message for message in messages if isinstance(message, ToolMessage)]
    if not required.issubset({call['name'] for call in calls}):
        raise AssertionError(f'{label}: required tool calls missing')
    by_id = {message.tool_call_id: message for message in results}
    if (len(by_id) != len(results)
            or set(by_id) != {call['id'] for call in calls} or any(
        call['id'] not in by_id or by_id[call['id']].status != 'success'
        for call in calls
    )):
        raise AssertionError(f'{label}: tool call/result pairing or status failed')


def main():
    return asyncio.run(_async_main())

async def _async_main() -> int:
    async with AsyncExitStack() as resources:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--config', default=str(get_models_path()))
        parser.add_argument('--model-id')
        args = parser.parse_args()

        # Keep this local diagnostic out of hosted tracing regardless of shell settings.
        os.environ['LANGSMITH_TRACING'] = 'false'
        os.environ['LANGCHAIN_TRACING_V2'] = 'false'

        with TemporaryDirectory(prefix='naiyue-tool-closeout-') as directory:
            root = Path(directory)
            (root / 'contract.md').write_text('CASE_PROBE\nstatus=unreviewed\n', encoding='utf-8')
            tools = [create_session_read_tool(), create_session_ls_tool(),
                     create_session_find_tool(), create_session_grep_tool(),
                     create_session_write_tool(), create_session_edit_tool(),
                     create_session_bash_tool()]
            app = await build_async_local_application(
                args.config, tools=tools,
                system_prompt=('This is a controlled tool integration check. Use the requested '
                               'tools on files inside the selected workspace. Do not access '
                               'outside files or use bash unless explicitly requested. '
                               'Do not infer tool results.'),
                response_format=None,
            )
            resources.push_async_callback(app.aclose)
            model_id = args.model_id or app.default_model_id
            session = await app.acreate_workspace_session(model_id, root)
            print(f'model_id={model_id}')
            results: list[ToolMessage] = []

            first = await app.arun(session, [('user',
                "Use ls on '.', find with pattern '*.md', grep for literal CASE_PROBE "
                "in '.', and read contract.md. Actually call all four tools; wait for "
                'their results, then briefly report the marker.')])
            if first.get('approval_requests'):
                raise AssertionError('discovery unexpectedly requested approval')
            first_messages = first['messages']
            _trace('discovery', first_messages, results)
            _check_results('discovery', first_messages,
                           {'ls', 'find', 'grep', 'read'})
            for name, expected in (('ls', 'contract.md'), ('find', 'contract.md'),
                                   ('grep', 'CASE_PROBE')):
                if not any(message.name == name and expected in str(message.content)
                           for message in results):
                    raise AssertionError(f'discovery {name} omitted expected fixture')
            if not any(message.name == 'read' and 'CASE_PROBE' in str(message.content)
                       for message in results):
                raise AssertionError('discovery did not read the fixture')

            second = await app.arun(session, [('user',
                "Use write to create draft.md with exactly these two lines and a final newline: "
                "status=unreviewed, then marker=CASE_PROBE. Then use edit on draft.md "
                "to replace the exact text 'status=unreviewed' with 'status=reviewed'. "
                'Finally use read on draft.md. Actually call all three tools in this order.')])
            if second.get('approval_requests'):
                raise AssertionError('workspace mutation unexpectedly requested approval')
            second_messages = second['messages'][len(first_messages):]
            _trace('mutation', second_messages, results)
            _check_results('mutation', second_messages,
                           {'write', 'edit', 'read'})
            if (root / 'draft.md').read_text(encoding='utf-8') != EXPECTED_DRAFT:
                raise AssertionError('final draft content differs from expected bytes')
            acceptable_read_results = {
                EXPECTED_DRAFT,
                EXPECTED_DRAFT + '[read: lines 1-2; next_offset=none]',
            }
            if not any(message.name == 'read'
                       and str(message.content) in acceptable_read_results
                       for message in results):
                raise AssertionError('model did not read the final draft content')

            pending = await app.arun(session, [('user',
                f'Call bash exactly once with command {SAFE_COMMAND!r} and timeout 10. '
                'Wait for the tool result and report the stdout.')])
            pending_messages = pending['messages'][len(second['messages']):]
            _trace('approval', pending_messages, results)
            approvals = pending.get('approval_requests', [])
            if (len(approvals) != 1 or approvals[0]['tool_name'] != 'bash'
                    or approvals[0]['target'] != SAFE_COMMAND
                    or approvals[0]['workspace'] != str(root.resolve())):
                raise AssertionError('unexpected command or approval boundary; nothing approved')
            if any(isinstance(message, ToolMessage) and message.name == 'bash'
                   for message in pending_messages):
                raise AssertionError('bash returned a result before approval')
            print('approval=exact_safe_command_only')

            resumed = await app.aresume_tool_approval(session, ['approve'])
            if resumed.get('approval_requests'):
                raise AssertionError('additional command unexpectedly requested approval')
            resumed_messages = resumed['messages'][resumed['resume_start_index']:]
            _trace('resumed', resumed_messages, results)
            bash_calls = [call for message in pending_messages
                          if isinstance(message, AIMessage)
                          for call in message.tool_calls if call['name'] == 'bash']
            bash_results = [message for message in resumed_messages
                            if isinstance(message, ToolMessage) and message.name == 'bash']
            if (len(bash_calls) != 1 or len(bash_results) != 1
                    or bash_results[0].tool_call_id != bash_calls[0]['id']
                    or bash_results[0].status != 'success'
                    or 'exit_code=0' not in str(bash_results[0].content)
                    or 'TOOL_PROBE_OK' not in str(bash_results[0].content)):
                raise AssertionError('approved bash tool result failed')
            if not isinstance(resumed['messages'][-1], AIMessage):
                raise AssertionError('model did not receive bash result and finish')
            print('status=PASS tools=ls,find,grep,read,write,edit,bash '
                  'approval=paused_then_resumed final_file=verified')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
