"""Windows private service directories require CPython's 3.12.4 security fix."""

from pathlib import Path
import tomllib

from packaging.specifiers import SpecifierSet
from packaging.version import Version


def test_release_excludes_python_without_windows_private_directory_support():
    manifest = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
    supported = SpecifierSet(manifest['project']['requires-python'])
    for version in ('3.12.0', '3.12.1', '3.12.2', '3.12.3'):
        assert Version(version) not in supported
    for version in ('3.12.4', '3.12.15', '3.13.0'):
        assert Version(version) in supported
