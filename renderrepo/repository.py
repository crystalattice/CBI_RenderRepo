"""Static package catalogs from explicitly configured local/HTTP sources."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, build_opener

import yaml

from .archive import package_directory
from .cli import load_package, read_yaml, require_keys
from .download import DownloadRedirects, fetch_artifact, source_uri

MAX_INDEX_BYTES = 8 * 1024 * 1024
INDEX_VERSION = 1


def validate_provenance(value):
    require_keys(value, {'name', 'index_uri', 'uri', 'archive_sha256'},
                 {'name', 'index_uri', 'uri', 'archive_sha256'}, 'repository provenance')
    if not isinstance(value['name'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value['name']):
        raise ValueError('invalid repository provenance source name')
    for key in ('index_uri', 'uri'):
        if not isinstance(value[key], str) or source_uri(value[key]) != value[key]:
            raise ValueError('invalid repository provenance URI')
    if not isinstance(value['archive_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['archive_sha256']):
        raise ValueError('invalid repository provenance archive SHA-256')
    return value


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def repository_lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another source configuration/refresh operation is active; retry when it finishes.') from None
        yield


def version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*', value):
        raise ValueError(f'repository versions must be numeric dotted strings: {value!r}')
    parts = tuple(int(part) for part in value.split('.'))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
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


def index_uri(value, relative_to=None):
    parts = urlsplit(value)
    if not parts.scheme:
        path = Path(value).expanduser()
        if relative_to and not path.is_absolute():
            path = relative_to / path
        path = path.resolve()
        return (path / 'index.json' if path.is_dir() or path.suffix != '.json' else path).as_uri()
    uri = source_uri(value)
    if not parts.path.endswith('.json'):
        uri = urljoin(uri.rstrip('/') + '/', 'index.json')
    return uri


def sources(args):
    if not args.sources.exists():
        return []
    config = read_yaml(args.sources)
    require_keys(config, {'repositories', 'official_catalog'}, {'repositories'}, 'sources')
    if not isinstance(config['repositories'], list):
        raise ValueError('repositories must be a list')
    result, seen = [], set()
    for item in config['repositories']:
        require_keys(item, {'name', 'uri', 'enabled', 'display_name'}, {'name', 'uri'}, 'repository')
        if not isinstance(item['name'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', item['name']) or item['name'] in seen:
            raise ValueError('repository names must be unique letters/digits/underscore/hyphen')
        if not isinstance(item['uri'], str) or not item['uri']:
            raise ValueError('repository uri must be nonempty')
        if type(item.get('enabled', True)) is not bool:
            raise ValueError('repository enabled must be a boolean')
        if not isinstance(item.get('display_name', item['name']), str) or len(item.get('display_name', '')) > 100:
            raise ValueError('catalog display name must be text, at most 100 characters')
        seen.add(item['name'])
        result.append({'name': item['name'], 'uri': index_uri(item['uri'], args.sources.resolve().parent),
                       'enabled': item.get('enabled', True), **({'display_name': item['display_name']} if 'display_name' in item else {})})
    official = config.get('official_catalog')
    if official:
        if not isinstance(official, str):
            raise ValueError('official_catalog must be an index location or null')
        if any(r['name'] == 'renderrepo-official' for r in result):
            raise ValueError('renderrepo-official is reserved when an official catalog is configured')
        result.insert(0, {'name': 'renderrepo-official', 'uri': index_uri(official), 'enabled': True})
    return result


def official_catalog(args):
    return read_yaml(args.sources).get('official_catalog') if args.sources.exists() else None


def configure_official(args, uri):
    with repository_lock(args.sources.with_suffix(args.sources.suffix + '.lock')):
        config = read_yaml(args.sources) if args.sources.exists() else {'repositories': []}
        if uri and any(r.get('name') == 'renderrepo-official' for r in config.get('repositories', [])):
            raise ValueError('A community catalog uses the reserved renderrepo-official name; rename/remove that source before configuring the official setting.')
        config['official_catalog'] = index_uri(uri) if uri else None
        atomic_text(args.sources, yaml.safe_dump(config, sort_keys=False))


def manage_sources(args):
    if args.action == 'official':
        configure_official(args, args.uri)
        print('Official catalog: ' + (official_catalog(args) or 'unconfigured'))
        return 0
    if args.action != 'list':
        with repository_lock(args.sources.with_suffix(args.sources.suffix + '.lock')):
            return change_source(args)
    configured = sources(args)
    for item in configured:
        state = cache_status(args, item)
        status = 'STALE' if state['stale'] else 'fresh'
        if not cache_path(args, item).exists():
            status = 'no index; STALE' if state.get('error') else 'no index'
        print(f"{item['name']} {'enabled' if item['enabled'] else 'disabled'} {item['uri']} [{status}]")
        if state.get('error'):
            print(f"  last refresh: {state['error']}")
    if not configured:
        print('No repositories configured.')
    return 0


def change_source(args, report=print):
    configured = [r for r in sources(args) if r['name'] != 'renderrepo-official' or not official_catalog(args)]
    if args.name == 'renderrepo-official' and official_catalog(args):
        raise ValueError('Configure the official catalog setting separately.')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.name):
        raise ValueError('invalid repository name')
    if args.action == 'add':
        if any(item['name'] == args.name for item in configured):
            raise ValueError('repository name already exists; remove it first')
        configured.append({'name': args.name, 'uri': index_uri(args.uri), 'enabled': True,
                           **({'display_name': args.display_name} if getattr(args, 'display_name', None) else {})})
    else:
        if not any(item['name'] == args.name for item in configured):
            raise ValueError('repository name is not configured')
        if args.action == 'remove':
            configured = [item for item in configured if item['name'] != args.name]
        else:
            for item in configured:
                if item['name'] == args.name:
                    item['enabled'] = args.action == 'enable'
    atomic_text(args.sources, yaml.safe_dump({'repositories': configured, 'official_catalog': official_catalog(args)}, sort_keys=False))
    report(f'{args.action.upper()}: {args.name}')
    return 0


def validate_index(index, uri):
    require_keys(index, {'index_version', 'packages'}, {'index_version', 'packages'}, 'index')
    if type(index['index_version']) is not int or index['index_version'] not in {0, INDEX_VERSION} or not isinstance(index['packages'], list):
        raise ValueError('index_version must be 0 (legacy) or 1 and packages must be a list')
    seen = set()
    packages = []
    for item in index['packages']:
        required = {'name', 'version', 'uri', 'sha256', 'size_bytes'}
        if index['index_version'] == INDEX_VERSION:
            required.add('description')
        require_keys(item, required | {'description', 'discovery', 'dependency_inventory', 'capabilities'}, required, 'index package')
        if not isinstance(item['name'], str) or not item['name'].strip():
            raise ValueError('package name must be nonempty')
        if not isinstance(item.get('description', ''), str):
            raise ValueError('package description must be a string')
        from .discovery import validate_discovery
        validate_discovery(item.get('discovery', {}))
        if 'capabilities' in item:
            from .capabilities import validate_summary
            validate_summary(item['capabilities'])
        if 'dependency_inventory' in item:
            from .readiness import validate_inventory
            validate_inventory(item['dependency_inventory'])
        key = (item['name'], version_key(item['version']))
        if key in seen:
            raise ValueError('duplicate package name/version (including equivalent dotted versions)')
        seen.add(key)
        if not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-fA-F]{64}', item['sha256']):
            raise ValueError('archive SHA-256 must contain 64 hex characters')
        if type(item['size_bytes']) is not int or item['size_bytes'] <= 0:
            raise ValueError('archive size_bytes must be positive')
        if not isinstance(item['uri'], str) or not item['uri']:
            raise ValueError('archive uri must be nonempty')
        resolved = source_uri(urljoin(uri, item['uri']))
        if urlsplit(uri).scheme in {'http', 'https'} and urlsplit(resolved).scheme not in {'http', 'https'}:
            raise ValueError('HTTP catalogs may reference only HTTP(S) package archives')
        packages.append(dict(item, description=item.get('description', ''), uri=resolved, sha256=item['sha256'].lower()))
    return packages


def build_index(args):
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError('repository directory must exist')
    output = args.output.expanduser().resolve() if args.output else directory / 'index.json'
    if output.parent != directory:
        raise ValueError('index output must be in the package directory so relative URLs remain portable')
    packages = []
    for archive in sorted(directory.glob('*.rrpkg')):
        with package_directory(archive) as package:
            manifest = load_package(package)
            from .readiness import inventory
            graph = json.loads((package / manifest['workflow']).read_text())
            dependencies = inventory(manifest, graph)
            from .capabilities import resolve_summary
            capabilities = resolve_summary(graph, manifest.get('capabilities'))
        version = manifest.get('version', '0')
        version_key(version)
        with archive.open('rb') as stream:
            sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        packages.append({'name': manifest['name'], 'version': version, 'description': manifest.get('description', ''),
                         'dependency_inventory': dependencies, 'capabilities': capabilities,
                         'uri': quote(archive.name), 'sha256': sha256, 'size_bytes': archive.stat().st_size,
                         **({'discovery': manifest['discovery']} if 'discovery' in manifest else {})})
    index = {'index_version': INDEX_VERSION, 'packages': packages}
    validate_index(index, output.as_uri())
    text = json.dumps(index, indent=2) + '\n'
    if len(text.encode()) > MAX_INDEX_BYTES:
        raise ValueError('index exceeds 8 MiB')
    atomic_text(output, text)
    print(f'INDEX: {output} ({len(packages)} packages)')
    return 0


def cache_path(args, repository):
    key = hashlib.sha256(repository['uri'].encode()).hexdigest()
    return args.cache_dir / 'indexes' / (repository['name'] + '-' + key + '.json')


def status_path(args, repository):
    return cache_path(args, repository).with_suffix('.status.json')


def cache_status(args, repository, cache_bytes=None):
    path = cache_path(args, repository)
    try:
        state = json.loads(status_path(args, repository).read_text())
        if (not isinstance(state, dict) or state.get('uri') != repository['uri']
                or type(state.get('stale')) is not bool):
            raise ValueError('invalid cache status')
        if cache_bytes is not None or path.is_file():
            if cache_bytes is not None:
                actual = hashlib.sha256(cache_bytes).hexdigest()
            else:
                with path.open('rb') as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if state.get('cache_sha256') != actual:
                return {'stale': True, 'error': 'cache/status mismatch; refresh interrupted or cache modified'}
        return state
    except (OSError, ValueError):
        return {'stale': True, 'error': 'no verified refresh status; run renderrepo refresh'}


def mark_status(args, repository, stale, error=None, refreshed_at=None):
    path = cache_path(args, repository)
    sha = None
    if path.is_file():
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    atomic_text(status_path(args, repository), json.dumps({
        'uri': repository['uri'], 'stale': stale, 'error': error,
        'checked_at': timestamp(), 'refreshed_at': refreshed_at, 'cache_sha256': sha}, indent=2) + '\n')


def selected_sources(args):
    configured = sources(args)
    selected = getattr(args, 'repo', None)
    if selected:
        configured = [r for r in configured if r['name'] == selected]
        if not configured:
            raise ValueError(f'Source {selected!r} is not configured.')
        if not configured[0]['enabled']:
            raise ValueError(f'Source {selected!r} is disabled; use renderrepo source enable {selected}.')
    return configured


def refresh_repository(args, repository, report=print):
    path = cache_path(args, repository)
    with repository_lock(path.with_suffix('.lock')):
        previous = cache_status(args, repository)
        try:
            mark_status(args, repository, True, 'refresh in progress/interrupted', previous.get('refreshed_at'))
            uri = repository['uri']
            request = Request(uri, headers={'User-Agent': 'RenderRepo/0.1', 'Accept-Encoding': 'identity'})
            with build_opener(DownloadRedirects()).open(request, timeout=args.timeout) as stream:
                if urlsplit(uri).scheme in {'http', 'https'} and stream.status != 200:
                    raise ValueError('index server must return HTTP 200')
                length = stream.headers.get('Content-Length')
                data = stream.read(MAX_INDEX_BYTES + 1)
                if length is not None and len(data) <= MAX_INDEX_BYTES and int(length) != len(data):
                    raise ValueError('incomplete index response')
            if len(data) > MAX_INDEX_BYTES:
                raise ValueError('index exceeds 8 MiB')
            index = json.loads(data)
            packages = validate_index(index, uri)
            refreshed_at = timestamp()
            atomic_text(path, json.dumps({'uri': uri, 'index_version': index['index_version'],
                                         'refreshed_at': refreshed_at, 'packages': packages}, indent=2) + '\n')
            mark_status(args, repository, False, refreshed_at=refreshed_at)
            report(f"UPDATED: {repository['name']} ({len(packages)} packages) [fresh]")
            return True
        except (OSError, ValueError, URLError, http.client.HTTPException) as error:
            message = ' '.join(str(error).split())
            try:
                mark_status(args, repository, True, message, previous.get('refreshed_at'))
            except OSError as status_error:
                report(f"STATUS WRITE FAILED: {repository['name']}: {status_error}")
            report(f"FAILED: {repository['name']}: {message}; previous valid cache retained [STALE]")
            return False


def refresh(args):
    configured = selected_sources(args)
    if not configured:
        print('No repositories configured.')
        return 0
    if args.timeout <= 0:
        raise ValueError('timeout must be positive')
    failed = False
    for repository in configured:
        if not repository['enabled']:
            print(f"SKIPPED: {repository['name']} (disabled)")
            continue
        try:
            if not refresh_repository(args, repository):
                failed = True
        except (OSError, ValueError) as error:
            print(f"FAILED: {repository['name']}: {error}; cache retained")
            failed = True
    if args.comfy_dir:
        available_updates(args)
    return 1 if failed else 0


def cached_packages(args, report=print):
    packages = []
    for repository in selected_sources(args):
        if not repository['enabled']:
            continue
        path = cache_path(args, repository)
        if not path.exists():
            state = cache_status(args, repository)
            report(f"NO INDEX: {repository['name']}; run renderrepo refresh" +
                  (f" [STALE: {state['error']}]" if state.get('error') else ''))
            continue
        try:
            data = path.read_bytes()
            state = cache_status(args, repository, cache_bytes=data)
            cached = json.loads(data)
            require_keys(cached, {'uri', 'packages', 'index_version', 'refreshed_at'}, {'uri', 'packages'}, 'cached index')
            if cached.get('uri') != repository['uri']:
                raise ValueError('cached index source does not match configuration')
            entries = validate_index({'index_version': cached.get('index_version', 0), 'packages': cached['packages']}, repository['uri'])
        except (OSError, ValueError) as error:
            report(f"INVALID CACHE: {repository['name']}: {error}; run renderrepo refresh [STALE]")
            continue
        if state['stale']:
            report(f"STALE: {repository['name']}: {state.get('error') or 'refresh failed'}; showing last valid catalog")
        packages.extend(dict(item, repository=repository['name'], index_uri=repository['uri'],
                             cache_stale=state['stale']) for item in entries)
    return sorted(packages, key=lambda p: (p['name'], version_key(p['version']), p['repository']))


def search(args):
    from .discovery import ranked
    packages = cached_packages(args)
    seen = set()
    for match in ranked(packages, args.query):
        identity = (match['repository'], match['index_uri'], match['name'])
        if identity in seen:
            continue
        seen.add(identity)
        versions = [p for p in packages if (p['repository'], p['index_uri'], p['name']) == identity]
        item = max(versions, key=lambda p: version_key(p['version']))
        meta = item.get('discovery', {})
        title = meta.get('title') or item['name']
        description = ' '.join((meta.get('summary') or item['description']).split())
        print(f"{item['name']} {item['version']} [{item['repository']}] — {title}"
              + (' [STALE]' if item['cache_stale'] else '') + (f' — {description}' if description else ''))
        if len(versions) > 1:
            print('  Available versions: ' + ', '.join(p['version'] for p in versions))
        if meta.get('required_inputs'):
            print('  Publisher input requirements: ' + '; '.join(meta['required_inputs']))
    return 0


def update_candidates(root, packages):
    from .receipts import read_receipts
    updates = []
    for installed in read_receipts(root):
        try:
            installed_key = version_key(installed['version'])
        except ValueError:
            updates.append({"name": installed["name"], "installed_version": installed["version"], "unordered": True})
            continue
        pinned_repo = installed.get('repository', {}).get('name')
        pinned_uri = installed.get('repository', {}).get('index_uri')
        candidates = [p for p in packages if p['name'] == installed['name'] and
                      (not pinned_repo or p['repository'] == pinned_repo) and
                      (not pinned_uri or p['index_uri'] == pinned_uri)]
        for repo in sorted({p['repository'] for p in candidates}):
            latest = max((p for p in candidates if p['repository'] == repo), key=lambda p: version_key(p['version']))
            if version_key(latest['version']) > installed_key:
                updates.append(dict(latest, installed_version=installed['version'],
                                    installed_status=installed['status'], unordered=False))
    return updates


def available_updates(args):
    root = args.comfy_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Comfy directory does not exist')
    for item in update_candidates(root, cached_packages(args)):
        if item['unordered']:
            print(f"UNORDERED VERSION: {item['name']} {item['installed_version']}")
        else:
            print(f"UPDATE AVAILABLE: {item['name']} {item['installed_version']} -> {item['version']} [{item['repository']}]"
                  + (' (partial installation)' if item['installed_status'] == 'partial' else '')
                  + (' [STALE]' if item['cache_stale'] else ''))
    return 0


def select_package(args, report=print):
    name = str(args.package)
    candidates = [p for p in cached_packages(args, report=report) if p['name'] == name and
                  (not args.version or version_key(p['version']) == version_key(args.version))]
    if not candidates:
        raise ValueError('package/version not found in configured cached indexes; run update')
    if len({p['repository'] for p in candidates}) > 1:
        raise ValueError('package exists in multiple repositories; select --repo NAME')
    return max(candidates, key=lambda p: version_key(p['version']))


def acquire_package(args, report=print):
    selected = select_package(args, report=report)
    expected = getattr(args, 'expected_catalog', None)
    if expected and any(selected[key] != expected[key] for key in
                        ('name', 'version', 'repository', 'index_uri', 'uri', 'sha256', 'size_bytes')):
        raise ValueError('Selected catalog entry changed; search and request review again.')
    name = str(args.package)
    path = args.cache_dir / 'packages' / (selected['sha256'] + '.rrpkg')
    valid = False
    if path.is_file() and path.stat().st_size == selected['size_bytes']:
        with path.open('rb') as stream:
            valid = hashlib.file_digest(stream, 'sha256').hexdigest() == selected['sha256']
    if not valid:
        report(f"PACKAGE: {name} {selected['version']} [{selected['repository']}] — {selected['size_bytes']:,} bytes")
        report(f"  source: {selected['uri']}; SHA-256 {selected['sha256']}")
        if args.timeout <= 0:
            raise ValueError('timeout must be positive')
        path.parent.mkdir(parents=True, exist_ok=True)
        fetch_artifact(dict(selected, filename=path.name), selected['uri'], path.parent, args.timeout)
    args.repository_info = {'name': selected['repository'], 'index_uri': selected['index_uri'],
                            'uri': selected['uri'], 'archive_sha256': selected['sha256']}
    args.expected_package = {'name': selected['name'], 'version': selected['version']}
    return path
