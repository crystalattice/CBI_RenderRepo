"""Conservative, output-connected workflow descriptions; never execution qualification.

Semantics are intentionally finite. Core image nodes (nodes.py/nodes_images.py),
conditioning/sampling, and Comfy's Qwen edit encoders (nodes_qwen.py) are mapped.
Unknown nodes are opaque, even when their socket names sound familiar.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from .pack import LOADERS as MODEL_LOADERS

VERSION = 2
ACTIONS = {'create-image': 'Create Image', 'edit-image': 'Edit Image',
           'resize-crop': 'Resize & Crop', 'combine-images': 'Combine Images',
           'create-video': 'Create Video'}
OUTPUTS = {'SaveImage': 'image', 'PreviewImage': 'image', 'SaveAnimatedWEBP': 'animation',
           'SaveAnimatedPNG': 'animation', 'SaveVideo': 'video'}
LOADERS = {'LoadImage': 'image', 'LoadImageMask': 'image', 'LoadVideo': 'video'}
ENCODERS = {'CLIPTextEncode': 'text', 'TextEncodeQwenImageEdit': 'prompt',
            'TextEncodeQwenImageEditPlus': 'prompt'}
PROCESSING = {'ImageScale': 'Resize image', 'ImageScaleBy': 'Scale image',
              'ImageCrop': 'Crop image', 'ImageCropV2': 'Crop image',
              'ImageCompositeMasked': 'Overlay images', 'ImageBatch': 'Combine images into a batch',
              'ImageUpscaleWithModel': 'Upscale with a model'}
CONTROLS = {'EmptyLatentImage':['width','height','batch_size'], 'EmptySD3LatentImage':['width','height','batch_size'],
            'ImageScale': ['width', 'height', 'upscale_method', 'crop'],
            'ImageScaleBy': ['scale_by', 'upscale_method'],
            'ImageCrop': ['width', 'height', 'x', 'y'], 'ImageCropV2': ['width', 'height', 'x', 'y'],
            'ImageCompositeMasked': ['x', 'y', 'resize_source'],
            'KSampler': ['seed', 'steps', 'cfg', 'denoise'],
            'KSamplerAdvanced': ['noise_seed', 'steps', 'cfg', 'start_at_step', 'end_at_step']}
CONTROL_LABELS = {'cfg':'Guidance (CFG)', 'upscale_method':'Resize method',
                  'unet_name':'Diffusion model', 'clip_name':'Text encoder', 'vae_name':'VAE',
                  'crop':'Crop mode', 'x':'Horizontal offset', 'y':'Vertical offset',
                  'resize_source':'Resize overlay to base', 'scale_by':'Scale factor'}
WIDGETS = {'EmptyLatentImage':['width','height','batch_size'], 'EmptySD3LatentImage':['width','height','batch_size'],
           'SaveImage': ['filename_prefix'],
           'UNETLoader': ['unet_name', 'weight_dtype'], 'CLIPLoader': ['clip_name', 'type', 'device'],
           'VAELoader': ['vae_name'], 'CheckpointLoaderSimple': ['ckpt_name'],
           'LoadImage': ['image', 'upload'], 'LoadImageMask': ['image', 'channel', 'upload'],
           'CLIPTextEncode': ['text'], 'TextEncodeQwenImageEdit': ['prompt'],
           'TextEncodeQwenImageEditPlus': ['prompt'], 'PrimitiveNode': ['value'], 'ComfySwitchNode': ['switch'], 'ComfySoftSwitchNode': ['switch'],
           'ImageScale': ['upscale_method', 'width', 'height', 'crop'],
           'ImageScaleBy': ['upscale_method', 'scale_by'],
           'ImageCrop': ['width', 'height', 'x', 'y'],
           'ImageCompositeMasked': ['x', 'y', 'resize_source'],
           'KSampler': ['seed', 'control_after_generate', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise']}
CONDITIONING = {'ConditioningCombine', 'ConditioningAverage', 'ConditioningConcat',
                'ConditioningSetArea', 'ConditioningSetMask', 'ConditioningZeroOut'}
KNOWN = (set(OUTPUTS) | set(LOADERS) | set(ENCODERS) | set(PROCESSING) | CONDITIONING |
         {'KSampler', 'KSamplerAdvanced', 'VAEDecode', 'VAEEncode', 'VAEEncodeForInpaint',
          'EmptyLatentImage', 'EmptySD3LatentImage', 'CheckpointLoaderSimple', 'CLIPLoader',
          'DualCLIPLoader', 'UNETLoader', 'VAELoader', 'UpscaleModelLoader', 'LoraLoader',
          'LoraLoaderModelOnly', 'ModelSamplingFlux', 'ModelSamplingAuraFlow', 'FluxGuidance', 'ReferenceLatent',
          'PrimitiveNode', 'Reroute', 'Note', 'MarkdownNote', 'ComfySwitchNode', 'ComfySoftSwitchNode'})


def graph_digest(graph):
    """Semantic JSON digest: whitespace/key order changes do not stale the summary."""
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def validate_summary(value):
    # Cached publisher data is never executed. Bound size and validate the render contract.
    if (not isinstance(value, dict) or type(value.get('analysis_version')) is not int or value.get('analysis_version') not in {1, VERSION} or
            not re.fullmatch('[0-9a-f]{64}', str(value.get('workflow_sha256', ''))) or
            len(json.dumps(value)) > 128 * 1024):
        raise ValueError('invalid workflow capability summary')
    for key in ('actions', 'inputs', 'prompts', 'controls', 'processing', 'outputs', 'uncertainties', 'evidence'):
        if not isinstance(value.get(key), list):
            raise ValueError('invalid workflow capability summary: ' + key)
    if not all(isinstance(a,str) and a in ACTIONS for a in value['actions']):
        raise ValueError('unknown workflow action')
    if not isinstance(value.get('purpose'), str):
        raise ValueError('invalid capability purpose')
    for key in ('inputs', 'prompts', 'controls', 'processing'):
        if not all(isinstance(i, dict) and isinstance(i.get('label'), str) for i in value[key]):
            raise ValueError('invalid capability ' + key)
    if not all(isinstance(s, str) for s in value['outputs'] + value['uncertainties']):
        raise ValueError('invalid capability evidence text')
    return value


@dataclass
class BoundValue:
    value: object
    node: str
    field: str


def analyze(graph, node_schema=None):
    nodes, definitions, issues = {}, {}, set()
    def register(g):
        for sub in g.get('definitions', {}).get('subgraphs', []):
            definitions[str(sub['id'])] = sub
            register(sub)
    register(graph)

    # Normalize links without serializing/rewriting the source graph. Expand only
    # instantiated subgraphs, namespace every instance, preserve boundary slots.
    def build(g, prefix='', external=None, ancestry=(), overrides_unknown=False):
        local, links = {}, {}
        editor = 'nodes' in g
        for link in g.get('links', []) if editor else []:
            if isinstance(link, list) and len(link) >= 5:
                links[str(link[0])] = (str(link[1]), link[2])
            elif isinstance(link, dict):
                links[str(link['id'])] = (str(link['origin_id']), link['origin_slot'])
        source_nodes = g['nodes'] if editor else [dict(v, id=k, type=v['class_type']) for k, v in g.items()]
        for n in source_nodes:
            key = prefix + str(n['id'])
            values, refs = {}, {}
            if editor:
                widgets = n.get('widgets_values', [])
                fields = ([i['name'] for i in definitions[n['type']].get('inputs', [])]
                          if n['type'] in definitions else WIDGETS.get(n['type'], []))
                values = dict(widgets) if isinstance(widgets, dict) else dict(zip(fields, widgets or []))
                values.update(n.get('widgets_values_named') or {})
                for i in n.get('inputs', []):
                    if i.get('link') is not None and str(i['link']) in links:
                        refs[i['name']] = links[str(i['link'])]
            else:
                for name, v in n['inputs'].items():
                    if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) and str(v[0]) in g:
                        refs[name] = (str(v[0]), v[1])
                    else:
                        values[name] = v
            if overrides_unknown: values = {}
            local[str(n['id'])] = dict(id=key, type=n['type'], refs=refs, values=values, bindings={},
                                        mode=n.get('mode', 0), raw=n)
        expanded = {}
        resolving = set()
        def resolve(node_id, slot=0):
            if str(node_id) == str((g.get('inputNode') or {}).get('id', -10)) and external is not None:
                return external[slot]() if slot in external else None
            n = local.get(str(node_id))
            if n is None:
                issues.add('Unresolved graph link at ' + prefix + str(node_id)); return None
            token = (node_id, slot)
            if token in resolving:
                issues.add('Cyclic or recursive graph path at ' + n['id']); return None
            resolving.add(token)
            try:
                if n['mode'] == 2:  # Comfy NEVER
                    issues.add('Muted node on an output path: ' + n['id']); return None
                if n['mode'] == 4 or n['type'] == 'Reroute':
                    # Bypass requires matching socket type, not arbitrary first input.
                    raw = n['raw']; outs = raw.get('outputs', [])
                    typ = outs[slot].get('type') if len(outs) > slot else None
                    matches = [i['name'] for i in raw.get('inputs', []) if typ and i.get('type') == typ and i['name'] in n['refs']]
                    if len(matches) == 1:
                        return resolve(*n['refs'][matches[0]])
                    issues.add('Ambiguous bypass at ' + n['id']); return None
                if n['type'] in definitions:
                    if n['type'] in ancestry or len(ancestry) >= 16:
                        issues.add('Recursive subgraph at ' + n['id']); return None
                    if n['id'] not in expanded:
                        sub = definitions[n['type']]
                        # Definition inputs include exposed widgets absent from the
                        # instance's socket list. Carry literals across the boundary
                        # without inventing executable nodes or losing their origin.
                        entries = sub.get('inputs') or n['raw'].get('inputs', [])
                        ext = {}
                        for i, inp in enumerate(entries):
                            field = inp['name']
                            if field in n['refs']:
                                ext[i] = lambda ref=n['refs'][field]: resolve(*ref)
                            elif field in n['values']:
                                bound = n['bindings'].get(field, BoundValue(n['values'][field], n['id'], field))
                                ext[i] = lambda bound=bound: bound
                        unmapped = set(n['values']) - {inp['name'] for inp in entries}
                        opaque = bool(n['raw'].get('properties', {}).get('proxyWidgets')) or bool(unmapped) or bool(n['raw'].get('widgets_values')) and not sub.get('inputs')
                        expanded[n['id']] = build(sub, n['id']+'/', ext, (*ancestry, n['type']), opaque)
                        if opaque:
                            issues.add('Subgraph widget overrides are not interpreted at ' + n['id'])
                    result = expanded[n['id']](slot)
                    if result is None: issues.add('Unresolved subgraph output at ' + n['id'])
                    return result
                if n['type'] in {'ComfySwitchNode', 'ComfySoftSwitchNode'}:
                    choice = n['values'].get('switch')
                    connected = [p for p in ('on_false', 'on_true') if p in n['refs']]
                    selected = ('on_true' if choice else 'on_false') if isinstance(choice, bool) and 'switch' not in n['refs'] else None
                    if n['type'] == 'ComfySoftSwitchNode' and len(connected) == 1:
                        selected = connected[0]
                    if selected is not None:
                        if selected in n['refs']: return resolve(*n['refs'][selected])
                        issues.add('Selected switch branch is empty at '+n['id']); return None
                    issues.add('Mode-dependent switch at '+n['id']+': connected alternatives shown; active branch is unresolved.')
                if n['id'] not in nodes:
                    nodes[n['id']] = {**n, 'refs': {}}
                    for field, ref in n['refs'].items():
                        resolved = resolve(*ref)
                        if isinstance(resolved, BoundValue):
                            nodes[n['id']]['values'][field] = resolved.value
                            nodes[n['id']]['bindings'][field] = resolved
                        elif resolved is not None:
                            nodes[n['id']]['refs'][field] = resolved
                        else:
                            nodes[n['id']]['values'].pop(field, None)
                return (n['id'], slot)
            finally:
                resolving.remove(token)
        if external is not None:
            boundary = {}
            out_id = str((g.get('outputNode') or {}).get('id', -20))
            for link in g.get('links', []):
                if isinstance(link, list) and len(link) >= 5 and str(link[3]) == out_id:
                    boundary[link[4]] = (str(link[1]), link[2])
                elif isinstance(link, dict) and str(link.get('target_id')) == out_id:
                    boundary[link['target_slot']] = (str(link['origin_id']), link['origin_slot'])
            # Side-effect output nodes inside the instantiated subgraph are roots too.
            for nid, n in local.items():
                if n['type'] in OUTPUTS and n['mode'] not in {2, 4}: resolve(nid)
            return lambda slot: resolve(*boundary[slot]) if slot in boundary else None
        for nid, n in local.items():
            if n['type'] in OUTPUTS and n['mode'] not in {2, 4}: resolve(nid)
            elif n['type'] in definitions and n['mode'] not in {2, 4} and has_output(definitions[n['type']]): resolve(nid)
        return {}
    def has_output(g, seen=()):
        return any(n.get('mode',0) not in {2,4} and (n['type'] in OUTPUTS or n['type'] in definitions and n['type'] not in seen and has_output(definitions[n['type']],(*seen,n['type']))) for n in g.get('nodes',[]))
    try:
        build(graph)
    except RecursionError:
        nodes.clear()
        issues.add('Graph traversal exceeded the supported nesting/path depth; input details are unknown.')
    if not any(n['type'] in OUTPUTS for n in nodes.values()):
        issues.add('No supported active output node; inputs and task intent cannot be established.')

    def ancestors(ref, allowed=None):
        found, todo = set(), [ref[0]] if ref else []
        while todo:
            k = todo.pop()
            if k in found: continue
            found.add(k)
            n = nodes[k]
            if allowed is None or n['type'] in allowed:
                todo.extend(r[0] for r in n['refs'].values())
        return found
    consumers = {}
    for n in nodes.values():
        for port, ref in n['refs'].items(): consumers.setdefault(ref[0], []).append((n, port))
    unknown = [n for n in nodes.values() if n['type'] not in KNOWN]
    incomplete = bool(unknown) or any(any(word in i for word in ('Unresolved', 'Ambiguous', 'Cyclic', 'Recursive', 'exceeded', 'No supported')) for i in issues)
    for n in unknown:
        issues.add('Unknown behavior: '+n['type']+' (node '+n['id']+'); input necessity, branch selection and additional inputs are unknown.')
    inputs, prompts, controls, processing, evidence = [], [], [], [], []
    actions = set()
    samplers = [n for n in nodes.values() if n['type'] in {'KSampler', 'KSamplerAdvanced'}]
    roles = {}
    polarity_basis = {}
    for sampler in samplers:
        for role in ('positive', 'negative'):
            if node_schema is not None and sampler['type'] in node_schema:
                schema = node_schema[sampler['type']].get('input', {})
                spec = schema.get('required', {}).get(role, schema.get('optional', {}).get(role))
                if schema and (not isinstance(spec, (list, tuple)) or not spec or spec[0] != 'CONDITIONING'):
                    issues.add('Target conditioning input schema does not establish '+role+' at '+sampler['id'])
                    continue
                basis = 'graph connections and target /object_info input schema' if schema else 'graph connections and built-in node semantics'
            else:
                basis = 'graph connections and built-in node semantics'
            # ZeroOut consumes conditioning for its shape, but removes its text
            # content. Do not assign negative polarity to a shared positive encoder.
            for k in ancestors(sampler['refs'].get(role), (CONDITIONING - {'ConditioningZeroOut'}) | set(ENCODERS) | {'FluxGuidance', 'ReferenceLatent'}):
                if nodes[k]['type'] in ENCODERS: roles.setdefault(k, set()).add(role)
                if nodes[k]['type'] in ENCODERS: polarity_basis[k] = basis
    for n in nodes.values():
        typ, key = n['type'], n['id']
        evidence.append({'node': key, 'type': typ, 'reason': 'Connected to supported output'})
        if typ in LOADERS:
            downstream = [dest for dest, _ in consumers.get(key, [])]
            # A loader is required by this saved graph even when the consuming
            # node declares an optional socket. Unconnected optional sockets are
            # optional capabilities; connected loaders must still load a file.
            inputs.append({'node': key, 'kind': LOADERS[typ], 'label': LOADERS[typ].capitalize()+' input',
                           'requirement': 'unknown' if incomplete else 'mode-dependent' if any('Mode-dependent switch' in i for i in issues) else 'required',
                           'role': 'reference' if downstream and all(d['type'] in {'TextEncodeQwenImageEdit', 'TextEncodeQwenImageEditPlus'} for d in downstream) else next(({'destination':'base','source':'overlay'}[port] for dest, port in consumers.get(key, []) if dest['type']=='ImageCompositeMasked' and port in {'destination','source'}), 'input')})
        if typ in ENCODERS:
            field = ENCODERS[typ]; traced = roles.get(key, set())
            role = next(iter(traced)) if len(traced) == 1 else 'unknown'
            label = ('Editing instructions' if typ.startswith('TextEncodeQwenImageEdit') and role == 'positive' else
                     role.capitalize()+' prompt' if role != 'unknown' else 'Text prompt (role unknown)')
            prompts.append({'node': key, 'field': field, 'role': role, 'label': label, 'requirement': 'may be blank' if role == 'negative' else 'editable',
                            'role_evidence': polarity_basis.get(key, 'unresolved graph path')})
            # Boundary resolution has already applied the effective outer value.
            # A runtime-connected text input must not expose its stale widget default.
            if field not in n['refs'] and isinstance(n['values'].get(field), str):
                prompts[-1]['value'] = n['values'][field]
                binding = n['bindings'].get(field)
                prompts[-1]['value_source'] = ({'node':binding.node,'field':binding.field} if binding
                                               else {'node':key,'field':field})
            if role == 'unknown': issues.add('Prompt polarity is not established at node '+key)
            if typ.startswith('TextEncodeQwenImageEdit'):
                fields = ['image'] if typ == 'TextEncodeQwenImageEdit' else ['image1','image2','image3']
                empty = [f for f in fields if f not in n['refs']]
                if empty: inputs.append({'node': key, 'kind': 'image', 'label': 'Additional reference image sockets', 'requirement': 'optional', 'slots': len(empty)})
                processing.append({'node': key, 'label': 'Automatic reference resizing inside the edit encoder', 'exposed': False})
                if role == 'positive' and any(f in n['refs'] for f in fields): actions.add('edit-image')
        if typ in PROCESSING:
            # A geometric operation feeds final pixels directly (possibly via
            # more image operations), or is internal preprocessing into an encoder.
            internal = any(nodes[k]['type'] in {'VAEEncode', 'VAEEncodeForInpaint', *ENCODERS} for k in nodes
                           if key in ancestors((k, 0)) and k != key)
            processing.append({'node': key, 'label': PROCESSING[typ], 'exposed': not internal})
            if not internal:
                if typ in {'ImageScale', 'ImageScaleBy', 'ImageCrop', 'ImageCropV2'}: actions.update({'resize-crop','edit-image'})
                if typ in {'ImageCompositeMasked', 'ImageBatch'}: actions.update({'combine-images','edit-image'})
                if typ == 'ImageUpscaleWithModel': actions.add('edit-image')
            if typ == 'ImageScale':
                crop = n['values'].get('crop')
                if crop == 'center': processing.append({'node':key,'label':'Center crop to the selected dimensions','exposed':not internal})
                elif crop == 'disabled': processing.append({'node':key,'label':'Resizes to width/height; may stretch when both are set','exposed':not internal})
        control_fields = CONTROLS.get(typ, []) + [f for f, _, _ in MODEL_LOADERS.get(typ, [])]
        if control_fields:
            exposed = typ in MODEL_LOADERS or typ.startswith('KSampler') or typ in {'EmptyLatentImage','EmptySD3LatentImage'} or any(p['node'] == key and p['exposed'] for p in processing)
            if exposed:
                for field in control_fields:
                    if field in n['values'] and field not in n['refs']:
                        # A subgraph's internal defaults are not outer controls.
                        if '/' in key and field not in n['bindings']:
                            continue
                        label = CONTROL_LABELS.get(field, field.replace('_',' ').capitalize())
                        if typ in {'EmptyLatentImage','EmptySD3LatentImage'} and field in {'width','height'}:
                            label = 'Output '+field
                        binding = n['bindings'].get(field)
                        controls.append({'node': key, 'field': field, 'label': label, 'value': n['values'][field],
                                         'exposed_at': {'node':binding.node,'field':binding.field} if binding else {'node':key,'field':field}})
        if typ not in KNOWN:
            # Do not call unknown text sockets instructions or assign polarity.
            for inp in n['raw'].get('inputs', []) if 'class_type' not in n['raw'] else []:
                if inp.get('type') == 'STRING':
                    prompts.append({'node':key,'field':inp['name'],'role':'unknown','label':inp['name']+' (text; role unknown)','requirement':'unknown'})
    for sampler in samplers:
        if any(nodes[k]['type'] in {'VAEEncode','VAEEncodeForInpaint'} for k in ancestors(sampler['refs'].get('latent_image'))):
            actions.add('edit-image')
            processing.append({'node': sampler['id'], 'label': 'Image-conditioned sampling', 'exposed': True})
        elif any(nodes[k]['type'] in {'EmptyLatentImage','EmptySD3LatentImage'} for k in ancestors(sampler['refs'].get('latent_image'))):
            if 'edit-image' not in actions: actions.add('create-image')
    if unknown:
        # Known operations remain visible, but an opaque route cannot verify intent.
        issues.add('Counts describe connected visible inputs only; opaque nodes may require other inputs.')
    outputs = sorted({OUTPUTS[n['type']] for n in nodes.values() if n['type'] in OUTPUTS})
    if 'edit-image' in actions and any(n['type'].startswith('TextEncodeQwenImageEdit') for n in nodes.values()):
        purpose = 'Reference-guided image editing'
    elif 'edit-image' in actions and samplers:
        purpose = 'Image editing with sampling'
    elif 'create-image' in actions and any(p['role']=='positive' for p in prompts) and not inputs:
        purpose = 'Generate an image from a text prompt'
        # A graph label identifies the author's model family, not validated
        # architecture or compatibility. Require an output-connected model loader.
        model_instances = {n['id'].split('/')[0] for n in nodes.values() if n['type']=='UNETLoader' and '/' in n['id']}
        labels = []
        for instance in graph.get('nodes', []):
            if str(instance.get('id')) in model_instances and instance['type'] in definitions:
                name = definitions[instance['type']].get('name', '')
                match = re.search(r'\(([^()]+)\)\s*$', name)
                if match:
                    labels.append(match[1])
        if len(set(labels)) == 1:
            purpose += ' using '+labels[0]
    elif processing:
        purpose = ' / '.join(dict.fromkeys(p['label'] for p in processing if p['exposed'])) or 'Image preprocessing'
    else:
        purpose = 'Create image' if 'create-image' in actions else 'Workflow inputs and outputs'
    for kind in ('image','video'):
        slots = [i for i in inputs if i['kind']==kind and i['requirement']!='optional']
        for index, slot in enumerate(slots,1):
            if len(slots)>1: slot['label'] = kind.capitalize()+' '+str(index)
    counts = {}
    for kind in ('image', 'video'):
        connected = [i for i in inputs if i['kind'] == kind and i['requirement'] != 'optional']
        counts[kind] = {'min': 0 if incomplete else sum(i['requirement'] == 'required' for i in connected),
                        'max': None if incomplete else len(connected), 'connected': len(connected)}
    # Describe known generation operations in dependency order, independently
    # from the geometric task classification above.
    steps = {**{t:'Text encoding' for t in ENCODERS},
             'EmptyLatentImage':'Empty latent', 'EmptySD3LatentImage':'Empty latent',
             'KSampler':'Sampling', 'KSamplerAdvanced':'Sampling', 'VAEDecode':'VAE decoding', 'SaveImage':'Image saving'}
    for key, n in nodes.items():
        if n['type'] in steps:
            processing.append({'node':key, 'label':steps[n['type']], 'exposed':False})
    ordered, visited = [], set()
    def order(key):
        if key in visited: return
        visited.add(key)
        for ref in nodes[key]['refs'].values(): order(ref[0])
        ordered.append(key)
    for key in nodes: order(key)
    processing.sort(key=lambda p: ordered.index(p['node']))
    output_details = [{'node':n['id'], 'kind':OUTPUTS[n['type']],
                       'filename_prefix':n['values'].get('filename_prefix'),
                       'label':('Image saved through SaveImage'+(' (prefix: '+str(n['values']['filename_prefix'])+')' if 'filename_prefix' in n['values'] else ''))
                               if n['type']=='SaveImage' else OUTPUTS[n['type']]}
                      for n in nodes.values() if n['type'] in OUTPUTS]
    from .runtime import graph_nodes
    executable = sorted({n['type'] for n in graph_nodes(graph)})
    models = [{'node':n['id'],'field':field,'filename':n['values'][field],'category':category,
               'expected_sha256':next((r['sha256'] for r in n['raw'].get('properties', {}).get('models', [])
                                      if r.get('name')==n['values'][field] and re.fullmatch('[0-9a-fA-F]{64}', str(r.get('sha256', '')))), None)}
              for n in nodes.values() for field, category, _ in MODEL_LOADERS.get(n['type'], [])
              if isinstance(n['values'].get(field), str) and field not in n['refs']]
    conditioning = [{'node':n['id'],'label':'Negative conditioning is zeroed; no separate negative-prompt field.', 'role':'negative'}
                    for n in samplers if n['refs'].get('negative') and nodes[n['refs']['negative'][0]]['type']=='ConditioningZeroOut']
    return {'analysis_version':VERSION, 'workflow_sha256':graph_digest(graph), 'provenance':'graph analysis',
            'purpose':purpose, 'actions':[a for a in ACTIONS if a in actions],
            'inputs':inputs, 'input_counts':counts, 'prompts':prompts, 'controls':controls, 'processing':processing,
            'outputs':outputs, 'output_details':output_details, 'nodes':executable, 'models':models, 'conditioning':conditioning,
            'limitations':['Graph analysis does not establish execution, hardware suitability or performance.'],
            'uncertainties':sorted(issues), 'evidence':evidence}


def resolve_summary(graph, previous=None, node_schema=None):
    result = analyze(graph, node_schema=node_schema)
    if previous and (previous.get('workflow_sha256') != result['workflow_sha256'] or previous.get('analysis_version') != VERSION):
        result['uncertainties'].append('Previous capability summary was stale; recomputed from this workflow.')
    return result
