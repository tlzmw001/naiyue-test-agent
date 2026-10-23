"""Discover project Skill metadata and prepare a per-run prompt/resource scope."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import yaml

from .project_instructions import load_workspace_instruction_part


logger = logging.getLogger(__name__)
_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
_SOURCES = ('agents', 'claude')
_BUILTIN_COMMANDS = {'compact', 'context', 'model', 'tools', 'permissions', 'agents', 'skills'}
_MAX_SKILL_SOURCE_BYTES = 64 * 1024


class _SkillError(ValueError):
    """A fixed discovery reason, safe to log without the source file content."""


@dataclass(frozen=True)
class SkillEntry:
    source_id: str
    name: str
    description: str
    skill_file: Path
    resource_root: Path


@dataclass(frozen=True)
class SkillCatalog:
    entries: tuple[SkillEntry, ...]
    diagnostics: tuple[dict, ...]

    @property
    def default_source(self) -> str | None:
        return next((source for source in _SOURCES
                     if any(item.source_id == source for item in self.entries)), None)

    @property
    def read_resource_roots(self) -> tuple[Path, ...]:
        return tuple(dict.fromkeys(item.resource_root for item in self.entries))

    def command_for(self, entry: SkillEntry) -> str:
        # Keep aliases stable when one source disappears between selection and sending.
        qualified = entry.source_id != 'agents' or entry.name in _BUILTIN_COMMANDS
        return f'/{entry.source_id}:{entry.name}' if qualified else f'/{entry.name}'


def _target(path: Path, workspace: Path) -> Path:
    target = path.resolve(strict=True)
    try:
        relative = target.relative_to(workspace)
    except ValueError as error:
        raise _SkillError('outside_workspace') from error
    if any(part.startswith('.') and not (
        index == 0 and part in ('.agents', '.claude')
        and len(relative.parts) > 1 and relative.parts[1] == 'skills'
    ) for index, part in enumerate(relative.parts)):
        raise _SkillError('hidden_target')
    if any(part.startswith('.env') or part in ('.git', '.ssh') for part in target.parts):
        raise _SkillError('sensitive_target')
    if target.suffix.lower() in ('.key', '.pem'):
        raise _SkillError('sensitive_target')
    return target


def _report(diagnostics: list, source: str, reason: str, **details) -> None:
    record = {'source_id': source, 'reason': reason, **details}
    diagnostics.append(record)
    logger.warning('skill_discovery_failed source=%s reason=%s', source, reason,
                   extra={'skill_discovery': {
                       'source_id': source, 'reason': reason,
                       **({'error_type': details['error_type']} if 'error_type' in details else {}),
                   }})


def _metadata(path: Path) -> dict:
    with path.open('rb') as stream:
        content = stream.read(_MAX_SKILL_SOURCE_BYTES + 1)
    if len(content) > _MAX_SKILL_SOURCE_BYTES:
        raise _SkillError('skill_too_large')
    text = content.decode('utf-8-sig')
    if any(ord(char) < 32 and char not in '\t\r\n' or ord(char) == 127 for char in text):
        raise _SkillError('non_text_content')
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise _SkillError('missing_frontmatter')
    end = next((index for index in range(1, len(lines)) if lines[index] == '---'), None)
    if end is None:
        raise _SkillError('missing_frontmatter')
    data = yaml.safe_load('\n'.join(lines[1:end]))
    if not isinstance(data, dict):
        raise _SkillError('invalid_metadata')
    name, description = data.get('name'), data.get('description')
    if (not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name)
            or not isinstance(description, str) or not description.strip()
            or len(description) > 1024
            or type(data.get('disable-model-invocation', False)) is not bool):
        raise _SkillError('invalid_metadata')
    return data


def discover_project_skills(workspace_root: Path | None) -> SkillCatalog:
    if workspace_root is None:
        return SkillCatalog((), ())
    workspace = workspace_root.resolve(strict=True)
    entries, diagnostics = [], []
    for source in _SOURCES:
        origin = workspace / f'.{source}' / 'skills'
        if not origin.exists() and not origin.is_symlink():
            continue
        try:
            directory = _target(origin, workspace)
            if not directory.is_dir():
                raise _SkillError('invalid_source')
            candidates = sorted(directory.iterdir(), key=lambda path: path.name)
        except (OSError, ValueError, RuntimeError) as error:
            reason = str(error) if isinstance(error, _SkillError) else 'source_unavailable'
            _report(diagnostics, source, reason, error_type=type(error).__name__)
            continue
        names = {}
        for candidate in candidates:
            if candidate.name.startswith('.'):
                continue
            try:
                root = _target(candidate, workspace)
                if not root.is_dir():
                    continue
                supplied = root / 'SKILL.md'
                if not supplied.exists() and not supplied.is_symlink():
                    continue
                path = _target(supplied, workspace)
                try:
                    path.relative_to(root)
                except ValueError as error:
                    raise _SkillError('outside_skill_directory') from error
                if not path.is_file():
                    raise _SkillError('not_regular_file')
                data = _metadata(path)
                if data.get('disable-model-invocation') is True:
                    continue
                name = data['name']
                if name in names:
                    _report(diagnostics, source, 'name_collision',
                            winner=str(names[name]), loser=str(path))
                    continue
                names[name] = path
                entries.append(SkillEntry(source, name, data['description'].strip(), path, root))
            except yaml.YAMLError as error:
                _report(diagnostics, source, 'invalid_yaml', error_type=type(error).__name__)
            except (OSError, ValueError, UnicodeError, RuntimeError) as error:
                reason = str(error) if isinstance(error, _SkillError) else 'skill_unavailable'
                _report(diagnostics, source, reason, error_type=type(error).__name__)
    catalog = SkillCatalog(tuple(entries), tuple(diagnostics))
    logger.info('skill_catalog_loaded', extra={'skill_discovery': {
        'event': 'skill_catalog_loaded', 'skill_count': len(entries),
        'default_source': catalog.default_source,
    }})
    return catalog


def format_skill_catalog(catalog: SkillCatalog) -> str | None:
    if not catalog.entries:
        return None
    lines = [
        '\n\nSkill loading policy: Use the default source unless the user explicitly '
        'requests another source. Source agents means .agents/skills; source claude '
        'means .claude/skills. Keep same-name skills from different sources distinct. '
        'A user message starting with a listed command explicitly selects that exact '
        'Skill and source: use read to load its SKILL.md before following it. '
        'When a task matches a skill description, use read to load its SKILL.md before '
        'following it. Resolve referenced relative paths against the Skill directory '
        'and use absolute paths. If a requested source or skill is unavailable, say so '
        'rather than silently substituting another. If read is unavailable, explain '
        'that the Skill cannot be loaded. Skill instructions do not grant '
        'permissions or bypass tool approvals.',
        f'<available_skills default_source={quoteattr(catalog.default_source)}>',
    ]
    for source in _SOURCES:
        group = [item for item in catalog.entries if item.source_id == source]
        if not group:
            continue
        lines.append(f'<source id={quoteattr(source)} '
                     f'default={quoteattr(str(source == catalog.default_source).lower())}>')
        for item in group:
            lines.append(f'<skill><name>{escape(item.name)}</name>'
                         f'<command>{escape(catalog.command_for(item))}</command>'
                         f'<description>{escape(item.description)}</description>'
                         f'<location>{escape(str(item.skill_file))}</location></skill>')
        lines.append('</source>')
    lines.append('</available_skills>')
    return '\n'.join(lines)


def prepare_run_resources(workspace_root: Path | None, *, enable_skills=True):
    part = load_workspace_instruction_part(workspace_root)
    instructions = part['text'] if part else None
    catalog = discover_project_skills(workspace_root) if enable_skills else SkillCatalog((), ())
    index = format_skill_catalog(catalog)
    parts = ([part] if part else []) + ([{'id': 'skills', 'text': index, 'count': len(catalog.entries)}] if index else [])
    return {'prompt_parts': parts,
            'project_instructions': instructions, 'read_resource_roots': catalog.read_resource_roots}
