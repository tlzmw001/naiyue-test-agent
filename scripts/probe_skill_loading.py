"""Bounded, read-only live check of default and explicitly selected Skill sources."""

import argparse

from naiyue_test_agent.config_paths import get_models_path
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.application import build_local_application
from naiyue_test_agent.application.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.tools import create_session_read_tool


MARKERS = {'agents': 'SOURCE_AGENTS_PROBE', 'claude': 'SOURCE_CLAUDE_PROBE'}


def check_skill_trace(messages, root: Path, source: str) -> int:
    root = root.resolve()
    folder = root / f'.{source}/skills/source-check'
    required = {folder / 'SKILL.md', folder / 'references/source.txt'}
    calls = [call for msg in messages if isinstance(msg, AIMessage) for call in msg.tool_calls]
    results = [msg for msg in messages if isinstance(msg, ToolMessage)]
    by_id = {msg.tool_call_id: msg for msg in results}
    if (len(by_id) != len(results) or set(by_id) != {call['id'] for call in calls}
            or any(msg.status != 'success' for msg in results)):
        raise AssertionError('Tool result pairing or status failed')
    observed = set()
    for call in calls:
        if call['name'] != 'read' or not isinstance(call['args'].get('path'), str):
            raise AssertionError('Unexpected tool or argument')
        path = Path(call['args']['path'])
        path = (path if path.is_absolute() else root / path).resolve()
        if path not in required:
            raise AssertionError('Unexpected source or resource')
        observed.add(path)
        if path.name == 'source.txt' and MARKERS[source] not in str(by_id[call['id']].content):
            raise AssertionError('Reference observation missing')
    if not required.issubset(observed):
        raise AssertionError('Required Skill reads missing')
    last_ai = next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)
    if last_ai is None or MARKERS[source] not in str(last_ai.content):
        raise AssertionError('Final source marker missing')
    return len(calls)


def _worker(args) -> int:
    # This worker accepts only an empty parent-owned temporary directory.
    root = Path(args.workspace).resolve(strict=True)
    if not root.is_dir() or any(root.iterdir()):
        raise ValueError('An empty temporary workspace is required')
    for source, marker in MARKERS.items():
        folder = root / f'.{source}/skills/source-check'
        (folder / 'references').mkdir(parents=True)
        (folder / 'SKILL.md').write_text(
            '---\nname: source-check\n'
            'description: Use for controlled source verification tasks called source-check.\n'
            '---\n\nRead references/source.txt relative to this Skill directory using read. '
            'Then reply with only the exact marker returned by that reference file. '
            'Do not infer or guess its value.\n', encoding='utf-8',
        )
        (folder / 'references/source.txt').write_text(marker + '\n', encoding='utf-8')
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    app = build_local_application(
        args.config, tools=[create_session_read_tool()],
        system_prompt=BASE_SYSTEM_PROMPT, response_format=None,
    )
    session = app.create_workspace_session(args.model_id or app.default_model_id, root)
    print('phase=default_started', flush=True)
    first = app.run(session, [('user', 'Use the source-check Skill for a controlled '
                              'verification task. Follow the default source in the '
                              'available Skill index and report the required marker.')])
    if first.get('approval_requests'):
        raise AssertionError('Unexpected approval; nothing was approved')
    first_count = check_skill_trace(first['messages'], root, 'agents')
    print(f'phase=default_verified source=agents read_calls={first_count}', flush=True)
    print('phase=explicit_started', flush=True)
    second = app.run(session, [('user', 'Now explicitly use the source-check Skill from '
                               '.claude/skills. Read that source and its required '
                               'reference, then report only its exact marker.')])
    if second.get('approval_requests'):
        raise AssertionError('Unexpected approval; nothing was approved')
    second_count = check_skill_trace(second['messages'][len(first['messages']):], root, 'claude')
    print(f'phase=explicit_verified source=claude read_calls={second_count}', flush=True)
    print('status=PASS default_source=agents explicit_source=claude '
          'body_and_reference_reads=verified tool_messages=paired', flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id')
    parser.add_argument('--total-timeout-seconds', type=float, default=120)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--workspace', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            return _worker(args)
        except Exception as error:
            print(f'status=FAIL error_type={type(error).__name__}', flush=True)
            return 1
    if args.total_timeout_seconds <= 0:
        parser.error('total timeout must be positive')
    with TemporaryDirectory(prefix='naiyue-skill-probe-') as directory:
        command = [sys.executable, '-m', 'scripts.probe_skill_loading', '--worker',
                   '--workspace', directory, '--config', args.config]
        if args.model_id:
            command.extend(['--model-id', args.model_id])
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=args.total_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b''
            print(output.decode('utf-8') if isinstance(output, bytes) else output, end='')
            print('status=TIMEOUT worker=terminated')
            return 1
        print(result.stdout, end='')
        if result.returncode and not result.stdout:
            print(f'status=FAIL worker_exit_code={result.returncode}')
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
