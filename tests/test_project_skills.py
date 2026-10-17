"""Two Skill sources keep identity and disclose only usable metadata."""

import json
from xml.etree import ElementTree

import pytest

from naiyue_test_agent.application.skills import (
    discover_project_skills, format_skill_catalog, prepare_run_resources,
)
from skill_helpers import write_skill


def test_two_sources_keep_same_name_and_escape_index(tmp_path):
    first = write_skill(tmp_path, description='Use <xml> & "quotes".', body='PRIVATE_FIRST')
    second = write_skill(tmp_path, 'claude', body='PRIVATE_SECOND')
    catalog = discover_project_skills(tmp_path)
    assert [(item.source_id, item.name) for item in catalog.entries] == [
        ('agents', 'demo'), ('claude', 'demo'),
    ]
    assert catalog.default_source == 'agents'
    assert catalog.read_resource_roots == (first.parent.resolve(), second.parent.resolve())
    text = format_skill_catalog(catalog)
    block = text[text.index('<available_skills'):text.index('</available_skills>') + 19]
    tree = ElementTree.fromstring(block)
    assert tree.attrib['default_source'] == 'agents'
    assert tree.find('./source/skill/description').text == 'Use <xml> & "quotes".'
    assert 'PRIVATE_' not in text


@pytest.mark.parametrize('agents_state', ['missing', 'empty', 'invalid', 'disabled'])
def test_claude_becomes_default_when_agents_has_no_usable_skill(tmp_path, agents_state):
    if agents_state == 'empty':
        (tmp_path / '.agents/skills').mkdir(parents=True)
    elif agents_state == 'invalid':
        write_skill(tmp_path, name='Invalid Name', body='PRIVATE_INVALID')
    elif agents_state == 'disabled':
        write_skill(tmp_path, extra={'disable-model-invocation': True})
    write_skill(tmp_path, 'claude')
    catalog = discover_project_skills(tmp_path)
    assert catalog.default_source == 'claude'
    assert len(catalog.entries) == 1


def test_no_skills_has_no_catalog_and_keeps_project_instructions(tmp_path):
    assert format_skill_catalog(discover_project_skills(tmp_path)) is None
    assert prepare_run_resources(None) == {'prompt_parts': [], 'project_instructions': None, 'read_resource_roots': ()}
    (tmp_path / 'AGENTS.md').write_text('PROJECT_RULE', encoding='utf-8')
    resources = prepare_run_resources(tmp_path)
    prompt = ''.join(part['text'] for part in resources['prompt_parts'])
    assert 'PROJECT_RULE' in prompt and 'available_skills' not in prompt
    assert resources['read_resource_roots'] == ()
    assert resources['project_instructions'] == prompt


def test_same_source_duplicate_is_deterministic_and_reported(tmp_path, caplog):
    first = write_skill(tmp_path, directory='a', body='FIRST')
    second = write_skill(tmp_path, directory='z', body='SECOND')
    catalog = discover_project_skills(tmp_path)
    assert catalog.entries[0].skill_file == first.resolve()
    assert catalog.read_resource_roots == (first.parent.resolve(),)
    diagnostic = next(item for item in catalog.diagnostics if item['reason'] == 'name_collision')
    assert diagnostic['winner'] == str(first.resolve())
    assert diagnostic['loser'] == str(second.resolve())
    records = [record.skill_discovery for record in caplog.records if hasattr(record, 'skill_discovery')]
    assert all('winner' not in item and 'loser' not in item for item in records)


@pytest.mark.parametrize('bad_file', ['yaml', 'utf8', 'size', 'control', 'missing_description'])
def test_invalid_skill_is_diagnosed_without_disclosing_body(tmp_path, caplog, bad_file):
    path = write_skill(tmp_path, body='PRIVATE_BODY')
    data = {
        'yaml': b'---\nname: [PRIVATE_SECRET\n---\n',
        'utf8': b'PRIVATE_SECRET\xff',
        'size': b'PRIVATE_SECRET' + b'x' * (64 * 1024),
        'control': b'---\nname: demo\ndescription: valid\n---\nPRIVATE_SECRET\x00',
        'missing_description': b'---\nname: demo\n---\nPRIVATE_SECRET',
    }
    path.write_bytes(data[bad_file])
    catalog = discover_project_skills(tmp_path)
    assert catalog.entries == () and catalog.diagnostics
    assert 'PRIVATE_' not in caplog.text
    assert 'PRIVATE_' not in json.dumps(catalog.diagnostics)


def test_external_links_and_sensitive_targets_are_excluded(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = write_skill(tmp_path / 'outside')
    source = workspace / '.agents/skills'
    source.mkdir(parents=True)
    (source / 'external').symlink_to(outside.parent, target_is_directory=True)
    secret = workspace / '.env/private'
    secret.mkdir(parents=True)
    (source / 'sensitive').symlink_to(secret, target_is_directory=True)
    catalog = discover_project_skills(workspace)
    assert catalog.entries == () and len(catalog.diagnostics) == 2
    assert 'outside_workspace' in {item['reason'] for item in catalog.diagnostics}


def test_internal_link_uses_canonical_resource_directory(tmp_path):
    real = write_skill(tmp_path)
    (real.parent.parent / 'alias').symlink_to(real.parent, target_is_directory=True)
    catalog = discover_project_skills(tmp_path)
    assert len(catalog.entries) == 1
    assert catalog.read_resource_roots == (real.parent.resolve(),)


def test_link_to_hidden_metadata_is_not_advertised_as_readable(tmp_path):
    path = write_skill(tmp_path)
    hidden = path.parent / '.hidden.md'
    path.rename(hidden)
    path.symlink_to(hidden)
    catalog = discover_project_skills(tmp_path)
    assert catalog.entries == () and catalog.diagnostics
    assert catalog.diagnostics[0]['reason'] == 'hidden_target'
