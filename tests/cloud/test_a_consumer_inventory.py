"""The formal product must not reintroduce the retired A execution chain."""
from pathlib import Path
import pytest


def put(root,name,text):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)


@pytest.mark.parametrize('name,text', [
    ('naiyue_test_agent/envd/__init__.py',''),
    ('naiyue_test_agent/direct.py','import naiyue_test_agent.envd.worker\n'),
    ('naiyue_test_agent/relative.py','from .execution import docker_files\n'),
    ('naiyue_test_agent/dynamic.py','import importlib\nimportlib.import_module("naiyue_test_agent.envd")\n'),
    ('naiyue_test_agent/lazy/__init__.py','_EXPORTS={"old":("naiyue_test_agent.envd","run")}\n'),
    ('frontend/src/api.ts','export const prepareEnvironment = () => {};\n'),
    ('scripts/build_envd.py','print("old builder")\n'),
    ('.github/workflows/check.yml','run: python -m scripts.verify_sandbox_inputs\n'),
])
def test_retirement_gate_rejects_modules_import_forms_ui_and_tools(tmp_path,name,text):
    from scripts.a_consumer_inventory import check
    put(tmp_path,name,text)
    errors=check(tmp_path)
    assert errors and any(name in error for error in errors)


def test_retirement_gate_preserves_native_git_and_b_backend(tmp_path):
    from scripts.a_consumer_inventory import check
    put(tmp_path,'naiyue_test_agent/contracts/git_native.py','class GitInputError(RuntimeError):pass\n')
    put(tmp_path,'naiyue_test_agent/execution/git_repository.py','from ..contracts.git_native import GitInputError\n')
    put(tmp_path,'naiyue_test_agent/application/cloud/backend.py','from opensandbox import SandboxManager\n')
    put(tmp_path,'naiyue_test_agent/local.py','from .execution.git_repository import GitRepository\n')
    assert check(tmp_path)==[]


def test_repository_has_no_retired_a_consumers():
    from scripts.a_consumer_inventory import check
    assert check(Path(__file__).resolve().parents[2])==[]
