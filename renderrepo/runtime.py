"""Read-only Comfy capability checks and local runtime provenance."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener

from .download import DownloadRedirects, source_uri
from .pack import LOADERS, instance_definition

EDITOR_ONLY = {'Note', 'MarkdownNote', 'Reroute', 'PrimitiveNode'}
MAX_CAPABILITY_BYTES = 32 * 1024 * 1024


def graph_nodes(graph):
    definitions = {}
    def register(g):
        for sub in g.get('definitions', {}).get('subgraphs', []):
            definitions[sub['id']] = sub
            register(sub)
    register(graph)
    def walk(g, ancestors=frozenset()):
        if 'nodes' not in g:
            yield from (dict(n, id=i, type=n['class_type']) for i, n in g.items())
            return
        for node in g['nodes']:
            if not isinstance(node, dict) or not isinstance(node.get('type'), str):
                raise ValueError('editor nodes must have string type names')
            typ = node.get('type')
            if typ in definitions:
                if typ not in ancestors:
                    yield from walk(instance_definition(node, definitions[typ]), ancestors | {typ})
            elif typ not in EDITOR_ONLY:
                yield node
    return list(walk(graph))


def git_provenance(directory):
    def git(*args):
        result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True,
                                text=True, timeout=15, check=False, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
        return result.stdout.strip() if result.returncode == 0 else None
    result = {'revision': None, 'dirty': None, 'source': None}
    try:
        top = git('rev-parse', '--show-toplevel')
        if not top or Path(top).resolve() != directory.resolve():
            return result
        result['revision'] = git('rev-parse', 'HEAD')
        status = git('status', '--porcelain', '--untracked-files=normal', '--', '.', ':(exclude).renderrepo')
        result['dirty'] = bool(status) if status is not None else None
        remote = git('remote', 'get-url', 'origin')
        if remote:
            parts = urlsplit(remote)
            if parts.scheme in {'https', 'http', 'ssh', 'git'} and parts.hostname:
                # Persist provenance without credentials, query strings, or fragments.
                host = parts.hostname + (f':{parts.port}' if parts.port else '')
                result['source'] = urlunsplit((parts.scheme, host, parts.path, '', ''))
            elif remote.startswith('git@') and ':' in remote:
                result['source'] = 'ssh://' + remote[4:].replace(':', '/', 1)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return result


def capture_runtime(root):
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'Comfy directory does not exist: {root}')
    custom = []
    folder = root / 'custom_nodes'
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.name.startswith('.') or path.name == '__pycache__' or path.name.endswith('.disabled'):
                continue
            if path.is_dir():
                custom.append(dict(name=path.name, **git_provenance(path)))
            elif path.suffix == '.py':
                from .receipts import digest
                custom.append({'name': path.name, 'revision': None, 'sha256': digest(path)})
    return {'comfy': git_provenance(root), 'custom_nodes': custom}


def capability_json(base, route, timeout):
    uri = source_uri(base)
    if urlsplit(uri).scheme not in {'http', 'https'} or urlsplit(uri).query or urlsplit(uri).fragment:
        raise ValueError('Comfy URL must be an HTTP(S) base URL without credentials/query/fragment')
    request = Request(uri.rstrip('/') + route, headers={'User-Agent': 'RenderRepo/0.1'})
    with build_opener(DownloadRedirects()).open(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f'{route}: expected HTTP 200')
        length = response.headers.get('Content-Length')
        raw = response.read(MAX_CAPABILITY_BYTES + 1)
        if length is not None and len(raw) <= MAX_CAPABILITY_BYTES and int(length) != len(raw):
            raise ValueError('incomplete Comfy capability response')
    if len(raw) > MAX_CAPABILITY_BYTES:
        raise ValueError('Comfy capability response exceeds 32 MiB')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f'{route}: expected a JSON mapping')
    return data


def doctor(manifest, args, directories):
    from .cli import inspect_artifacts
    if args.timeout <= 0:
        raise ValueError('timeout must be positive')
    if args.comfy_dir:
        root = args.comfy_dir.expanduser().resolve()
        if not root.is_dir():
            raise ValueError('Comfy directory does not exist')
        for folder in (root / 'models', root / 'input'):
            if folder.is_dir() and folder not in directories:
                directories.append(folder)
    graph = json.loads((args.package_root / manifest['workflow']).read_text(encoding='utf-8'))
    try:
        nodes = graph_nodes(graph)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f'malformed workflow node metadata: {error}') from error
    info = capability_json(args.comfy_url, '/object_info', args.timeout)
    missing, unknown = False, False
    for typ in sorted({n['type'] for n in nodes}):
        available = typ in info
        print(f"{'NODE FOUND' if available else 'MISSING NODE'}: {typ}")
        if not available:
            missing = True
            print('  Supply the node/runtime explicitly; repository origin is not inferred.')
    for node in nodes:
        typ = node['type']
        if typ not in info:
            continue
        if not isinstance(info[typ], dict):
            raise ValueError(f'invalid capability entry: {typ}')
        for field, category, index in LOADERS.get(typ, []):
            widgets = node.get('widgets_values', [])
            value = (node['inputs'].get(field) if 'class_type' in node else
                     widgets.get(field) if isinstance(widgets, dict) else
                     widgets[index] if len(widgets) > index else None)
            if not isinstance(value, str):
                print(f'UNKNOWN MODEL SELECTION: {typ}.{field}')
                unknown = True
                continue
            inputs = info[typ].get('input', {})
            if (not isinstance(inputs, dict) or not isinstance(inputs.get('required', {}), dict)
                    or not isinstance(inputs.get('optional', {}), dict)):
                raise ValueError(f'invalid input capability mapping: {typ}')
            spec = inputs.get('required', {}).get(field, inputs.get('optional', {}).get(field))
            choices = spec[0] if isinstance(spec, list) and spec else None
            if not isinstance(choices, list):
                print(f'UNKNOWN MODEL VISIBILITY: {typ}.{field}={value}')
                unknown = True
            else:
                visible = value in choices
                print(f"{'MODEL VISIBLE' if visible else 'MODEL NOT VISIBLE'}: {category}/{value}")
                missing |= not visible
    if directories:
        for result in inspect_artifacts(manifest['artifacts'], directories):
            print(f"{result['status']}: {result['filename']}")
            for failure in result['failures']:
                print(f'  rejected: {failure}')
            missing |= result['status'] != 'FOUND'
    elif manifest['artifacts']:
        print('UNKNOWN LOCAL ARTIFACT IDENTITY: no machine directories supplied; server combos prove visibility only.')
        unknown = True
    for slot in manifest.get('inputs', []):
        if slot['mode'] == 'user':
            present = any((root / slot['filename']).is_file() for root in directories)
            loader = 'LoadImage' if slot['kind'] == 'image' else 'LoadVideo'
            specs = info.get(loader, {}).get('input', {}).get('required', {})
            advertised = [spec[0] for spec in specs.values() if isinstance(spec, list) and spec and isinstance(spec[0], list)]
            present |= any(slot['filename'] in choices for choices in advertised)
            print(f"{'INPUT FOUND' if present else 'INPUT REQUIRED'}: {slot['filename']} (or select a replacement in ComfyUI)")
            if not present and not directories and not advertised:
                unknown = True
            else:
                missing |= not present
    required_nodes = manifest.get('runtime', {}).get('custom_nodes', [])
    if required_nodes and not args.comfy_dir:
        print('UNKNOWN CUSTOM-NODE REVISIONS: supply --comfy-dir')
        unknown = True
    elif required_nodes:
        for required in required_nodes:
            actual = git_provenance(args.comfy_dir.expanduser().resolve() / 'custom_nodes' / required['name'])
            matches = actual['revision'] == required['revision'] and actual['dirty'] is False
            print(f"{'NODE REVISION MATCH' if matches else 'NODE REVISION MISMATCH'}: {required['name']} ({required['revision']})")
            missing |= not matches
    captured = manifest.get('runtime', {}).get('local_installation')
    if captured and args.comfy_dir:
        current = capture_runtime(args.comfy_dir)
        for label, expected, actual in [('Comfy', captured['comfy'], current['comfy'])] + [
            (entry['name'], entry, next((n for n in current['custom_nodes'] if n['name'] == entry['name']), {}))
            for entry in captured['custom_nodes']]:
            if expected.get('revision'):
                matches = actual.get('revision') == expected['revision'] and not actual.get('dirty')
                print(f"{'REVISION MATCH' if matches else 'REVISION MISMATCH'}: {label} ({expected['revision']})")
                missing |= not matches
            elif expected.get('sha256'):
                matches = actual.get('sha256') == expected['sha256']
                print(f"{'CODE HASH MATCH' if matches else 'CODE HASH MISMATCH'}: {label}")
                missing |= not matches
            else:
                print(f'UNKNOWN REVISION: {label}')
                unknown = True
            if expected.get('dirty'):
                print(f'UNKNOWN REPRODUCIBILITY: exported {label} had uncommitted changes')
                unknown = True
    elif captured:
        print('UNKNOWN RUNTIME REVISION: supply --comfy-dir to compare captured local revisions')
        unknown = True
    for issue in manifest.get('unresolved', []):
        print(f'UNRESOLVED EXPORT: {issue}')
        # Missing source/checksum does not mean an available node cannot run;
        # unresolved overrides do mean the model inventory cannot be checked reliably.
        if 'override' in issue or 'dynamic' in issue:
            unknown = True
    print('Checks diagnose declared dependencies; they do not prove execution, Python compatibility, or GPU capacity.')
    return 1 if missing else 3 if unknown else 0
