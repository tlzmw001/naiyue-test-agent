"""A may remain before S09, but its reviewed consumer boundary cannot drift silently."""
from pathlib import Path
import pytest


def put(root,name,text):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)


def test_inventory_tracks_import_shapes_entrypoints_packaging_and_explicit_retention(tmp_path):
    from scripts.a_consumer_inventory import scan
    put(tmp_path,'naiyue_test_agent/envd/__init__.py','')
    put(tmp_path,'naiyue_test_agent/envd/worker.py','value=1\n')
    put(tmp_path,'naiyue_test_agent/execution/git_repository.py','class GitRepository:pass\n')
    put(tmp_path,'naiyue_test_agent/direct.py','import naiyue_test_agent.envd.worker\n')
    put(tmp_path,'naiyue_test_agent/relative.py','from .envd import worker\n')
    put(tmp_path,'naiyue_test_agent/dynamic.py',
        'import importlib as loader\nloader.import_module("naiyue_test_agent.envd.worker")\n'
        '__import__("naiyue_test_agent.envd.worker")\n')
    put(tmp_path,'naiyue_test_agent/lazy/__init__.py',
        '_EXPORTS={"worker":("naiyue_test_agent.envd.worker","value")}\n')
    put(tmp_path,'naiyue_test_agent/service/entry.py',
        'from .. import direct\n@router.post("/environment")\ndef endpoint():pass\n')
    put(tmp_path,'naiyue_test_agent/unrelated.py','value=1\n')
    put(tmp_path,'scripts/old_probe.py','from naiyue_test_agent import direct\n')
    put(tmp_path,'scripts/verify_sandbox_inputs.py','import naiyue_test_agent.envd.worker\n')
    put(tmp_path,'.github/workflows/check.yml','run: python -m scripts.verify_sandbox_inputs\n')
    put(tmp_path,'tests/test_old.py','import naiyue_test_agent.envd.worker\n')
    put(tmp_path,'frontend/src/Api.ts','export const prepareEnvironment = () => {};\n')
    put(tmp_path,'pyproject.toml','[project.scripts]\nnyt="naiyue_test_agent.direct:main"\n'
        '[tool.setuptools.packages.find]\ninclude=["naiyue_test_agent.*"]\n')
    rows={row['path']:row for row in scan(tmp_path)}
    assert 'naiyue_test_agent/unrelated.py' not in rows
    assert 'naiyue_test_agent/execution/git_repository.py' in rows
    assert rows['naiyue_test_agent/execution/git_repository.py']['retained'].startswith('shared:')
    for name in ('naiyue_test_agent/direct.py','naiyue_test_agent/relative.py',
            'naiyue_test_agent/dynamic.py','naiyue_test_agent/lazy/__init__.py',
            'naiyue_test_agent/service/entry.py','scripts/old_probe.py','tests/test_old.py',
            'frontend/src/Api.ts','pyproject.toml','.github/workflows/check.yml'):
        assert rows[name]['retained'] and len(rows[name]['sha256'])==64
    kinds={ref['kind'] for row in rows.values() for ref in row['references']}
    assert {'absolute_import','relative_import','import_module','__import__','lazy_export',
        'transitive_consumer','frontend_symbol','cli_entrypoint','api_registration',
        'package_member','script_entrypoint'}<=kinds


def test_frozen_inventory_detects_new_consumers_removed_files_and_hash_drift(tmp_path):
    from scripts.a_consumer_inventory import freeze,check
    put(tmp_path,'naiyue_test_agent/envd/__init__.py','')
    put(tmp_path,'naiyue_test_agent/consumer.py','from . import envd\n')
    snapshot=tmp_path/'frozen.json'
    freeze(tmp_path,snapshot,review='approved pre-S09 A retention')
    assert check(tmp_path,snapshot)==[]
    put(tmp_path,'naiyue_test_agent/consumer.py','from . import envd\nvalue=2\n')
    assert any('hash_drift:naiyue_test_agent/consumer.py'==error for error in check(tmp_path,snapshot))
    put(tmp_path,'naiyue_test_agent/new_consumer.py','__import__("naiyue_test_agent.envd")\n')
    assert 'new_consumer:naiyue_test_agent/new_consumer.py' in check(tmp_path,snapshot)
    (tmp_path/'naiyue_test_agent/consumer.py').unlink()
    assert 'removed_consumer:naiyue_test_agent/consumer.py' in check(tmp_path,snapshot)


def test_dynamic_imports_with_unknown_targets_are_visible_for_review(tmp_path):
    from scripts.a_consumer_inventory import scan,freeze
    put(tmp_path,'naiyue_test_agent/runtime.py','from importlib import import_module as load\nload(selected)\n')
    row,=scan(tmp_path)
    assert row['path']=='naiyue_test_agent/runtime.py'
    assert any(ref['kind']=='dynamic_unresolved' for ref in row['references'])
    assert 'manual' in row['retained']
    with pytest.raises(ValueError,match='review'):
        freeze(tmp_path,tmp_path/'frozen.json',review='')


def test_repository_frozen_a_consumers_have_not_drifted():
    from scripts.a_consumer_inventory import check
    root=Path(__file__).resolve().parents[2]
    assert check(root,root/'release/a-consumers-s08.json')==[]
