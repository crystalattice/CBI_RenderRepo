"""RenderRepo's local S4 capsule profile; no cross-application middleware.

The JSON descriptor binds exact payload bytes. Legacy manifests are retained and
projected into declarations and historical authoring observations separately.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

FORMAT = 's4-capsule'
PROFILE = 'renderrepo'
SCHEMA_VERSION = 1
DESCRIPTOR = 'capsule.json'
PAYLOAD_DOMAIN = b'renderrepo:s4:payload:v1\n'
CAPSULE_DOMAIN = b'renderrepo:s4:capsule:v1\n'
HASH = re.compile(r'[0-9a-f]{64}')


def canonical(value):
    """RR JSON v1: ASCII JSON, sorted keys, compact separators, no NaN/Infinity."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(',', ':'), allow_nan=False).encode('ascii')
    except (ValueError, TypeError) as error:
        raise ValueError('Capsule metadata must be finite JSON data.') from error


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate capsule JSON field: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite capsule JSON number: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def digest(domain, value):
    return hashlib.sha256(domain + canonical(value)).hexdigest()


def projections(manifest):
    """Historical exporter snapshots are observations, never portable qualification."""
    claims = copy.deepcopy(manifest)
    runtime = claims.get('runtime', {})
    if set(runtime) - {'node_packages', 'requirements', 'custom_nodes', 'local_installation', 'compatibility'}:
        raise ValueError('Capsule runtime may contain declarations and authoring provenance only; target state belongs in observations.')
    if runtime.get('compatibility', 'unverified') != 'unverified':
        raise ValueError('Capsules cannot claim observed runtime compatibility.')
    if 'node_packages' in runtime and not isinstance(runtime['node_packages'], list):
        raise ValueError('Capsule node_packages must be a list of author declarations.')
    observations = {}
    if 'local_installation' in runtime:
        observations['authoring_runtime'] = runtime.pop('local_installation')
    runtime.pop('compatibility', None)  # Historical unverified sentinel, not a finding.
    target_fields = {'target_check', 'node_availability', 'installation_state', 'installed',
                     'execution_results', 'execution_result', 'hardware_performance', 'performance',
                     'compatibility', 'nodeCompatibility', 'target_status', 'worker_active', 'status'}
    def check(value):
        if isinstance(value, dict):
            if target_fields & set(value):
                raise ValueError('Target runtime state belongs in observations, not capsule claims.')
            for child in value.values(): check(child)
        elif isinstance(value, list):
            for child in value: check(child)
    check(claims)
    return claims, observations


def payload_files(package):
    from .pack import relative_path
    package = Path(package)
    result, names = [], set()
    for path in sorted(package.rglob('*')):
        if path.is_symlink():
            raise ValueError('Capsule payload cannot contain symlinks.')
        if path.is_dir():
            continue
        name = path.relative_to(package).as_posix()
        relative_path(name)
        if ':' in name or any(not part for part in name.split('/')) or name.casefold() in names:
            raise ValueError('Unsafe or ambiguous capsule path: ' + name)
        names.add(name.casefold())
        if name == DESCRIPTOR:
            continue
        if not path.is_file():
            raise ValueError('Capsule payload must contain regular files.')
        with path.open('rb') as stream:
            sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        result.append({'path': name, 'size_bytes': path.stat().st_size, 'sha256': sha256})
    # Sort paths, rather than filesystem traversal order; includes supplementary files.
    return sorted(result, key=lambda entry: entry['path'])


def validate_identity(value):
    keys = {'format', 'profile', 'schema_version', 'sha256', 'payload_sha256', 'predecessor'}
    if (not isinstance(value, dict) or set(value) != keys or value.get('format') != FORMAT
            or value.get('profile') != PROFILE or type(value.get('schema_version')) is not int
            or value['schema_version'] != SCHEMA_VERSION
            or not all(isinstance(value.get(k), str) and HASH.fullmatch(value[k]) for k in ('sha256', 'payload_sha256'))
            or value.get('predecessor') is not None and
            (not isinstance(value['predecessor'], str) or not HASH.fullmatch(value['predecessor']))):
        raise ValueError('Unsupported or invalid RenderRepo capsule identity/version.')
    if value['sha256'] == value['predecessor']:
        raise ValueError('Capsule cannot be its own predecessor.')
    return value


def identity(descriptor):
    return validate_identity({k: descriptor.get(k) for k in
        ('format', 'profile', 'schema_version', 'sha256', 'payload_sha256', 'predecessor')})


def describe(package, manifest, *, predecessor=None, provenance=None):
    claims, observations = projections(manifest)
    files = payload_files(package)
    value = dict(format=FORMAT, profile=PROFILE, schema_version=SCHEMA_VERSION,
                 predecessor=predecessor, files=files, payload_sha256=digest(PAYLOAD_DOMAIN, files),
                 claims=claims, observations=observations,
                 provenance=copy.deepcopy(provenance or {}), s4=None)
    value['sha256'] = digest(CAPSULE_DOMAIN, value)
    identity(value)
    return value


def verify(package, manifest):
    path = Path(package) / DESCRIPTOR
    if not path.exists() and not path.is_symlink():
        return None  # Legacy v0 remains readable; imports never silently migrate it.
    if path.is_symlink():
        raise ValueError('Capsule descriptor must be a regular file.')
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError('Capsule descriptor must be a JSON object.')
    identity(value)
    if value.get('s4', 'missing') is not None:
        raise ValueError('S4 cross-application metadata is reserved and must be null.')
    if not isinstance(value.get('provenance'), dict):
        raise ValueError('Capsule provenance must be an object.')
    records=value['provenance'].get('source_records', [])
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError('Capsule provenance source_records must be a list of source objects.')
    expected = describe(package, manifest, predecessor=value['predecessor'], provenance=value['provenance'])
    if canonical(value) != canonical(expected):
        raise ValueError('Capsule content/hash mismatch; create a linked revision instead of editing it.')
    return value


def seal(package, manifest, *, predecessor=None, provenance=None):
    """Seal a new staging directory once. Never overwrite an existing descriptor."""
    value = describe(package, manifest, predecessor=predecessor, provenance=provenance)
    with (Path(package) / DESCRIPTOR).open('xb') as stream:
        stream.write(canonical(value) + b'\n')
    return value


def parent_descriptor(package):
    from .cli import load_package
    manifest = load_package(package)
    return verify(package, manifest) or describe(package, manifest)


def copy_capsule(source, output, *, edit=None, provenance=None):
    """Copy/migrate to a new destination; edits produce a linked revision.

    A legacy parent's virtual identity uses the same deterministic descriptor
    with null predecessor/empty provenance; the original bytes remain unchanged.
    """
    from .archive import package_directory, write_archive
    from .cli import load_package
    source, output = Path(source).expanduser().resolve(), Path(output).expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise ValueError('Capsule output already exists; choose a new destination.')
    if source.suffix != '.rrpkg' and output.is_relative_to((source if source.is_dir() else source.parent).resolve()):
        raise ValueError('Capsule output must be outside its source directory.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with package_directory(source) as original, tempfile.TemporaryDirectory(dir=output.parent, prefix='.rr-capsule-') as temporary:
        if not original.is_dir():
            original = original.parent
        manifest = load_package(original)
        parent = verify(original, manifest)
        if parent is None:
            parent = describe(original, manifest)
        stage = Path(temporary) / 'package'
        shutil.copytree(original, stage, symlinks=True)
        # Validate all copied files even for migration from a legacy directory.
        payload_files(stage)
        if edit is not None:
            (stage / DESCRIPTOR).unlink(missing_ok=True)
            edit(stage)
            manifest = load_package(stage)
            seal(stage, manifest, predecessor=parent['sha256'], provenance=parent['provenance'] if provenance is None else provenance)
        elif not (stage / DESCRIPTOR).exists():
            seal(stage, manifest)  # Explicit migration, same virtual root identity.
        load_package(stage)
        if output.suffix == '.rrpkg':
            archive = Path(temporary) / 'capsule.rrpkg'
            write_archive(stage, archive)
            archive.rename(output)
        else:
            stage.rename(output)
    return output
