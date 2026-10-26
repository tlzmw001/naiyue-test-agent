"""Environment descriptors are immutable facts, with no provider dependency."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from naiyue_test_agent.contracts.environment import EnvironmentRef
from test_storage_dependency_direction import imports, PACKAGE


@pytest.mark.parametrize('platform,root', [('linux', 'relative'), ('linux', '/a/../b'),
    ('windows', 'C:relative'), ('windows', 'C:\\a\\..\\b')])
def test_environment_root_must_be_absolute_and_canonical(platform, root):
    with pytest.raises(ValueError, match='environment'):
        EnvironmentRef('binding', 'sandbox', 'project', root, platform)


def test_environment_descriptor_round_trips_without_live_capabilities():
    ref = EnvironmentRef('host:project:project', 'host', 'project', '/project', 'linux', 3)
    assert EnvironmentRef(**json.loads(json.dumps(ref.public()))) == ref
    with pytest.raises(FrozenInstanceError):
        ref.root = '/elsewhere'


def test_file_provider_and_environment_contract_do_not_import_application():
    for folder in ('execution',):
        for path in (PACKAGE / folder).rglob('*.py'):
            names = imports(path.read_text(), '.'.join(path.relative_to(PACKAGE.parent).parts[:-1]))
            assert not any(name.startswith('naiyue_test_agent.application') for name in names), str(path)
    names = imports((PACKAGE / 'contracts/environment.py').read_text(), 'naiyue_test_agent.contracts')
    assert not any(name.startswith(('naiyue_test_agent.execution.', 'naiyue_test_agent.tools.',
        'naiyue_test_agent.application.')) for name in names)
