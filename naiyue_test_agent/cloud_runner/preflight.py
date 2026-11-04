"""Finite image identity/tool checks; full Kit/pytest validation runs at build time."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER_PYTHON = '/opt/nyt/runner/bin/python'
TEST_PYTHON = '/opt/nyt/test/bin/python'
AITEST = '/opt/nyt/test/bin/aitest'


def _run(argv, *, cwd=None, timeout=90):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('preflight_command_failed:' + Path(argv[0]).name)
    return result.stdout


def check(*, full=False):
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    readonly = True
    for path in (Path(RUNNER_PYTHON), Path(__file__).absolute()):
        for parent in (path, *path.parents):
            if os.access(parent, os.W_OK): readonly = False
    kit = _run([TEST_PYTHON, '-I', '-c',
        'import aitest_kit,importlib.metadata;print(importlib.metadata.version("aitest-kit"))']).strip()
    from naiyue_test_agent.contracts.cloud_versions import RUNNER_PROTOCOL,STATE_SCHEMA
    result = dict(runner_protocol=RUNNER_PROTOCOL,state_schema=STATE_SCHEMA,schema_version=1, uid=os.getuid(), euid=os.geteuid(), gid=os.getgid(), egid=os.getegid(),
        groups=os.getgroups(), no_new_privs=int(status['NoNewPrivs']),
        caps={k:int(status[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb','CapBnd')},
        runner_readonly=readonly, runner_version=importlib.metadata.version('naiyue-test-agent'),
        kit_version=kit, toolchain_sha256=None, checks={})
    if not full: return result
    _run([AITEST, '--help'])
    result['checks']['kit_cli'] = 'passed'
    for program in ('git', 'bash', 'rg', 'fd'):
        _run([program, '--version'])
    result['checks']['native_tools'] = 'passed'
    _run([RUNNER_PYTHON, '-I', '-m', 'pip', 'check'])
    _run([TEST_PYTHON, '-I', '-m', 'pip', 'check'])
    result['checks']['pip_check'] = 'passed'
    with tempfile.TemporaryDirectory(prefix='nyt-template-check-') as directory:
        _run([AITEST, 'init', '--target', directory])
        _run([AITEST, 'doctor', '--workspace', directory])
        result['checks']['doctor'] = 'passed'
        test = Path(directory) / 'test_image.py'
        test.write_text('def test_image_fixture():\n    assert sum([1, 2]) == 3\n')
        junit = Path(directory) / 'junit.xml'
        _run([TEST_PYTHON, '-I', '-m', 'pytest', str(test), '-q', '--junitxml=' + str(junit)], cwd=directory)
        result['checks']['pytest'] = 'passed'
        # The report collector consumes the real pytest XML. This is not a
        # fabricated result.json or a codegen/business-effect acceptance claim.
        script = ('import json,sys;from pathlib import Path;'
            'from aitest_kit.report.collector import collect_result;'
            'from aitest_kit.report.renderer import render_markdown;'
            'r=collect_result(junit_path=sys.argv[1],generated_files=[sys.argv[2]],'
            'run_id="template-check",command="pytest image fixture");'
            'assert r["summary"]["passed"]==1;'
            'p=Path(sys.argv[3]);p.write_text(render_markdown(r));'
            'assert p.stat().st_size>0')
        _run([TEST_PYTHON, '-I', '-c', script, str(junit), str(test), str(Path(directory) / 'report.md')], cwd=directory)
        result['checks']['report'] = 'passed'
    installed = _run([TEST_PYTHON, '-I', '-c',
        'import json,importlib.metadata as m;print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()},sort_keys=True,separators=(",",":")))']).strip()
    result['toolchain_sha256'] = hashlib.sha256(installed.encode()).hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--quick', action='store_true')
    args = parser.parse_args(argv)
    try:
        value = check(full=args.full)
        print(json.dumps(value, separators=(',', ':')))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': 'image_preflight_failed', 'error_type': type(error).__name__}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
