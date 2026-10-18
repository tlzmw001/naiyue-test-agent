"""A wheel with bundled executables has a platform tag, without a Python ABI."""

import json
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel


class PlatformWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        path = Path(__file__).parent / 'naiyue_test_agent' / '_assets' / 'native.json'
        if not path.is_file():
            raise RuntimeError('Prepare release resources with python -m scripts.build_release --target TARGET')
        return 'py3', 'none', json.loads(path.read_text(encoding='utf-8'))['wheel_tag']


class PlatformDistribution(Distribution):
    def has_ext_modules(self):
        # Executables make this a platform distribution, independent of a Python ABI.
        return True


setup(distclass=PlatformDistribution, cmdclass={'bdist_wheel': PlatformWheel})
