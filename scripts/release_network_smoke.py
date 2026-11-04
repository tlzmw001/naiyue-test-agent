"""Native installed checks in disposable paths; no company SSH or service access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def network_scenario(root,*,git_executable=None):
    from naiyue_test_agent.cloud_files.source_scope import tree,plan_scope,preview_scope
    from naiyue_test_agent.cloud_files.cache import capture_cached
    from naiyue_test_agent.cloud_files.archive import validate_package
    from naiyue_test_agent.cloud_files.service_tunnels import ReverseProcess
    from naiyue_test_agent.cloud_runner.state import read_json,write_json
    from naiyue_test_agent.contracts.cloud_inputs import InputLimits
    root=Path(root).resolve()/'网络 native checks';root.mkdir()
    project=root/'项目 with spaces';project.mkdir();(project/'Src').mkdir()
    (project/'Src'/'样例.txt').write_text('native file\n',encoding='utf-8')
    limits=InputLimits(git_executable=git_executable or 'git')
    listing=tree(project,limits,path='Src')
    assert [v['name'] for v in listing['entries']]==['样例.txt']
    selected,_=plan_scope(project,limits,mode='selection',paths=['Src'])
    preview=preview_scope(project,limits,mode='selection',paths=['Src'])
    assert preview['file_count']==1 and preview['issues']==[]
    captured=capture_cached(project,selected,root/'cache',limits,codec='tar')
    manifest,_=validate_package(captured.directory/'archive',captured.codec,limits,expected_id=captured.input_id)
    assert [v.path for v in manifest.entries]==['Src/样例.txt']
    state=root/'网络.json'
    write_json(state,{'revision':1,'address':'http://example.test'})
    assert read_json(state)['revision']==1
    write_json(state,{'revision':2,'address':'https://example.test'})
    assert read_json(state)['revision']==2
    git='unavailable'
    if git_executable:
        subprocess.run([git_executable,'init','-q',str(project)],check=True,timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        selected,receipt=plan_scope(project,limits,mode='git',paths=['src' if os.name=='nt' else 'Src'])
        assert selected.paths==['Src/样例.txt'] and receipt.root==project
        git='passed'
    # A real local child exercises Win32 flags, CRLF pipe reads and terminate/wait.
    # Its synthetic ACK is deliberately not a claim of real SSH connectivity.
    code="import sys,time;sys.stderr.write('debug1: remote forward success for: listen 172.18.0.1:21001, connect example.test:443\\r\\n');sys.stderr.flush();time.sleep(60)"
    child=ReverseProcess([sys.executable,'-c',code,'-R','172.18.0.1:21001:example.test:443','local-smoke'],5)
    try:
        assert child.acknowledged.wait(10) and child.ready
    finally:child.close()
    assert not child.alive and not child.reader.is_alive()
    return dict(source_tree='passed',capture_archive='passed',state_replace='passed',
        git_scope=git,child_process='passed',real_ssh='not_tested')


def verify_network_report(report):
    if not isinstance(report,dict) or any(report.get(k)!='passed' for k in
            ('source_tree','capture_archive','state_replace','child_process')):
        raise ValueError('installed_network_checks_failed')
    if report.get('git_scope') not in ('passed','unavailable'):raise ValueError('installed_git_check_invalid')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='nyt-network-smoke-') as path:
        result=network_scenario(Path(path),git_executable=shutil.which('git'))
        verify_network_report(result);print(json.dumps(result,ensure_ascii=False))
