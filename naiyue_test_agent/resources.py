"""Resources installed with the application; source development can use PATH."""

import os
from pathlib import Path
import shutil


ASSETS = Path(__file__).resolve().parent / '_assets'


class PackageResourceError(RuntimeError):
    """An installed distribution is incomplete."""


def frontend_directory():
    if ASSETS.exists():
        if not (ASSETS / 'native.json').is_file():
            raise PackageResourceError('Missing resource manifest; reinstall naiyue-test-agent.')
        frontend = ASSETS / 'web'
        if not (frontend / 'index.html').is_file():
            raise PackageResourceError('Missing packaged frontend; reinstall naiyue-test-agent.')
        return frontend
    return Path(__file__).resolve().parents[1] / 'frontend' / 'dist'


def resolve_tool_binary(name):
    if name not in ('rg', 'fd'):
        raise ValueError('Unsupported bundled tool')
    if ASSETS.exists():
        if not (ASSETS / 'native.json').is_file():
            raise PackageResourceError('Missing resource manifest; reinstall naiyue-test-agent.')
        binary = ASSETS / 'bin' / (name + '.exe' if os.name == 'nt' else name)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise PackageResourceError(f'Missing executable {name}; reinstall naiyue-test-agent.')
        return str(binary)
    found = shutil.which(name)
    return found or (shutil.which('fdfind') if name == 'fd' else None)
