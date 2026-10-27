"""Run one deterministic file partition of the complete backend suite."""

import argparse
from pathlib import Path
import subprocess
import sys


def test_files(directory, index, count):
    if count < 1 or not 0 <= index < count:
        raise ValueError('Invalid CI shard index/count')
    files = sorted(p for p in directory.rglob('*.py')
                   if p.name.startswith('test_') or p.name.endswith('_test.py'))
    if not files:
        raise ValueError('CI shard directory contains no tests')
    selected = files[index::count]
    if not selected:
        raise ValueError('CI shard contains no tests')
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('tests'))
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    files = test_files(args.directory, args.index, args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f'Backend shard {args.index + 1}/{args.count}: {len(files)} test files', flush=True)
    return subprocess.call([sys.executable, '-m', 'pytest', '-q', '-x',
                            '--junitxml=' + str(args.output), *map(str, files)])


if __name__ == '__main__':
    sys.exit(main())
