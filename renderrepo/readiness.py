"""Read-only, evidence-based local readiness. Never acquires or executes anything."""
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re

from .pack import LOADERS, MEDIA, relative_path
from .runtime import graph_nodes, capability_json, git_provenance

LIMIT = 'Checks declared dependencies and explicit runtime requirements only; not execution, graph correctness, Python dependency health or hardware capacity.'


def inventory(manifest, graph):
    nodes = graph_nodes(graph)
    models = []
    inputs = list(manifest.get('inputs', []))
    unresolved = list(manifest.get('unresolved', []))
    for node in nodes:
        if node['type'] in MEDIA:
            values = node.get('widgets_values', [])
            value = (node['inputs'].get(MEDIA[node['type']]) if 'class_type' in node else
                     values[0] if isinstance(values, list) and values else None)
            if isinstance(value, str) and value:
                if not any(i['filename'] == value for i in inputs):
                    inputs.append({'filename':value, 'kind':'image' if node['type']=='LoadImage' else 'video', 'mode':'user'})
            else:
                unresolved.append('Dynamic or unset input selection: ' + node['type'])
    for node in nodes:
        for field, category, index in LOADERS.get(node['type'], []):
            values = node.get('widgets_values', [])
            value = (node['inputs'].get(field) if 'class_type' in node else
                     values.get(field) if isinstance(values, dict) else
                     values[index] if len(values) > index else None)
            models.append(dict(node=node['type'], field=field, category=category,
                               filename=value if isinstance(value, str) else None))
    return dict(nodes=sorted({n['type'] for n in nodes}), models=models,
                artifacts=manifest['artifacts'], inputs=inputs,
                runtime=manifest.get('runtime', {}), unresolved=unresolved)


def validate_requirements(requirements):
    if not isinstance(requirements, dict) or len(requirements) > 30:
        raise ValueError('runtime requirements must be a mapping (at most 30 fields)')
    for key, value in requirements.items():
        if key == 'device_types':
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                raise ValueError('device_types must list acceptable runtime device types')
        elif not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError('runtime version requirements must be nonempty text (at most 200 characters)')


def validate_inventory(value):
    keys = {'nodes', 'models', 'artifacts', 'inputs', 'runtime', 'unresolved'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('invalid dependency inventory fields')
    for key in ('nodes', 'unresolved'):
        if not isinstance(value[key], list) or not all(isinstance(v, str) and v for v in value[key]):
            raise ValueError('invalid dependency inventory ' + key)
    if not isinstance(value['runtime'], dict):
        raise ValueError('invalid dependency runtime')
    validate_requirements(value['runtime'].get('requirements', {}))
    from .nodes import validate_nodes
    validate_nodes(value['runtime'].get('custom_nodes', []))
    for key in ('models', 'artifacts', 'inputs'):
        if not isinstance(value[key], list) or not all(isinstance(v, dict) for v in value[key]):
            raise ValueError('invalid dependency inventory ' + key)
    for item in value['models']:
        if set(item) != {'node', 'field', 'category', 'filename'} or not all(isinstance(item[k], str) for k in ('node','field','category')) or item['filename'] is not None and not isinstance(item['filename'], str):
            raise ValueError('invalid model selection')
    for item in value['artifacts']:
        if set(item) - {'filename','target','sha256','size_bytes','sources'} or not isinstance(item.get('filename'), str):
            raise ValueError('invalid dependency artifact')
        relative_path(item['filename'])
        if '/' in item['filename']:
            raise ValueError('artifact filename must be a basename')
        if 'target' in item:
            relative_path(item['target'])
            if not item['target'].startswith(('models/', 'input/')) or Path(item['target']).name != item['filename']:
                raise ValueError('invalid dependency target')
        if 'sha256' in item and (not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-fA-F]{64}', item['sha256'])):
            raise ValueError('invalid dependency hash')
        if 'size_bytes' in item and (type(item['size_bytes']) is not int or item['size_bytes'] < 0):
            raise ValueError('invalid dependency size')
    for slot in value['inputs']:
        if set(slot) != {'filename','kind','mode'} or slot['kind'] not in {'image','video'} or slot['mode'] not in {'user','bundled'}:
            raise ValueError('invalid dependency input')
        relative_path(slot['filename'])
    return value


def snapshot(url, timeout=5):
    result = {'server': url, 'observed_at': datetime.now(timezone.utc).isoformat(), 'errors': {}}
    for key, route in [('nodes', '/object_info'), ('system', '/system_stats')]:
        try:
            result[key] = capability_json(url, route, timeout)
            if key == 'nodes' and not all(isinstance(v, dict) for v in result[key].values()):
                raise ValueError('Malformed node inventory')
            if key == 'system' and (not isinstance(result[key].get('system'), dict) or
                                    not isinstance(result[key].get('devices', []), list) or
                                    not all(isinstance(d, dict) for d in result[key].get('devices', []))):
                raise ValueError('Malformed system inventory')
        except (OSError, ValueError, TypeError, http.client.HTTPException) as error:
            result[key] = None
            result['errors'][key] = str(error)
    return result


def version_matches(actual, spec):
    # Deliberately small numeric contract; unsupported/prerelease comparisons stay unknown.
    actual = str(actual).split(' ')[0].split('+')[0]
    if not re.fullmatch(r'\d+(?:\.\d+)*', actual):
        return None
    results = []
    for clause in spec.split(','):
        match = re.fullmatch(r'\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)\s*', clause)
        if not match:
            return None
        left, right = [int(n) for n in actual.split('.')], [int(n) for n in match[2].split('.')]
        length = max(len(left), len(right)); left += [0]*(length-len(left)); right += [0]*(length-len(right))
        results.append({'==':left==right, '>=':left>=right, '<=':left<=right, '>':left>right, '<':left<right}[match[1]])
    return all(results)


def _evaluate(dependencies, observed, root=None, artifact_dirs=(), input_dir=None):
    checks = []
    def add(state, kind, detail): checks.append(dict(state=state, kind=kind, detail=detail))
    if dependencies is None:
        add('unknown', 'metadata', 'No dependency inventory available locally. No archive was retrieved.')
    else:
        validate_inventory(dependencies)
        info = observed.get('nodes')
        if not isinstance(info, dict) or not all(isinstance(v, dict) for v in info.values()):
            info = None
        for typ in dependencies['nodes']:
            add('unknown' if info is None else 'ready' if typ in info else 'missing', 'node', typ)
        for model in dependencies['models']:
            spec = (info or {}).get(model['node'], {}).get('input', {})
            choices = spec.get('required', {}).get(model['field'], spec.get('optional', {}).get(model['field'], []))
            choices = choices[0] if isinstance(choices, list) and choices else None
            name = model['filename']
            add('unknown' if name is None or not isinstance(choices, list) else 'ready' if name in choices else 'missing',
                'model visibility', f"{model['node']}.{model['field']} = {name}")
        roots = [Path(p) for p in artifact_dirs]
        if root:
            roots.extend([Path(root)/'models', Path(input_dir) if input_dir else Path(root)/'input'])
        roots = list(dict.fromkeys(p for p in roots if p.is_dir()))
        # Check selected Comfy placements first. A reusable file elsewhere cannot mask a broken target.
        from .cli import inspect_artifacts
        for artifact in dependencies['artifacts']:
            name = artifact['filename']; target = artifact.get('target')
            path = None
            if root and target:
                path = (Path(input_dir)/target[6:] if input_dir and target.startswith('input/') else Path(root)/target)
            candidates = [path] if path and path.is_file() else []
            if path and not candidates and target.startswith('input/'):
                add('missing', 'artifact', target + ' is not placed in Comfy'); continue
            if not candidates and roots:
                results = inspect_artifacts([artifact], roots)
                if results[0]['path']: candidates = [Path(results[0]['path'])]
                elif results[0]['failures']:
                    add('incompatible', 'artifact identity', name + ': ' + '; '.join(results[0]['failures'])); continue
                else:
                    add('missing', 'artifact', name); continue
            if not candidates:
                add('unknown', 'artifact identity', name + ': no local file evidence'); continue
            file = candidates[0]
            if 'size_bytes' in artifact and file.stat().st_size != artifact['size_bytes']:
                add('incompatible', 'artifact identity', name + ': size mismatch'); continue
            if 'sha256' not in artifact:
                add('unknown', 'artifact identity', name + ': no expected SHA-256'); continue
            with file.open('rb') as stream: actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            add('ready' if actual.lower() == artifact['sha256'].lower() else 'incompatible', 'artifact identity', name + ': SHA-256 comparison')
        for slot in dependencies['inputs']:
            loader = 'LoadImage' if slot['kind']=='image' else 'LoadVideo'
            specs = (info or {}).get(loader, {}).get('input', {}).get('required', {})
            choices = [s[0] for s in specs.values() if isinstance(s, list) and s and isinstance(s[0], list)]
            visible = any(slot['filename'] in c for c in choices)
            add('ready' if visible else 'missing' if choices else 'unknown', 'input selection', slot['filename'] + ' (select a replacement in Comfy if needed)')
        runtime = dependencies['runtime']
        system = (observed.get('system') or {}).get('system', {})
        for key, requirement in runtime.get('requirements', {}).items():
            if key == 'device_types':
                devices = (observed.get('system') or {}).get('devices')
                matches = None if not isinstance(devices, list) else any(d.get('type') in requirement for d in devices)
                actual = [d.get('type') for d in devices] if isinstance(devices, list) else 'unavailable'
            else:
                actual = system.get(key)
                matches = version_matches(actual, requirement) if actual is not None and key in {'python_version','comfyui_version','pytorch_version'} else None
            add('unknown' if matches is None else 'ready' if matches else 'incompatible', 'runtime requirement', f'{key}: requires {requirement}; observed {actual}')
        for node in runtime.get('custom_nodes', []):
            path = Path(root)/'custom_nodes'/node['name'] if root else None
            actual = git_provenance(path) if path and path.is_dir() else None
            state = ('unknown' if not root else 'missing' if actual is None else 'unknown' if not actual['revision'] or actual['dirty'] is not False else 'ready' if actual['revision']==node['revision'] else 'incompatible')
            add(state, 'custom node revision', node['name'] + ': requires ' + node['revision'])
        for issue in dependencies['unresolved']:
            add('unknown', 'unresolved dependency', issue)
    if observed.get('nodes') is None:
        add('unknown', 'server', 'Comfy node inventory unavailable: ' + observed.get('errors', {}).get('nodes','no response'))
    state = next((s for s in ('incompatible','missing','unknown') if any(c['state']==s for c in checks)), 'ready')
    system = (observed.get('system') or {}).get('system', {})
    runtime = {k:system.get(k) for k in ('comfyui_version','python_version','pytorch_version')}
    runtime['device_types'] = [d.get('type') for d in (observed.get('system') or {}).get('devices', [])]
    return dict(state=state, checked_at=observed['observed_at'], server=observed.get('server'), runtime=runtime, checks=checks, limitation=LIMIT)


def evaluate(dependencies, observed, root=None, artifact_dirs=(), input_dir=None):
    try:
        return _evaluate(dependencies, observed, root, artifact_dirs, input_dir)
    except OSError as error:
        report = _evaluate(None, observed)
        report['checks'].append({'state':'unknown', 'kind':'local evidence', 'detail':str(error)})
        return report


def command(manifest, args, directories):
    graph = json.loads((args.package_root/manifest['workflow']).read_text())
    report = evaluate(inventory(manifest, graph), snapshot(args.comfy_url,args.timeout), args.comfy_dir, directories, args.input_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print('Local readiness: ' + report['state'])
        print('Observed: ' + report['checked_at'] + ' — ' + report['server'])
        print('Runtime: ' + json.dumps(report['runtime']))
        for check in report['checks']: print(f"{check['state'].upper()} {check['kind']}: {check['detail']}")
        print(LIMIT)
    return {'ready':0,'missing':1,'incompatible':4,'unknown':3}[report['state']]
