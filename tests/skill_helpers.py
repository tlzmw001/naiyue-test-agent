"""Local Skill fixtures shared by discovery and application integration tests."""

from pathlib import Path

import yaml


def write_skill(root: Path, source='agents', name='demo', *, directory=None,
                description='Use for a controlled test task.', body='BODY_MARKER', extra=None):
    folder = root / f'.{source}' / 'skills' / (directory or name)
    folder.mkdir(parents=True, exist_ok=True)
    header = {'name': name, 'description': description, **(extra or {})}
    path = folder / 'SKILL.md'
    path.write_text('---\n' + yaml.safe_dump(header, allow_unicode=True)
                    + '---\n\n' + body + '\n', encoding='utf-8')
    return path
