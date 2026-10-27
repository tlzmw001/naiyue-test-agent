"""Real temporary Git fixtures shared by snapshot, API and envd input tests."""
import os
import subprocess


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath='+os.devnull,
        '-c', 'commit.gpgsign=false', '-c', 'user.name=Input Test',
        '-c', 'user.email=input@invalid', *args], cwd=root, stderr=subprocess.PIPE)


def repository(tmp_path):
    root=tmp_path/'project';root.mkdir()
    git(root,'init','-q')
    (root/'app.txt').write_text('base\n')
    (root/'.gitignore').write_text('.env\nignored/\n')
    git(root,'add','.');git(root,'commit','-qm','base')
    return root
