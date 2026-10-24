"""Extract a conservative dependency inventory without altering workflow bytes."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .cli import inspect_artifacts

# Explicit loader mappings also support API exports without editor metadata.
LOADERS = {
    'UNETLoader': [('unet_name', 'diffusion_models', 0)],
    'VAELoader': [('vae_name', 'vae', 0)],
    'CLIPLoader': [('clip_name', 'text_encoders', 0)],
    'DualCLIPLoader': [('clip_name1', 'text_encoders', 0), ('clip_name2', 'text_encoders', 1)],
    'CLIPVisionLoader': [('clip_name', 'clip_vision', 0)],
    'CheckpointLoaderSimple': [('ckpt_name', 'checkpoints', 0)],
    'LoraLoader': [('lora_name', 'loras', 0)],
    'LoraLoaderModelOnly': [('lora_name', 'loras', 0)],
}
MEDIA = {'LoadImage': 'image', 'LoadVideo': 'file'}


def relative_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\0' in value
            or Path(value).is_absolute() or any(p in {'.', '..'} for p in value.split('/'))):
        raise ValueError(f'unsafe relative path: {value!r}')
    return value


def instance_definition(instance, definition, unresolved=None):
    """Apply named values only through explicit subgraph input-to-widget links.

    Model records describe defaults, so their URLs must still pass the
    selected-name check below. Never assign a default model URL to a variant.
    """
    if unresolved is None:
        unresolved = set()
    result = copy.deepcopy(definition)
    values = instance.get('widgets_values_named', instance.get('widgets_values', {}))
    if not isinstance(values, dict):
        if values:
            unresolved.add(f'subgraph {instance.get("id")}: positional overrides require manual inventory')
        return result
    links = {link.get('id'): link for link in result.get('links', []) if isinstance(link, dict)}
    nodes = {node.get('id'): node for node in result['nodes']}
    applied = set()
    for slot, entry in enumerate(result.get('inputs', [])):
        field = entry.get('name')
        if field not in values:
            continue
        for link_id in entry.get('linkIds', []):
            link = links.get(link_id, {})
            node = nodes.get(link.get('target_id'), {})
            target_slot = link.get('target_slot')
            if (link.get('origin_id') != result.get('inputNode', {}).get('id')
                    or link.get('origin_slot') != slot or not isinstance(target_slot, int)
                    or target_slot < 0 or target_slot >= len(node.get('inputs', []))):
                continue
            target = node['inputs'][target_slot]
            widget = target.get('widget', {}).get('name')
            if target.get('link') != link_id or not widget:
                continue
            for loader_field, _, index in LOADERS.get(node.get('type'), []):
                if widget != loader_field:
                    continue
                selected = node.setdefault('widgets_values', [])
                if isinstance(selected, dict):
                    selected[widget] = values[field]
                elif isinstance(selected, list):
                    selected.extend([None] * max(0, index + 1 - len(selected)))
                    selected[index] = values[field]
                else:
                    continue
                applied.add(field)
    for field, value in values.items():
        if (field not in applied and isinstance(value, str)
                and value.endswith(('.safetensors', '.ckpt', '.pt', '.bin', '.gguf'))):
            unresolved.add(f'subgraph {instance.get("id")}: override {field}={value} requires manual inventory')
    return result


def discover(graph):
    artifacts, inputs, annotations, unresolved = {}, {}, set(), set()
    definitions = {}
    def register(g):
        for sub in g.get('definitions', {}).get('subgraphs', []):
            definitions[sub['id']] = sub
            register(sub)
    register(graph)

    def add(name, directory, url=None):
        relative_path(name)
        relative_path(directory)
        filename = Path(name).name
        target = f'models/{directory}/{name}'
        if filename in artifacts and artifacts[filename]['target'] != target:
            raise ValueError(f'{filename}: conflicting installation paths; rename or author manifest manually')
        item = artifacts.setdefault(filename, {'filename': filename, 'target': target, 'sources': []})
        if url and {'uri': url} not in item['sources']:
            item['sources'].append({'uri': url})

    def visit(g, ancestors=frozenset()):
        nodes = g.get('nodes')
        if nodes is None:
            nodes = [dict(n, id=i, type=n['class_type']) for i, n in g.items()]
        for n in nodes:
            typ = n.get('type', '')
            if typ in definitions:
                if typ in ancestors:
                    unresolved.add(f'subgraph {typ}: recursive definition requires manual inventory')
                else:
                    visit(instance_definition(n, definitions[typ], unresolved), ancestors | {typ})
                continue
            props = n.get('properties', {})
            if props.get('cnr_id'):
                annotations.add((props['cnr_id'], str(props.get('ver', 'unknown'))))
            elif typ not in {'MarkdownNote', 'Note', 'Reroute'}:
                unresolved.add(f'node {typ}: runtime package metadata unavailable')
            records = props.get('models', [])
            widgets = n.get('widgets_values', [])
            selected = []
            for field, directory, index in LOADERS.get(typ, []):
                value = (n['inputs'].get(field) if 'class_type' in n else
                         widgets.get(field) if isinstance(widgets, dict) else
                         widgets[index] if len(widgets) > index else None)
                if isinstance(value, str) and value:
                    selected.append(value)
                    add(value, directory)
                elif value is not None:
                    unresolved.add(f'node {typ}: dynamic {field} requires manual inventory')
            for record in records:
                name = record['name']
                if selected and name not in selected:
                    unresolved.add(f'node {typ}: stale model metadata {name}; selected {selected}')
                    continue
                add(name, record['directory'], record.get('url'))
            if typ in MEDIA:
                value = (n['inputs'].get(MEDIA[typ]) if 'class_type' in n else
                         widgets[0] if isinstance(widgets, list) and widgets else None)
                if isinstance(value, str) and value:
                    relative_path(value)
                    inputs[value] = {'filename': value, 'kind': 'video' if typ == 'LoadVideo' else 'image',
                                     'mode': 'user'}
                else:
                    unresolved.add(f'node {typ}: input filename unavailable')
    visit(graph)
    for item in artifacts.values():
        if not item['sources']:
            unresolved.add(f'{item["filename"]}: source unavailable')
    return list(artifacts.values()), list(inputs.values()), {
        'node_packages': [{'id': p, 'recorded_version': v} for p, v in sorted(annotations)],
        'compatibility': 'unverified',
    }, sorted(unresolved)


def pack_workflow(args, directories):
    data = args.workflow.read_bytes()
    graph = json.loads(data)
    if not isinstance(graph, dict) or not graph:
        raise ValueError('workflow must be a nonempty ComfyUI JSON object')
    if 'nodes' in graph:
        if not isinstance(graph['nodes'], list) or not graph['nodes']:
            raise ValueError('workflow nodes must be a nonempty list')
    elif not all(isinstance(n, dict) and isinstance(n.get('class_type'), str)
                 and isinstance(n.get('inputs'), dict) for n in graph.values()):
        raise ValueError('workflow must be an editor graph or API prompt graph')
    try:
        artifacts, inputs, runtime, unresolved = discover(graph)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f'malformed workflow dependency metadata: {error}') from error
    if getattr(args, 'runtime_requirements', None):
        from .readiness import validate_requirements
        requirements = json.loads(args.runtime_requirements.read_text())
        validate_requirements(requirements)
        runtime['requirements'] = requirements
    if getattr(args, 'comfy_dir', None):
        from .runtime import capture_runtime
        runtime['local_installation'] = capture_runtime(args.comfy_dir)
        from .nodes import export_nodes
        runtime['custom_nodes'] = export_nodes(args, runtime['local_installation'])
        if runtime['custom_nodes']:
            selected = {n['name'] for n in runtime['custom_nodes']}
            runtime['local_installation']['custom_nodes'] = [n for n in runtime['local_installation']['custom_nodes'] if n['name'] in selected]
        for folder in (args.comfy_dir / 'models', args.comfy_dir / 'input'):
            folder = folder.expanduser().resolve()
            if folder.is_dir() and folder not in directories:
                directories.append(folder)
    if (getattr(args, 'custom_node', None) or getattr(args, 'node_source', None)) and not args.comfy_dir:
        raise ValueError('--custom-node and --node-source require --comfy-dir')
    bundled = {}
    for name in args.bundle_input or []:
        match = next((i for i in inputs if i['filename'] == name), None)
        if match is None or name in bundled:
            raise ValueError(f'--bundle-input must name a unique detected input: {name}')
        candidates = [root / name for root in directories if (root / name).is_file()]
        if not candidates:
            raise ValueError(f'input not found in configured roots: {name}')
        source = candidates[0]
        filename = Path(name).name
        if any(a['filename'] == filename for a in artifacts):
            raise ValueError(f'input basename conflicts with artifact: {filename}')
        with source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        artifacts.append({'filename': filename, 'target': f'input/{name}',
                          'size_bytes': source.stat().st_size, 'sha256': digest,
                          'sources': [{'uri': f'package:assets/{name}'}]})
        match['mode'] = 'bundled'
        bundled[name] = source
    results = inspect_artifacts(artifacts, directories)
    for item, result in zip(artifacts, results):
        if result['status'] == 'FOUND' and 'sha256' not in item:
            path = Path(result['path'])
            item['size_bytes'] = path.stat().st_size
            with path.open('rb') as stream:
                item['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        if 'sha256' not in item:
            unresolved.append(f'{item["filename"]}: local identity unavailable; size/SHA-256 omitted')
    manifest = {'schema_version': 0, 'name': args.name or args.workflow.stem,
                'version': args.version, 'workflow': 'workflow.json', 'artifacts': artifacts, 'inputs': inputs,
                'runtime': runtime, 'unresolved': sorted(set(unresolved))}
    description = getattr(args, 'description', '')
    if not isinstance(description, str):
        raise ValueError('description must be a string')
    if description:
        manifest['description'] = description
    discovery = getattr(args, 'discovery', None)
    if discovery:
        from .discovery import validate_discovery
        manifest['discovery'] = validate_discovery(json.loads(Path(discovery).read_text()))
    if not manifest.get('discovery', {}).get('tasks'):
        print('Discovery notice: no task phrases supplied; search will use the name/description.')
    from .capabilities import analyze
    manifest['capabilities'] = analyze(json.loads(data))
    output = args.output.expanduser().absolute()
    if output.exists():
        raise ValueError(f'output already exists: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.renderrepo-pack-') as temporary:
        stage = Path(temporary) / 'package'
        stage.mkdir()
        (stage / 'workflow.json').write_bytes(data)
        for name, source in bundled.items():
            target = stage / 'assets' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            expected = next(a for a in artifacts if a.get('target') == f'input/{name}')
            with target.open('rb') as stream:
                copied_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            if copied_hash != expected['sha256'] or target.stat().st_size != expected['size_bytes']:
                raise ValueError(f'input changed while packing: {name}')
        (stage / 'manifest.yaml').write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        from .cli import load_package
        load_package(stage)
        from .capsule import seal, parent_descriptor
        predecessor = getattr(args, 'predecessor', None)
        parent = None
        if predecessor:
            from .archive import package_directory
            with package_directory(predecessor) as previous:
                parent = parent_descriptor(previous if previous.is_dir() else previous.parent)
        seal(stage, manifest, predecessor=parent['sha256'] if parent else None,
             provenance=parent['provenance'] if parent else None)
        if output.suffix == '.rrpkg':
            from .archive import write_archive
            archive = Path(temporary) / 'package.rrpkg'
            write_archive(stage, archive)
            archive.rename(output)
        else:
            stage.rename(output)
    print(f'Packed: {output} ({len(artifacts)} artifacts, {len(inputs)} input slots)')
    for issue in manifest['unresolved']:
        print(f'UNRESOLVED: {issue}')
    print('Runtime compatibility unverified; review manifest before publication.')
    return 0
