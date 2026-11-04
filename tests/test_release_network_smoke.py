"""The standard installed-wheel gate exercises platform-native file/process APIs."""
import shutil
import sys


def test_network_install_smoke_checks_native_paths_state_and_child(tmp_path):
    from scripts.release_network_smoke import network_scenario,verify_network_report
    report=network_scenario(tmp_path,git_executable=shutil.which('git'))
    verify_network_report(report)
    assert report['git_scope']=='passed'
    assert report['source_tree']=='passed' and report['state_replace']=='passed'
    assert report['child_process']=='passed'


def test_network_probe_command_handles_platform_temporary_root():
    import json,subprocess
    from pathlib import Path
    result=subprocess.run([sys.executable,'-m','scripts.release_network_smoke'],cwd=Path(__file__).resolve().parents[1],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['source_tree']=='passed'
