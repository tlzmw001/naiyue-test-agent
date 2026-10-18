"""Prepare file identities without reading content; recheck them before IO."""

import os
import ntpath
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import unicodedata

from langchain_core.tools import ToolException


def permission_path(value, *, windows=None, pattern=False):
    """A comparison value only; never replace the native path used for IO."""
    windows = os.name == 'nt' if windows is None else windows
    if not windows:
        return PurePosixPath(value)
    text = str(value).replace('\\', '/')
    extended = text.startswith('//?/')
    if extended:
        tail = text[4:]
        if tail[:4].casefold() == 'unc/':
            text = '//' + tail[4:]
        elif re.match(r'^[a-zA-Z]:/', tail):
            text = tail
        else:
            raise ValueError('Unsupported Windows namespace')
        if any(part in ('.', '..') for part in text.split('/')):
            raise ValueError('Ambiguous extended Windows path')
    elif text.startswith(('//./', '/??/', '//??/')):
        raise ValueError('Unsupported Windows namespace')
    if (any(ord(char) < 32 or char in '<>"|' for char in text)
            or not pattern and any(char in text for char in '*?')):
        raise ValueError('Unsupported Windows file name')
    path = PureWindowsPath(text)
    if path.drive.startswith('\\\\') and ':' in path.drive:
        raise ValueError('Unsupported UNC share')
    if (path.drive and not path.is_absolute()) or (path.root and not path.drive):
        raise ValueError('Windows path requires an explicit drive or UNC share')
    reserved = {'con', 'prn', 'aux', 'nul', 'conin$', 'conout$'}
    reserved.update(prefix + digit for prefix in ('com', 'lpt') for digit in '123456789¹²³')
    components = path.parts[1:] if path.anchor else path.parts
    for part in components:
        if part in ('.', '..'):
            continue
        if (part.endswith((' ', '.')) or ':' in part
                or part.split('.')[0].rstrip(' ').casefold() in reserved):
            raise ValueError('Unsupported Windows file name')
    return PureWindowsPath(ntpath.normpath(str(path)).casefold())


def _existing_directory(path):
    while not path.exists():
        if path == path.parent:
            raise OSError('No existing directory anchor')
        path = path.parent
    if not path.is_dir():
        raise OSError('Target parent is not a directory')
    info = path.stat()
    return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino}


def prepare_file_subject(path, workspace, operation, *, allow_read_aliases=True):
    if not isinstance(workspace, Path) or not workspace.is_absolute():
        raise ToolException('This file tool has no session workspace. Select a workspace first.')
    try:
        if '\x00' in path:
            raise ValueError('Invalid path')
        if os.name == 'nt':
            permission_path(path)
        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else workspace / supplied
        if operation == 'read' and allow_read_aliases:
            try:
                candidate.lstat()
            except FileNotFoundError:
                # Only absence enables filename compatibility. A directory, broken
                # link or permission failure must keep its own target/error.
                normalized = re.sub('[\u00a0\u2000-\u200a\u202f\u205f\u3000]', ' ', path)
                normalized = normalized[1:] if normalized.startswith('@') else normalized
                bases = (normalized, re.sub(r' (AM|PM)(?=\.|$)', '\u202f' + r'\1', normalized, flags=re.IGNORECASE))
                variants = []
                for base in bases:
                    canonical = unicodedata.normalize('NFC', base)
                    forms = [base, canonical, unicodedata.normalize('NFD', base)]
                    # Mixed composition is distinct on Linux; probe finite names,
                    # without relying on macOS's filesystem normalization.
                    for index, char in enumerate(canonical):
                        decomposed = unicodedata.normalize('NFD', char)
                        if decomposed != char:
                            forms.append(canonical[:index] + decomposed + canonical[index + 1:])
                    for form in forms:
                        variants.extend((form, form.replace("'", '\u2019')))
                for variant in dict.fromkeys(variants):
                    if os.name == 'nt':
                        permission_path(variant)
                    supplied = Path(variant).expanduser()
                    alternative = supplied if supplied.is_absolute() else workspace / supplied
                    if alternative == candidate:
                        continue
                    try:
                        alternative.lstat()
                    except FileNotFoundError:
                        continue
                    candidate = alternative
                    break
        requested = Path(os.path.abspath(candidate))
        # Resolve existing links strictly; missing ordinary targets can be created
        # by an earlier sequential call in this same batch.
        for component in (candidate, *candidate.parents):
            if component.is_symlink() or os.name == 'nt' and component.is_junction():
                component.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
        if os.name == 'nt':
            permission_path(resolved)
        exists = resolved.exists()
        kind = ('directory' if resolved.is_dir() else 'file') if exists else 'missing'
        if exists:
            if not (resolved.is_file() or resolved.is_dir()):
                raise ValueError('Unsupported file kind')
            if operation in ('read', 'write') and kind != 'file':
                raise ValueError('Expected a regular file')
            if operation in ('list', 'find') and kind != 'directory':
                raise ValueError('Expected a directory')
        return {'operation': operation, 'workspace': str(workspace), 'requested': str(requested),
                'resolved': str(resolved), 'kind': kind,
                'anchor': _existing_directory(resolved.parent)}
    except (OSError, ValueError, RuntimeError) as error:
        raise ToolException('File target is unavailable: check the path, links and required file type.') from error


def verify_file_target(subject):
    try:
        current = prepare_file_subject(subject['requested'], Path(subject['workspace']), subject['operation'],
                                       allow_read_aliases=False)
        anchor = subject['anchor']
        info = Path(anchor['path']).stat()
        windows = subject.get('execution_environment', {}).get('platform')
        windows = windows == 'windows' if windows is not None else None
        if (permission_path(current['resolved'], windows=windows) != permission_path(subject['resolved'], windows=windows)
                or (current['kind'] != 'missing' and subject['kind'] != 'missing'
                    and current['kind'] != subject['kind'])
                or (info.st_dev, info.st_ino) != (anchor['device'], anchor['inode'])):
            raise ValueError('Target changed')
    except (OSError, ValueError, RuntimeError, ToolException) as error:
        raise ToolException('File target changed or became unavailable after approval review. Reissue the tool call.') from error
    target = Path(subject['resolved'])
    if subject['operation'] != 'write' and not target.exists():
        raise ToolException(subject.get('unavailable_message', 'File target is missing. Check the path and retry.'))
    return target


def hidden_or_private(subject):
    platform = subject.get('execution_environment', {}).get('platform')
    windows = platform == 'windows' if platform is not None else None
    root = permission_path(subject['workspace'], windows=windows)
    for value in (subject['requested'], subject['resolved']):
        path = permission_path(value, windows=windows)
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
        if any(part.startswith('.') and part not in ('.', '..') for part in parts):
            return True
        if path.suffix.lower() in ('.pem', '.key'):
            return True
    return False
