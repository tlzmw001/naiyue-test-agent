"""Build a Linux execution-only wheel using the project's existing native/setuptools flow."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from scripts.envd_artifacts import ROOT, inspect_envd_wheel, load_profile, stage_envd
from scripts.native_assets import load_config, prepare_native
from scripts.release_artifacts import check_tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=[name for name, value in load_config()['targets'].items() if value['os'] == 'linux'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tag')
    args = parser.parse_args()
    version = load_profile(ROOT)['version']
    if args.tag:
        check_tag(args.tag, version)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    filename = f'naiyue_test_agent_envd-{version}-py3-none-{load_config()["targets"][args.target]["wheel_tag"]}.whl'
    if (output / filename).exists():
        raise FileExistsError('Execution wheel already exists; choose a fresh output directory')
    with tempfile.TemporaryDirectory(prefix='nyt-envd-build-') as directory:
        staging = Path(directory) / 'project'
        stage_envd(ROOT, staging, args.target)
        prepare_native(args.target, staging / 'naiyue_test_agent/_assets')
        subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output), str(staging)], check=True)
        print(json.dumps(inspect_envd_wheel(output / filename), indent=2))


if __name__ == '__main__':
    main()
