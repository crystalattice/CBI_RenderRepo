"""Place artifacts and unchanged workflows into an explicitly chosen Comfy root."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .cli import inspect_artifacts
from .download import install_artifacts, resolve_source, source_uri
from .receipts import read_receipts, save_receipt, placement, unchanged, digest


def install_comfy(manifest, args, directories):
    root = args.comfy_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'Comfy directory does not exist: {root}')
    model_root = args.model_dir.expanduser().resolve() if args.model_dir else root / 'models'
    input_root = args.input_dir.expanduser().resolve() if getattr(args, 'input_dir', None) else root / 'input'
    workflow_root = (args.workflow_dir.expanduser().resolve() if args.workflow_dir else
                     root / 'user' / 'default' / 'workflows')
    from .nodes import plan_nodes, acquire_node, install_requirements
    node_plan = plan_nodes(manifest, args, root)
    targets = {}
    aliases = {}
    for artifact in manifest['artifacts']:
        if 'target' not in artifact:
            raise ValueError(f'{artifact["filename"]}: target required for Comfy installation')
        area, tail = artifact['target'].split('/', 1)
        base = model_root if area == 'models' else input_root
        target = base / tail
        if not target.parent.resolve().is_relative_to(base.resolve()):
            raise ValueError(f'installation path escapes destination: {target}')
        if target.is_dir():
            raise ValueError(f'artifact target is a directory: {target}')
        targets[artifact['filename']] = target
        if area == 'models' and args.model_dir:
            alias = root / artifact['target']
            if not alias.parent.resolve().is_relative_to((root / 'models').resolve()):
                raise ValueError(f'model link escapes Comfy directory: {alias}')
            if alias.exists() and alias.resolve() != target.resolve():
                raise ValueError(f'model link would replace an existing file: {alias}')
            aliases[artifact['filename']] = alias
            print(f'COMFY MODEL LINK: {alias} -> {target}')
    for folder in (model_root, input_root):
        if folder.is_dir() and folder not in directories:
            directories.append(folder)
    results = inspect_artifacts(manifest['artifacts'], directories)
    if getattr(args, 'plan_json', False) or getattr(args, 'approved_scope', None):
        retained = root / '.renderrepo' / 'jobs'
        if any(result['path'] and Path(result['path']).resolve().is_relative_to(retained)
               for result in results):
            raise ValueError('Artifact reuse must not link installed files to removable RR history; choose installed artifact directories.')
    safe_name = re.sub(r'[^A-Za-z0-9_.-]', '-', manifest['name']).strip('.') or 'workflow'
    workflow_target = workflow_root / (safe_name + '.json')
    data = (args.package_root / manifest['workflow']).read_bytes()
    records = read_receipts(root)
    workflow_hash = hashlib.sha256(data).hexdigest()
    previous = next((r for r in records if r['name'] == manifest['name']), None)
    from .capsule import verify, identity
    capsule = verify(args.package_root, manifest)
    if previous and previous.get('capsule') and (capsule is None or previous['capsule']['sha256'] != capsule['sha256']):
        raise ValueError('a different capsule revision is registered; review removal with renderrepo remove before installing this revision')
    if previous and (previous['workflow_sha256'] != workflow_hash or
                     previous['version'] != manifest.get('version', '0')):
        raise ValueError('a different package version is registered; review removal with '
                         'renderrepo remove before installing this version. Modified and pre-existing '
                         'workflows are retained and may block replacement.')
    if workflow_target.exists() and workflow_target.read_bytes() != data:
        raise ValueError(f'workflow already exists with different contents: {workflow_target}; '
                         'back up or move the existing workflow before installing. It will not be overwritten.')
    candidates = {str(p.absolute()): p for p in [*targets.values(), *aliases.values(), workflow_target]}
    for node in node_plan:
        candidates[str(node['target'].absolute())] = node['target']
    node_paths = {str(node['target'].absolute()) for node in node_plan}
    preexisting = {name for name, p in candidates.items() if p.exists() or p.is_symlink()}
    managed = {item['path']: item for record in records for item in record.get('managed', [])
               if item['path'] in candidates and unchanged(item)}
    receipt = {'receipt_version': 1, 'name': manifest['name'], 'version': manifest.get('version', '0'),
               'comfy_root': str(root), 'workflow_sha256': workflow_hash,
               'workflow_path': str(workflow_target.absolute()), 'status': 'partial',
               'artifacts': [], 'managed': list(managed.values()),
               'custom_nodes': list(previous.get('custom_nodes', [])) if previous else []}

    if capsule:
        receipt['capsule'] = identity(capsule)
        if capsule['provenance']:
            receipt['capsule_provenance'] = capsule['provenance']

    from .readiness import inventory
    receipt['dependency_inventory'] = inventory(manifest, json.loads(data))

    if getattr(args, 'repository_info', None):
        receipt['repository'] = args.repository_info
    elif previous and 'repository' in previous:
        receipt['repository'] = previous['repository']

    def checkpoint(status):
        receipt['status'] = status
        for name, path in candidates.items():
            if name not in preexisting and (path.exists() or path.is_symlink()):
                if name in node_paths:
                    managed.setdefault(name, placement(path, 'git'))
                elif path.is_symlink():
                    managed[name] = placement(path, 'link')
                elif path == workflow_target or path.is_relative_to(input_root):
                    managed[name] = placement(path, 'file')
        receipt['managed'] = list(managed.values())
        receipt['artifacts'] = []
        for artifact in manifest['artifacts']:
            path = targets[artifact['filename']]
            if path.is_file():
                identity = getattr(args, 'acquired', {}).get(artifact['filename'], {})
                receipt['artifacts'].append({'filename': artifact['filename'],
                    'path': str(path.resolve()), 'placement': str(path.absolute()),
                    'size_bytes': path.stat().st_size,
                    'sha256': identity.get('sha256') or digest(path)})
        save_receipt(root, receipt)

    # Preflight sources before consent and all writes. Existing files become links, not copies.
    overrides = {}
    for item in args.source or []:
        name, sep, uri = item.partition('=')
        if not sep or name not in targets or name in overrides:
            raise ValueError('--source must name a unique declared filename')
        overrides[name] = source_uri(uri)
    # Explicit server-file choices are references, not a second heavyweight copy.
    # A user-selected unchecksummed file remains weaker evidence than a declared hash.
    for artifact, result in zip(manifest['artifacts'], results):
        source = overrides.get(artifact['filename'])
        if source and urlsplit(source).scheme == 'file' and artifact['target'].startswith('models/'):
            path = Path(url2pathname(urlsplit(source).path)).resolve()
            if path.is_relative_to(root / '.renderrepo' / 'jobs'):
                raise ValueError('Artifact references cannot point into removable RR history.')
            if not path.is_file(): raise ValueError(f'Local source is not a file: {path}')
            if artifact.get('size_bytes') is not None and path.stat().st_size != artifact['size_bytes']:
                raise ValueError(f'{artifact["filename"]}: selected local file size mismatch')
            if artifact.get('sha256') and digest(path).lower() != artifact['sha256'].lower():
                raise ValueError(f'{artifact["filename"]}: selected local file SHA-256 mismatch')
            result.update(status='FOUND',path=str(path))
    unresolved = False
    resolved_sources = set()
    scope_artifacts = []
    for artifact, result in zip(manifest['artifacts'], results):
        name = artifact['filename']
        target = targets[name]
        evidence = {'filename': name, 'placement': str(target.absolute()),
                    'destination': str(target.parent.resolve() / target.name),
                    'sha256': artifact.get('sha256'), 'size_bytes': artifact.get('size_bytes')}
        if result['status'] == 'FOUND':
            resolved_sources.add(name)
            if target.exists() and target.resolve() != Path(result['path']).resolve():
                raise ValueError(f'placement would replace an existing file: {target}')
            print(f'REUSE: {name} -> {target} (from {result["path"]})')
            evidence.update(operation='reuse', reuse_path=str(Path(result['path']).resolve()))
            if getattr(args, 'plan_json', False) or getattr(args, 'approved_scope', None):
                # Unchecksummed completed files must match the durable receipt.
                actual_hash = artifact.get('sha256') or digest(Path(result['path']))
                evidence['reuse_sha256'] = actual_hash
                evidence['verified_completion'] = bool(previous and any(
                    entry['path'] == evidence['reuse_path'] and entry['sha256'] == actual_hash
                    and entry['size_bytes'] == Path(result['path']).stat().st_size
                    for entry in previous['artifacts']))
        else:
            if target.exists() or target.is_symlink():
                raise ValueError(f'artifact destination has different or unresolved contents: {target}; existing files are kept. Select a distinct declared model path/version.')
            if getattr(args, 'plan_json', False) or getattr(args, 'approved_scope', None):
                evidence['replace_sha256'] = digest(target) if target.is_file() else None
            sources = [overrides[name]] if name in overrides else [
                resolve_source(s['uri'], args) for s in artifact.get('sources', [])]
            if not sources and not args.yes and not getattr(args, 'dry_run', False):
                try:
                    uri = input(f'Source URL or local file path for {name} (blank cancels): ').strip()
                except EOFError:
                    uri = ''
                if uri:
                    overrides[name] = source_uri(uri)
                    sources = [overrides[name]]
            if not sources:
                print(f'NO SOURCE: {name}', file=sys.stderr)
                unresolved = True
            else:
                resolved_sources.add(name)
            local = bool(sources) and all(urlsplit(s).scheme == 'file' for s in sources)
            action = 'COPY LOCAL FILE' if local else 'DOWNLOAD' if sources else 'SOURCE NEEDED'
            size = artifact.get('size_bytes')
            size_label = f'{size / 1_000_000_000:.2f} GB' if size is not None else 'size not recorded'
            checks = ('verify SHA-256 against the package' if 'sha256' in artifact else
                      'package has no checksum to verify file identity')
            print(f'{action}: {name} ({size_label})\n  save to: {target}\n  verification: {checks}'
                  + ('\n  replaces the existing destination file' if target.exists() else ''))
            for source in sources:
                readable = url2pathname(urlsplit(source).path) if urlsplit(source).scheme == 'file' else source
                print(f'  {"local file" if urlsplit(source).scheme == "file" else "download from"}: {readable}')
            evidence.update(operation='acquire', sources=sources)
            evidence['local_sources'] = []
            if getattr(args, 'plan_json', False) or getattr(args, 'approved_scope', None):
                for source in sources:
                    if urlsplit(source).scheme == 'file':
                        source_path = Path(url2pathname(urlsplit(source).path))
                        evidence['local_sources'].append({'path': str(source_path.resolve()),
                                                         'sha256': digest(source_path)})
        scope_artifacts.append(evidence)
    print(f'WORKFLOW: save as {workflow_target}')
    user_inputs = [slot for slot in manifest.get('inputs', []) if slot['mode'] == 'user']
    if user_inputs:
        print('INPUT IMAGES/VIDEO: these files are not included. Select local files in the workflow before running it:')
    for slot in user_inputs:
        print(f'  USER INPUT: {slot["filename"]}')
    unverified_nodes = []
    for issue in manifest.get('unresolved', []):
        if issue.endswith(': source unavailable') and issue.removesuffix(': source unavailable') in resolved_sources:
            continue
        if issue.startswith('node ') and issue.endswith(': runtime package metadata unavailable'):
            unverified_nodes.append(issue[5:].removesuffix(': runtime package metadata unavailable'))
        else:
            print(f'NEEDS REVIEW: {issue}')
    if unverified_nodes:
        print('COMPATIBILITY NOT CHECKED: the export did not identify which Comfy version or node packages these nodes require.')
        print('  This does not mean the nodes are missing. Open the installed workflow to check availability.')
        print('  Nodes: ' + ', '.join(unverified_nodes))
    print('COMFY VERSION: this installation does not update ComfyUI.')
    if args.timeout <= 0:
        raise ValueError('timeout must be greater than zero')
    if getattr(args, 'plan_json', False) or getattr(args, 'approved_scope', None):
        from .installation_history import package_hash, within_scope
        python_scope = None
        if args.comfy_python:
            executable = args.comfy_python.expanduser().absolute()
            config = executable.parent.parent / 'pyvenv.cfg'
            python_scope = {'path': str(executable), 'resolved': str(executable.resolve()),
                            'sha256': digest(executable),
                            'venv': digest(config) if config.is_file() else None}
        scope = {'package': package_hash(args.package_root), 'artifacts': scope_artifacts,
                 'workflow': {'path': str(workflow_target.parent.resolve() / workflow_target.name),
                              'sha256': workflow_hash}, 'python': python_scope,
                 'nodes': [{**{key: str(node[key]) for key in ('name', 'source', 'revision', 'target')},
                            'operation': 'reuse' if node['existing'] else 'install'} for node in node_plan]}
        if getattr(args, 'repository_info', None):
            scope['repository'] = args.repository_info
        if getattr(args, 'plan_json', False):
            print('RR_PLAN ' + json.dumps(scope, sort_keys=True))
        if getattr(args, 'approved_scope', None):
            approved = json.loads(args.approved_scope.read_text())
            if not within_scope(approved, scope):
                raise ValueError('Installation plan changed; review and approve the revised plan.')
    if unresolved:
        return 1
    if getattr(args, 'dry_run', False):
        print('PLAN ONLY: nothing has been downloaded, copied, linked, or installed. Approve this plan to apply it.')
        return 0
    if not args.yes:
        try:
            approved = input('Apply downloads, artifact links, workflow placement, and listed code/environment changes? [y/N] ').strip().lower() in {'y', 'yes'}
        except EOFError:
            approved = False
        if not approved:
            print('Cancelled; no files changed.')
            return 1
    args.acquired = {}
    save_receipt(root, receipt)
    args.comfy_targets = targets
    args.destination = model_root
    args.source = [f'{name}={uri}' for name, uri in overrides.items()]
    args.yes = True
    completed = False
    try:
        for node in node_plan:
            acquire_node(node, args.timeout)
            record = {key: node[key] for key in ('name', 'source', 'revision')}
            record.update(path=str(node['target']), python=str(node['python']) if node['python'] else None, dependencies='pending')
            receipt['custom_nodes'] = [r for r in receipt['custom_nodes'] if r['name'] != node['name']] + [record]
            checkpoint('partial')
            record['dependencies'] = install_requirements(node, args.timeout)
            checkpoint('partial')
        code = install_artifacts(manifest, results, args)
        if code:
            return code
        for result in results:
            if result['status'] != 'FOUND':
                continue
            target = targets[result['filename']]
            source = Path(result['path']).resolve()
            if target.resolve() == source:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Do not overwrite a file introduced after preflight.
            target.symlink_to(source)
            print(f'LINKED: {target} -> {source}')
        for name, alias in aliases.items():
            target = targets[name].resolve()
            if alias.resolve() == target:
                continue
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.symlink_to(target)
            print(f'LINKED: {alias} -> {target}')
        workflow_root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=workflow_root, prefix='.renderrepo-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            if workflow_target.exists():
                if workflow_target.read_bytes() != data:
                    raise ValueError('workflow changed during installation')
            else:
                os.link(temporary, workflow_target)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        completed = not any(r.get('dependencies') == 'unverified' for r in receipt['custom_nodes'])
    finally:
        checkpoint("installed" if completed else "partial")
    print(f'INSTALLED WORKFLOW: {workflow_target}')
    print('Open the workflow in ComfyUI; user inputs and runtime compatibility remain your responsibility.')
    if node_plan:
        print('RESTART REQUIRED: restart ComfyUI to load installed nodes, then run doctor.')
    if getattr(args, 'comfy_url', None):
        from .runtime import doctor
        code = doctor(manifest, args, directories)
        if code and node_plan:
            print('Installation recorded; restart ComfyUI and rerun doctor before rendering.')
        return code
    if any(r.get('dependencies') == 'unverified' for r in receipt['custom_nodes']):
        return 3
    return 0

