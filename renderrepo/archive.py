"""Portable ZIP packages, with no model weights or executable install scripts."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

from .pack import relative_path

MAX_FILES = 4096
MAX_BYTES = 1024 * 1024 * 1024  # Bound unpacking; packages carry examples, not weights.


def write_archive(package, output):
    from .cli import load_package
    manifest = load_package(package)
    from .capsule import verify, DESCRIPTOR
    capsule = verify(package, manifest)
    paths = ({DESCRIPTOR, *(entry['path'] for entry in capsule['files'])} if capsule else
             {'manifest.yaml', manifest['workflow']})
    for artifact in manifest['artifacts']:
        for source in artifact.get('sources', []):
            if source['uri'].startswith('package:'):
                paths.add(relative_path(source['uri'][8:]))
    if len(paths) > MAX_FILES or sum((package / name).stat().st_size for name in paths) > MAX_BYTES:
        raise ValueError('archive exceeds 4096 members or 1 GiB unpacked size')
    if len({name.casefold() for name in paths}) != len(paths) or any(':' in name for name in paths):
        raise ValueError('archive names must be unique across case and contain no colons')
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(paths):
            path = package / name
            if not path.resolve().is_relative_to(package.resolve()) or not path.is_file():
                raise ValueError(f'package member missing or outside package: {name}')
            archive.write(path, name)


@contextmanager
def package_directory(path):
    if path.suffix != '.rrpkg':
        yield path
        return
    try:
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix='renderrepo-package-') as temporary:
            root = Path(temporary)
            members = archive.infolist()
            if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_BYTES:
                raise ValueError('archive exceeds 4096 members or 1 GiB unpacked size')
            seen = set()
            for member in members:
                name = member.filename.rstrip('/') if member.is_dir() else member.filename
                relative_path(name)
                # Reject drive paths as well as absolute/traversing paths on every OS.
                if ':' in name or name.casefold() in seen:
                    raise ValueError(f'duplicate or unsafe archive member: {name}')
                seen.add(name.casefold())
                mode = member.external_attr >> 16
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError(f'archive links/special files are not supported: {name}')
                if member.flag_bits & 1:
                    raise ValueError('encrypted package members are not supported')
            if 'manifest.yaml' not in seen:
                raise ValueError('archive must contain manifest.yaml at its root')
            for member in members:
                target = root / member.filename
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open('xb') as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
            yield root
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError(f'invalid .rrpkg archive: {error}') from error
