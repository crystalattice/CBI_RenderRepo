"""Per-Comfy installation records and conservative removal of owned placements."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def receipt_path(root, name):
    return root / '.renderrepo' / 'receipts' / (hashlib.sha256(name.encode()).hexdigest() + '.json')


def read_receipts(root):
    folder = root / '.renderrepo' / 'receipts'
    records = []
    for path in sorted(folder.glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('receipt_version') != 1:
            raise ValueError(f'invalid installation receipt: {path}')
        if (not isinstance(record.get('name'), str) or not record['name']
                or not isinstance(record.get('version'), str)
                or record.get('status') not in {'partial', 'installed'}
                or not isinstance(record.get('workflow_sha256'), str)
                or not isinstance(record.get('managed'), list)
                or not isinstance(record.get('artifacts'), list)):
            raise ValueError(f'invalid installation receipt fields: {path}')
        if 'capsule' in record:
            from .capsule import validate_identity
            validate_identity(record['capsule'])
        if 'capsule_provenance' in record and not isinstance(record['capsule_provenance'], dict):
            raise ValueError(f'invalid capsule provenance in receipt: {path}')
        if record.get('comfy_root') != str(root):
            raise ValueError(f'receipt belongs to a different Comfy root: {path}')
        for item in record.get('managed', []):
            if (not isinstance(item, dict) or item.get('kind') not in {'file', 'link', 'git'}
                    or not isinstance(item.get('path'), str)
                    or not Path(item['path']).is_absolute()
                    or not isinstance(item.get('parent'), str)
                    or not Path(item['parent']).is_absolute()
                    or not isinstance(item.get('fingerprint'), str)):
                raise ValueError(f'invalid managed placement: {path}')
        records.append(record)
    return records


def fingerprint(path, kind):
    if kind == 'git':
        if not path.is_dir() or path.is_symlink():
            return None
        from .runtime import git_provenance
        provenance = git_provenance(path)
        if not provenance['revision']:
            return None
        h = hashlib.sha256(provenance['revision'].encode())
        for entry in sorted(path.rglob('*')):
            relative = entry.relative_to(path)
            if any(p in {'.git', '__pycache__'} for p in relative.parts) or entry.suffix == '.pyc':
                continue
            h.update(str(relative).encode() + b'\0')
            if entry.is_symlink():
                h.update(b'link:' + os.readlink(entry).encode())
            elif entry.is_file():
                h.update(b'file:' + digest(entry).encode() + str(entry.stat().st_mode & 0o777).encode())
            elif entry.is_dir():
                h.update(b'dir')
        return h.hexdigest()
    if kind == 'link':
        return os.readlink(path) if path.is_symlink() else None
    return digest(path) if path.is_file() and not path.is_symlink() else None


def placement(path, kind):
    return {'path': str(path.absolute()), 'kind': kind, 'fingerprint': fingerprint(path, kind),
            'parent': str(path.parent.resolve())}


def unchanged(item):
    path = Path(item['path'])
    return (str(path.parent.resolve()) == item['parent']
            and fingerprint(path, item['kind']) == item['fingerprint'])


def save_receipt(root, record):
    target = receipt_path(root, record['name'])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.receipt-', delete=False, mode='w', encoding='utf-8') as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def list_installed(args):
    root = args.comfy_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'Comfy directory does not exist: {root}')
    records = read_receipts(root)
    for record in sorted(records, key=lambda r: r['name']):
        print(f"{record['name']} {record['version']} {record['status']} {record['workflow_sha256']}")
    if not records:
        print('No RenderRepo packages installed.')
    return 0


def remove_package(args):
    root = args.comfy_dir.expanduser().resolve()
    records = read_receipts(root)
    record = next((r for r in records if r['name'] == args.name), None)
    if record is None:
        raise ValueError(f'package is not installed: {args.name}')
    other_claims = {i['path'] for r in records if r['name'] != args.name for i in r.get('managed', [])}
    other_claims.update(a.get('placement') for r in records if r['name']!=args.name for a in r.get('artifacts',[]))
    plan = []
    for item in record['managed']:
        path = Path(item['path'])
        if item['path'] in other_claims:
            print(f'KEEP SHARED: {path}')
        elif not path.exists() and not path.is_symlink():
            print(f'ALREADY ABSENT: {path}')
        elif not unchanged(item):
            print(f'KEEP MODIFIED: {path}')
        else:
            print(f'REMOVE: {path}')
            plan.append(item)
    print('Model weights, pre-existing placements, and installed Python dependencies are retained.')
    if not args.yes:
        try:
            approved = input(f'Remove package {args.name} and the listed placements? [y/N] ').strip().lower() in {'y', 'yes'}
        except EOFError:
            approved = False
        if not approved:
            print('Cancelled; no files changed.')
            return 1
    for item in plan:
        if unchanged(item):
            if item['kind'] == 'git':
                shutil.rmtree(item['path'])
            else:
                Path(item['path']).unlink()
        else:
            print(f'KEEP CHANGED DURING REMOVAL: {item["path"]}')
    receipt_path(root, args.name).unlink()
    print(f'Removed package registration: {args.name}')
    return 0
