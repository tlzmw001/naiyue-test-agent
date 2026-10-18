"""Build-time, checksum-pinned native resources. Nothing downloads at runtime."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import struct
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'release' / 'native-tools.json'
MAX_DOWNLOAD = 64 * 1024 * 1024


def load_config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def download_checked(url, checksum, cache):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'raw.githubusercontent.com')
            or parsed.username or parsed.password or parsed.query):
        raise ValueError('Unsupported release asset URL')
    if len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
        raise ValueError('Invalid SHA-256')
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / checksum
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == checksum:
        return destination
    temporary = None
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=cache, suffix='.download', delete=False) as output:
                temporary = Path(output.name)
                digest, size = hashlib.sha256(), 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_DOWNLOAD:
                        raise ValueError('Release asset exceeds size budget')
                    digest.update(block)
                    output.write(block)
        if digest.hexdigest() != checksum:
            raise ValueError('Release asset SHA-256 mismatch')
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def archive_member(archive, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('Invalid archive member')
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            matches = [entry for entry in source.infolist() if entry.filename == name]
            if len(matches) != 1:
                raise ValueError('Missing or duplicate archive member')
            entry = matches[0]
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or entry.file_size > MAX_DOWNLOAD:
                raise ValueError('Invalid archive member type or size')
            return source.read(entry)
    with tarfile.open(archive) as source:
        matches = [entry for entry in source.getmembers() if entry.name == name]
        if len(matches) != 1 or not matches[0].isreg() or matches[0].size > MAX_DOWNLOAD:
            raise ValueError('Missing, duplicate or non-regular archive member')
        with source.extractfile(matches[0]) as member:
            return member.read()


def macos_minimum(content, machine):
    if content[:4] != b'\xcf\xfa\xed\xfe':
        raise ValueError('Expected a 64-bit Mach-O executable')
    cpu, = struct.unpack_from('<I', content, 4)
    if cpu != {'arm64': 0x100000C, 'x86_64': 0x1000007}[machine]:
        raise ValueError('Wrong Mach-O architecture')
    commands, = struct.unpack_from('<I', content, 16)
    offset = 32
    for _ in range(commands):
        command, size = struct.unpack_from('<II', content, offset)
        if size < 8 or offset + size > len(content):
            raise ValueError('Invalid Mach-O command')
        if command in (0x32, 0x24):
            version, = struct.unpack_from('<I', content, offset + (12 if command == 0x32 else 8))
            return version >> 16, version >> 8 & 255
        offset += size
    raise ValueError('Mach-O minimum OS is missing')


def check_native(content, target):
    machine = target['machine']
    if target['os'] == 'macos':
        return macos_minimum(content, machine)
    if target['os'] == 'windows':
        if content[:2] != b'MZ':
            raise ValueError('Expected a PE executable')
        offset, = struct.unpack_from('<I', content, 0x3C)
        if content[offset:offset + 6] != b'PE\0\0\x64\x86':
            raise ValueError('Expected an x64 PE executable')
        return None
    if content[:6] != b'\x7fELF\x02\x01':
        raise ValueError('Expected a 64-bit little-endian ELF')
    architecture, = struct.unpack_from('<H', content, 18)
    if architecture != {'x86_64': 62, 'arm64': 183}[machine]:
        raise ValueError('Wrong ELF architecture')
    offset, = struct.unpack_from('<Q', content, 32)
    size, count = struct.unpack_from('<HH', content, 54)
    for index in range(count):
        kind, = struct.unpack_from('<I', content, offset + index * size)
        if kind == 3:  # PT_INTERP would require a host libc loader.
            raise ValueError('Expected a static Linux executable')
    return None


def prepare_native(target_name, destination, *, cache=None):
    config = load_config()
    if target_name not in config['targets']:
        raise ValueError('Unsupported release target')
    target = config['targets'][target_name]
    cache = cache or ROOT / 'build' / 'native-cache'
    binary_dir, license_dir = destination / 'bin', destination / 'licenses'
    binary_dir.mkdir(parents=True)
    license_dir.mkdir(parents=True, exist_ok=True)
    tools, minimums = {}, []
    for name, tool in config['tools'].items():
        asset = tool['assets'][target_name]
        archive = download_checked(asset['url'], asset['sha256'], cache)
        content = archive_member(archive, asset['binary'])
        minimum = check_native(content, target)
        if minimum is not None:
            minimums.append(minimum)
        filename = name + ('.exe' if target['os'] == 'windows' else '')
        binary = binary_dir / filename
        binary.write_bytes(content)
        binary.chmod(0o755)
        for license_name in tool['licenses']:
            data = archive_member(archive, str(PurePosixPath(asset['binary']).parent / license_name))
            (license_dir / f'{name}-{license_name}').write_bytes(data)
        tools[name] = {'version': tool['version'], 'filename': filename,
                       'sha256': hashlib.sha256(content).hexdigest(), 'archive_sha256': asset['sha256']}
    tag = target['wheel_tag']
    if minimums:
        major, minor = max(minimums)
        if major >= 11 and minor:
            major, minor = major + 1, 0  # Wheel tags on macOS 11+ only distinguish major releases.
        tag = f'macosx_{major}_{minor}_{target["machine"]}'
    manifest = {'target': target_name, 'wheel_tag': tag, 'tools': tools}
    (destination / 'native.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=load_config()['targets'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_native(args.target, args.output), indent=2))


if __name__ == '__main__':
    main()
