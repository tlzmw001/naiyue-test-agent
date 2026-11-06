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


def test_network_install_smoke_checks_new_attachments_and_output_files(tmp_path):
    from scripts.release_network_smoke import network_scenario,verify_network_report
    report=network_scenario(tmp_path)
    assert report['attachment_roundtrip']=='passed'
    assert report['output_files_save']=='passed'
    assert report['long_upload_journal']=='passed'
    verify_network_report(report)
    root=tmp_path/'网络 native checks'
    assert (root/'附件展开'/'隐藏 目录'/'.env').read_bytes()==b'EXPLICIT_SAMPLE=1\r\n'
    assert (root/'保存结果'/'报告 目录'/'.env').read_bytes()==b'received output\r\n'
    assert (root/'保存结果'/'新建文件夹'/'报告 目录'/'.env').read_bytes()==b'received output\r\n'


def test_network_report_rejects_missing_new_native_checks():
    import pytest
    from scripts.release_network_smoke import verify_network_report
    report=dict(source_tree='passed',capture_archive='passed',state_replace='passed',child_process='passed',git_scope='unavailable')
    with pytest.raises(ValueError,match='installed_network_checks_failed'):verify_network_report(report)
