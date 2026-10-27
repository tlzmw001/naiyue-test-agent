"""Immutable Git input facts and bounded, public preparation failures."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

GIT_INPUT_CODES = frozenset({
    'git_input_invalid_id','git_input_invalid_object','git_input_invalid_path','git_input_sensitive',
    'git_input_unsupported','git_input_git_unavailable','git_input_budget','git_input_timeout',
    'git_input_git_failed','git_input_cache_unavailable','git_input_source_changed','git_input_outside',
    'git_input_invalid_selection','git_input_invalid_source','git_input_no_repository','git_input_no_head',
    'git_input_layout_unsupported','git_input_ignored','git_input_not_found','git_input_conflict',
    'git_input_capacity','git_input_project_unavailable','git_input_unconfirmed','git_input_io_failed',
    'git_input_invalid_transfer','git_input_corrupt','git_input_chunk_conflict','git_input_base_missing',
    'git_input_invalid_bundle','git_input_not_cached','git_input_workspace_not_empty',
    'git_input_install_unconfirmed','git_input_busy','git_input_binding_conflict',
})


class GitInputError(RuntimeError):
    def __init__(self, code, paths=()):
        self.code, self.paths = code, tuple(paths)
        super().__init__(code + (': ' + ', '.join(self.paths) if self.paths else ''))


def input_id(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise GitInputError('git_input_invalid_id')
    return value


def object_id(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise GitInputError('git_input_invalid_object')
    return value


def input_path(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 4096
            or re.search(r'[\x00-\x1f\x7f\\]', value)):
        raise GitInputError('git_input_invalid_path')
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or path.as_posix() != value or any(part in ('.', '..')
            or part.casefold().rstrip(' .') == '.git' or ':' in part for part in path.parts)):
        raise GitInputError('git_input_invalid_path')
    return value


def input_payload(value):
    if not isinstance(value,dict) or set(value)!={'input_id','original_head','input_snapshot'}:
        raise GitInputError('git_input_invalid_transfer')
    input_id(value['input_id']);object_id(value['original_head']);object_id(value['input_snapshot'])
    return value


def git_path(raw):
    try:
        return input_path(raw.decode('utf-8'))
    except UnicodeError:
        raise GitInputError('git_input_invalid_path') from None


def sensitive_path(path):
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    if name == '.env' or name.startswith('.env.'):
        return name.rsplit('.', 1)[-1] not in ('example', 'sample', 'template')
    return (name in ('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.netrc', '_netrc')
            or name.endswith(('.p12', '.pfx')) or '/'.join(parts[-2:]).lower() == '.aws/credentials')


def check_content(path, content):
    if sensitive_path(path) or re.search(
            rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----', content):
        raise GitInputError('git_input_sensitive', (path,))
    if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
        raise GitInputError('git_input_unsupported', (path,))


@dataclass(frozen=True)
class InputFile:
    path: str
    mode: str
    oid: str
    size: int


@dataclass(frozen=True)
class InputExclusion:
    path: str
    reason: str


@dataclass(frozen=True)
class InputSnapshot:
    input_id: str
    original_head: str
    input_snapshot: str
    tree: str
    manifest: tuple[InputFile, ...]
    excluded: tuple[InputExclusion, ...]
    total_bytes: int
    bundle_sha256: str
    bundle_size: int

    @classmethod
    def from_dict(cls, value):
        fields = dict(value)
        fields['manifest'] = tuple(InputFile(**item) for item in fields['manifest'])
        fields['excluded'] = tuple(InputExclusion(**item) for item in fields['excluded'])
        return cls(**fields)


@dataclass(frozen=True)
class GitInputRecord:
    input_id: str
    project_id: str
    project_revision: int
    include_untracked: tuple[str, ...]
    settings: dict
    state: str = 'preparing'
    snapshot: InputSnapshot | None = None
    error_code: str | None = None
    error_paths: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value):
        fields = dict(value)
        fields['include_untracked'] = tuple(fields['include_untracked'])
        fields['error_paths'] = tuple(fields['error_paths'])
        if fields['snapshot'] is not None:
            fields['snapshot'] = InputSnapshot.from_dict(fields['snapshot'])
        return cls(**fields)
