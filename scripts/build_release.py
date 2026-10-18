"""Build one platform wheel from a fresh, allowlisted staging tree."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from scripts.native_assets import ROOT, load_config, prepare_native
from scripts.release_artifacts import check_tag, inspect_wheel, source_version, stage_built_frontend, stage_frontend, stage_python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=load_config()['targets'], required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--tag')
    parser.add_argument('--frontend-assets', type=Path)
    args = parser.parse_args()
    if args.tag:
        check_tag(args.tag, source_version(ROOT))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nyt-build-') as directory:
        staging = Path(directory) / 'project'
        stage_python(ROOT, staging)
        assets = staging / 'naiyue_test_agent' / '_assets'
        native = prepare_native(args.target, assets)
        if args.frontend_assets:
            stage_built_frontend(args.frontend_assets.absolute(), assets)
        else:
            stage_frontend(ROOT, assets)
        filename = f'naiyue_test_agent-{source_version(ROOT)}-py3-none-{native["wheel_tag"]}.whl'
        if (output / filename).exists():
            raise FileExistsError(f'Output already exists: {filename}; choose a fresh output directory')
        subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output), str(staging)], check=True)
        print(json.dumps(inspect_wheel(output / filename), indent=2))


if __name__ == '__main__':
    main()
