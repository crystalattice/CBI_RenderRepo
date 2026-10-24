"""Small, installation-local job files and conservative approval scope checks."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def lease(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise ValueError('An RR installation worker is still active; wait for it to finish.') from None
    return stream


def active(path):
    try:
        stream = lease(path)
    except ValueError:
        return True
    stream.close()
    return False


def package_hash(package):
    digest = hashlib.sha256()
    for path in sorted(package.rglob('*')):
        if path.is_symlink():
            raise ValueError('Retained package contains a symlink; import a clean package.')
        if path.is_file():
            digest.update(str(path.relative_to(package)).encode() + b'\0')
            with path.open('rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
    return digest.hexdigest()


def within_scope(approved, current):
    """Only verified completion/reuse may reduce the originally approved work."""
    if not approved or not current:
        return False
    for key in ('workflow', 'python', 'package', 'repository'):
        if approved.get(key) != current.get(key):
            return False
    old_nodes, new_nodes = approved.get('nodes', []), current.get('nodes', [])
    if not isinstance(old_nodes, list) or not isinstance(new_nodes, list):
        return False
    if len(old_nodes) != len(new_nodes):
        return False
    for old, new in zip(old_nodes, new_nodes):
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if {k: v for k, v in old.items() if k != 'operation'} != {k: v for k, v in new.items() if k != 'operation'}:
            return False
        # Legacy approvals came from the mandatory full code review. New reuse-only
        # approvals must never grow into a clone if a checkout disappears later.
        old_op, new_op = old.get('operation', 'install'), new.get('operation', 'install')
        if old_op not in {'install', 'reuse'} or new_op not in {'install', 'reuse'} or old_op == 'reuse' and new_op != 'reuse':
            return False
    before, after = approved.get('artifacts', []), current.get('artifacts', [])
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if old == new:
            continue
        identity = ('filename', 'destination', 'placement', 'sha256', 'size_bytes')
        if any(old.get(key) != new.get(key) for key in identity):
            return False
        if (old.get('operation') == new.get('operation') == 'reuse'
                and old.get('reuse_path') == new.get('reuse_path')
                and old.get('reuse_sha256') == new.get('reuse_sha256')):
            continue
        if not (old.get('operation') == 'acquire' and new.get('operation') == 'reuse'
                and new.get('reuse_path') == old['destination']
                and (new.get('sha256') or new.get('verified_completion'))):
            return False
    return True


def parse_plan(result):
    lines = result['log'].splitlines()
    scopes = [json.loads(line[len('RR_PLAN '):]) for line in lines if line.startswith('RR_PLAN ')]
    return {**result, 'log': '\n'.join(line for line in lines if not line.startswith('RR_PLAN ')),
            'scope': scopes[-1] if scopes else None}


def inherited_worker_fds():
    # Only set by the extension worker. Keep its locks alive through Git/pip.
    return tuple(int(value) for value in os.environ.get('RENDERREPO_WORKER_FDS', '').split(',') if value)
