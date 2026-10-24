"""Permutation v0: immutable plans, conservative submission fences, local review."""
import copy
import csv
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import time
from datetime import datetime
from decimal import Decimal
import re

from renderrepo.installation_history import atomic_json, lease, timestamp, active

TERMINAL = {'completed', 'failed'}
FIELDS = {'prompt', 'negative', 'seed', 'cfg', 'sampler', 'scheduler', 'lora',
          'lora_strength', 'width', 'height'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def locked(path):
    try:
        return lease(path)
    except ValueError:
        raise ValueError('A permutation runner or state writer is already active.') from None


def finite(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{label} must be a finite {"positive" if positive else "nonnegative"} number')
    return value


def graph_valid(graph):
    if not isinstance(graph, dict) or not graph or 'nodes' in graph or not all(
            isinstance(n, dict) and isinstance(n.get('class_type'), str) and
            isinstance(n.get('inputs'), dict) for n in graph.values()):
        raise ValueError('Workflow must be an executable API graph, not an editor export.')


def binding_targets(graph, bindings):
    seen = set()
    for name, targets in bindings.items():
        if name not in FIELDS and not name.startswith(('reference:', 'lora:', 'lora_strength:')):
            raise ValueError(f'Unknown setting: {name}')
        if not isinstance(targets, list) or not targets:
            raise ValueError(f'{name}: bindings must be a nonempty list of [node_id, input] pairs')
        for target in targets:
            if not isinstance(target, list) or len(target) != 2 or not all(isinstance(s, str) for s in target):
                raise ValueError('Use explicit string node-ID/input pairs; titles are not supported.')
            node, field = target
            if node not in graph or field not in graph[node]['inputs']:
                raise ValueError(f'Binding not found: {node}.{field}')
            if isinstance(graph[node]['inputs'][field], (dict, list)):
                raise ValueError(f'{node}.{field}: cannot replace a graph connection with a scalar')
            if (node, field) in seen:
                raise ValueError('Bindings overlap.')
            seen.add((node, field))


def setting_valid(name, value):
    if name == 'seed':
        if type(value) is not int or not 0 <= value < 2**64:
            raise ValueError('Seeds must be explicit unsigned 64-bit integers.')
    elif name in {'width', 'height'}:
        if type(value) is not int or value <= 0:
            raise ValueError('Dimensions must be positive integers.')
    elif name == 'cfg' or name.startswith('lora_strength'):
        # Negative LoRA strengths are valid, CFG must be nonnegative.
        if type(value) not in (int, float) or not math.isfinite(value) or (name == 'cfg' and value < 0):
            raise ValueError(f'{name}: expected a finite number')
    elif not isinstance(value, str) or (name not in {'prompt', 'negative'} and not value):
        raise ValueError(f'{name}: expected a string')


def effective_settings(graph, bindings, requested):
    effective = copy.deepcopy(requested)
    for name, selection in requested.items():
        if (name == 'lora' or name.startswith('lora:')) and selection == 'off':
            strength = 'lora_strength' + name.removeprefix('lora')
            if strength not in bindings:
                raise ValueError(f'{name}=off requires explicit {strength} bindings for all effect strengths.')
            strength_targets = {tuple(t) for t in bindings[strength]}
            names = {graph[node]['inputs'][field] for node, field in bindings[name]}
            if len(names) != 1 or not all(isinstance(v, str) and v and v != 'off' for v in names):
                raise ValueError('LoRA off must retain a concrete original model filename.')
            for node, _ in bindings[name]:
                effects = {k for k in graph[node]['inputs'] if k.startswith('strength')}
                if not effects or any((node, k) not in strength_targets for k in effects):
                    raise ValueError('LoRA off requires every effect strength on the selected node to be bound; no unbound inputs are rewritten.')
            effective[name] = next(iter(names))
            effective[strength] = 0.0
    return effective


def plan(definition, store):
    """Snapshot inputs and expand locally. Never instantiate or call a provider."""
    definition = Path(definition).resolve()
    spec = read(definition)
    allowed = {'version', 'name', 'workflow', 'bindings', 'fixed', 'vary', 'seeds',
               'references', 'provider', 'estimate', 'limits', 'output_nodes'}
    if not isinstance(spec, dict) or set(spec) - allowed or spec.get('version') != 0:
        raise ValueError('Unknown experiment field or version (expected version 0).')
    required = {'workflow', 'bindings', 'seeds', 'provider', 'estimate', 'limits', 'output_nodes'}
    if required - set(spec):
        raise ValueError('Experiment is missing required fields: ' + ', '.join(sorted(required - set(spec))))
    original_bytes = (definition.parent / spec['workflow']).read_bytes()
    graph = json.loads(original_bytes)
    graph_valid(graph)
    bindings = spec['bindings']
    if not isinstance(bindings, dict) or 'seed' not in bindings:
        raise ValueError('Explicit seed binding required.')
    binding_targets(graph, bindings)
    fixed, vary = spec.get('fixed', {}), spec.get('vary', {})
    if not isinstance(fixed, dict) or not isinstance(vary, dict) or set(fixed) & set(vary):
        raise ValueError('Fixed and varied settings must be disjoint mappings.')
    if 'seed' in fixed or 'seed' in vary:
        raise ValueError('Use the shared seeds list, not a seed axis.')
    seeds = spec['seeds']
    if not isinstance(seeds, list) or not seeds or len(set(seeds)) != len(seeds):
        raise ValueError('Provide a nonempty, unique shared seeds list.')
    for seed in seeds:
        setting_valid('seed', seed)
    references, sources = {}, {}
    for slot, selection in spec.get('references', {}).items():
        key = 'reference:' + slot
        if key not in bindings or key in fixed or key in vary:
            raise ValueError('Reference slots require their own explicit binding.')
        if isinstance(selection, dict) and set(selection) == {'directory'}:
            root = (definition.parent / selection['directory']).resolve()
            paths = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'})
        elif isinstance(selection, list):
            paths = [(definition.parent / p).resolve() for p in selection]
        else:
            raise ValueError('Reference selection must be paths or {directory: path}.')
        references[key] = []
        for p in paths:
            if p.stat().st_size > 40 * 1024 * 1024:
                raise ValueError('Reference exceeds 40 MiB; rejected before reading its content.')
            with p.open('rb') as stream:
                data = stream.read(40 * 1024 * 1024 + 1)
            if len(data) > 40 * 1024 * 1024 or p.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('Reference must be a PNG/JPEG/WebP up to 40 MiB.')
            sha = hashlib.sha256(data).hexdigest()
            name = sha + p.suffix.lower()
            sources[name] = data
            references[key].append({'file': name, 'sha256': sha, 'source_name': p.name})
        if not references[key]:
            raise ValueError('Empty reference selection.')
    for name, value in fixed.items():
        if name not in bindings or name.startswith('reference:'):
            raise ValueError(f'Unbound setting: {name}')
        setting_valid(name, value)
    for name, values in vary.items():
        if name not in bindings or name.startswith('reference:') or not isinstance(values, list) or not values:
            raise ValueError(f'Invalid/unbound variation: {name}')
        if len({digest(v) for v in values}) != len(values):
            raise ValueError('Duplicate axis values.')
        for value in values:
            setting_valid(name, value)
    if set(bindings) != set(fixed) | set(vary) | set(references) | {'seed'}:
        raise ValueError('Every binding needs a fixed value, variation, reference selection, or shared seeds.')
    provider = spec['provider']
    if not isinstance(provider, dict) or not {'kind', 'adapter_repo', 'base_url', 'account_profile'} <= set(provider) or set(provider) - {'kind', 'adapter_repo', 'base_url', 'account_profile', 'download_hosts'} or provider['kind'] != 'comfy-v2' or provider['account_profile'] != 'cbi':
        raise ValueError('v0 supports the existing comfy-v2 CBI adapter only.')
    # Validate the qualified origin without importing provider dependencies or resolving a key.
    from urllib.parse import urlsplit
    origin = urlsplit(provider['base_url'])
    if origin.scheme != 'https' or origin.netloc != 'cloud.comfy.org' or origin.path not in {'', '/'} or origin.query or origin.fragment:
        raise ValueError('v0 smoke integration is restricted to https://cloud.comfy.org.')
    from .provider import DEFAULT_DOWNLOAD_HOSTS
    hosts = provider.get('download_hosts', list(DEFAULT_DOWNLOAD_HOSTS))
    if not isinstance(hosts, list) or any(not isinstance(h, str) or not h or h != h.lower() or any(c in h for c in '/:@* ') for h in hosts):
        raise ValueError('Download hosts must be an explicit list of exact lowercase hostnames.')
    provider = {**provider, 'download_hosts': hosts,
                'adapter_repo': str((definition.parent / provider['adapter_repo']).resolve())}
    estimate = spec['estimate']
    if set(estimate) != {'per_render', 'unit', 'basis'} or not all(isinstance(estimate[k], str) and estimate[k] for k in ('unit', 'basis')):
        raise ValueError('Estimate needs a native unit and an explicitly hypothetical basis.')
    finite(estimate['per_render'], 'Estimated charge')
    limits = spec['limits']
    if set(limits) != {'max_renders', 'max_estimated_spend', 'max_elapsed_seconds', 'failure_threshold'}:
        raise ValueError('All four limits are required.')
    for field in ('max_renders', 'failure_threshold'):
        if type(limits[field]) is not int or limits[field] < 1:
            raise ValueError(f'{field} must be a positive integer')
    finite(limits['max_estimated_spend'], 'Spend limit')
    finite(limits['max_elapsed_seconds'], 'Elapsed limit', True)
    axes = {**vary, **references}
    keys = sorted(axes)
    count = math.prod(len(axes[k]) for k in keys) * len(seeds)
    estimated_total = Decimal(str(estimate['per_render'])) * count
    if count > min(limits['max_renders'], 10000) or estimated_total > Decimal(str(limits['max_estimated_spend'])):
        raise ValueError('Render count or hypothetical estimated spend exceeds the limit.')
    output_nodes = spec['output_nodes']
    if not isinstance(output_nodes, list) or not output_nodes or len(set(output_nodes)) != len(output_nodes) or any(n not in graph for n in output_nodes):
        raise ValueError('Explicit output node IDs required.')
    jobs = []
    for combination in itertools.product(*(axes[k] for k in keys)):
        settings = {**fixed, **dict(zip(keys, combination))}
        configuration = digest(settings)[:24]
        for seed in seeds:
            values = {**settings, 'seed': seed}
            effective = effective_settings(graph, bindings, values)
            compiled = copy.deepcopy(graph)
            for name, targets in bindings.items():
                for node, field in targets:
                    original = graph[node]['inputs'][field]
                    replacement = effective[name]
                    if not name.startswith('reference:') and original is not None:
                        numeric = lambda v: type(v) in (int, float)
                        if not ((numeric(original) and numeric(replacement)) or (isinstance(original, str) and isinstance(replacement, str))):
                            raise ValueError(f'Binding scalar type mismatch: {node}.{field}')
                    compiled[node]['inputs'][field] = copy.deepcopy(replacement)
            jobs.append({'id': digest({'settings': values, 'workflow': compiled}),
                         'configuration': configuration, 'settings': values, 'effective_settings': effective, 'workflow': compiled})
    if len({j['id'] for j in jobs}) != len(jobs):
        raise ValueError('Duplicate matrix jobs (check repeated reference contents/slots).')
    value = {'version': 0, 'name': spec.get('name', definition.stem), 'original_workflow': graph,
             'original_workflow_hash': digest(graph), 'original_file_sha256': hashlib.sha256(original_bytes).hexdigest(), 'bindings': bindings, 'provider': provider,
             'estimate': estimate, 'limits': limits, 'output_nodes': output_nodes,
             'jobs': jobs, 'count': count, 'estimated_total': float(estimated_total)}
    plan_hash = digest(value)
    directory = Path(store).resolve() / plan_hash
    directory.mkdir(parents=True, exist_ok=True)
    with locked(directory / 'state.lock'):
        if not (directory / 'plan.json').exists():
            atomic_bytes(directory / 'original-workflow.json', original_bytes)
            for name, data in sources.items():
                atomic_bytes(directory / 'references' / name, data)
            atomic_json(directory / 'plan.json', value)
            atomic_json(directory / 'control.json', {'action': 'pause'})
        else:
            load(directory)  # Existing identical plan is reused, never reset.
    return directory, value


def atomic_bytes(path, content):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            name = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if name:
            name.unlink(missing_ok=True)


def load(directory):
    directory = Path(directory).resolve()
    value = read(directory / 'plan.json')
    if digest(value) != directory.name:
        raise ValueError('Plan integrity error: changed plans require planning and approval again.')
    if hashlib.sha256((directory / 'original-workflow.json').read_bytes()).hexdigest() != value['original_file_sha256']:
        raise ValueError('Original workflow file hash mismatch.')
    verified_references = set()
    for job in value['jobs']:
        for name, ref in job['settings'].items():
            if name.startswith('reference:'):
                identity = (ref['file'], ref['sha256'])
                if identity in verified_references:
                    continue
                p = directory / 'references' / ref['file']
                if hashlib.sha256(p.read_bytes()).hexdigest() != ref['sha256']:
                    raise ValueError('Retained reference hash mismatch.')
                verified_references.add(identity)
    return value


def approve(directory, plan_hash):
    directory = Path(directory).resolve()
    with locked(directory / 'state.lock'):
        load(directory)
        if directory.name != plan_hash:
            raise ValueError('Approval must quote the exact displayed plan hash.')
        atomic_json(directory / 'approval.json', {'plan_hash': plan_hash, 'at': timestamp()})


def control(directory, action):
    if action not in {'pause', 'stop', 'run'}:
        raise ValueError('Invalid control')
    directory = Path(directory)
    with locked(directory / 'control.lock'):
        atomic_json(directory / 'control.json', {'action': action, 'at': timestamp()})


def records(directory, value=None):
    directory = Path(directory)
    value = value or load(directory)
    result = []
    for item in value['jobs']:
        path = directory / 'jobs' / item['id'] / 'state.json'
        try:
            if not path.exists() and path.parent.exists() and any(path.parent.iterdir()):
                raise ValueError('Missing durable job state; prior submission cannot be ruled out.')
            state = read(path) if path.exists() else {'state': 'planned'}
            allowed_states = {'planned', 'submitting', 'ambiguous', 'accepted', 'preparation_failed', 'reconciliation_required'} | TERMINAL
            if not isinstance(state, dict) or state.get('state') not in allowed_states or (path.exists() and not {'id', 'settings', 'workflow', 'state'} <= set(state)):
                raise ValueError('Invalid or incomplete durable job state.')
            if not isinstance(state, dict) or state.get('id', item['id']) != item['id'] or state.get('settings', item['settings']) != item['settings'] or state.get('workflow', item['workflow']) != item['workflow']:
                raise ValueError('Job identity/settings do not match the approved plan.')
            if path.exists():
                phase = state['state']
                if phase in {'accepted', 'reconciliation_required', 'completed'} and not state.get('provider_job_id'):
                    raise ValueError('Accepted/completed job is missing its provider job ID; never resubmit.')
                if phase in {'planned', 'preparation_failed'} and any(state.get(k) for k in ('provider_job_id', 'submitted_workflow_hash', 'started_at')):
                    raise ValueError('Pre-submission state conflicts with submission evidence.')
                if phase in {'submitting', 'ambiguous', 'accepted', 'reconciliation_required'} | TERMINAL:
                    if not state.get('submitted_workflow_hash') or not state.get('started_at'):
                        raise ValueError('Submission provenance is incomplete.')
                if phase in TERMINAL:
                    datetime.fromisoformat(state['started_at'])
                    datetime.fromisoformat(state['ended_at'])
                    finite(state['elapsed_seconds'], 'Execution seconds')
                if phase == 'completed':
                    if not state.get('outputs'):
                        raise ValueError('Completed job has no output evidence.')
                    verified_outputs(path.parent, state['outputs'])
            if state.get('submitted_workflow_hash'):
                submitted = read(path.parent / 'submitted-workflow.json')
                if digest(submitted) != state['submitted_workflow_hash']:
                    raise ValueError('Submitted workflow hash mismatch.')
            result.append({**item, **state})
        except (ValueError, OSError, KeyError, TypeError) as e:
            result.append({**item, 'state': 'corrupt', 'error': str(e)})
    return result


def verified_outputs(directory, outputs):
    if not isinstance(outputs, list) or not outputs:
        raise ValueError('Invalid output manifest.')
    for output in outputs:
        name = output['file']
        if not isinstance(name, str) or not re.fullmatch(r'output-[0-9]+\.(png|jpg|webp)', name):
            raise ValueError('Invalid output filename.')
        path = directory / name
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != output['sha256']:
            raise ValueError('Saved output is missing or fails its hash check.')
    return outputs


def recovered_delivery(path, item):
    manifest = path / 'delivery.json'
    if not manifest.exists():
        return None
    receipt = read(manifest)
    if receipt.get('provider_job_id') != item['provider_job_id'] or receipt.get('submitted_workflow_hash') != item['submitted_workflow_hash']:
        raise ValueError('Saved delivery belongs to a different provider job/workflow.')
    outputs = receipt['outputs']
    try:
        return verified_outputs(path, outputs)
    except (ValueError, OSError):
        return None  # Known provider ID is still reconciled; never rerender.


def sibling_outstanding(directory):
    warnings = []
    for other in directory.parent.iterdir():
        if other == directory or not other.is_dir() or not re.fullmatch('[0-9a-f]{64}', other.name):
            continue
        try:
            outstanding = records(other)
        except (ValueError, OSError, KeyError, TypeError) as error:
            # An empty damaged plan has no submission evidence. Isolate it.
            files = list((other / 'jobs').glob('*/state.json'))
            try:
                terminal_only = all(read(f).get('state') in TERMINAL for f in files)
                orphaned = (other / 'jobs').exists() and any(p.is_dir() and not (p / 'state.json').exists() for p in (other / 'jobs').iterdir())
            except (ValueError, OSError, AttributeError):
                terminal_only, orphaned = False, True
            if not terminal_only or orphaned:
                raise ValueError(f'Unresolved/corrupt submission evidence in experiment {other.name}; reconcile it first.') from error
            warnings.append({'experiment': other.name, 'error': 'Corrupt plan isolated; no unfinished submission evidence.'})
            continue
        if any(r['state'] in {'corrupt', 'submitting', 'ambiguous', 'accepted', 'reconciliation_required'} for r in outstanding):
            raise ValueError(f'Reconcile outstanding work in experiment {other.name} first.')
    return warnings


def run(directory, provider, *, sleep=time.sleep, poll_seconds=2, resume=False):
    """Blocking CLI worker; browser lifecycle does not own this worker."""
    directory = Path(directory).resolve()
    # Store-wide fence: two experiments cannot execute at once through this store.
    with locked(directory.parent / 'runner.lock'):
        value = load(directory)
        approval = read(directory / 'approval.json')
        if approval.get('plan_hash') != directory.name:
            raise ValueError('Exact plan approval required.')
        if resume:
            control(directory, 'run')  # Only the worker that acquired the lease may clear Stop/Pause.
        current = records(directory, value)
        if any(r['state'] == 'corrupt' for r in current):
            raise ValueError('Corrupt job state; repair it before execution. Other records remain readable.')
        if any(r['state'] in {'ambiguous', 'submitting'} and not r.get('provider_job_id') for r in current):
            for r in current:
                if r['state'] == 'submitting' and not r.get('provider_job_id'):
                    r.update(state='ambiguous', error='Submission acknowledgement unknown; do not resubmit.')
                    atomic_json(directory / 'jobs' / r['id'] / 'state.json', r)
            raise ValueError('Unresolved submission: reconcile with the provider/operator before continuing.')
        warnings = sibling_outstanding(directory)
        if all(r['state'] in TERMINAL for r in current):
            return summary(directory)  # No auth or live status calls for a finished experiment.
        atomic_json(directory.parent / 'runner.json', {'experiment': directory.name})
        provider.check(value['provider'])  # Identity check before any upload/submission.
        runtime_path = directory / 'runtime.json'
        runtime = read(runtime_path) if runtime_path.exists() else {'started_at': timestamp()}
        runtime['halt_reason'] = None
        runtime['isolated_experiments'] = warnings
        atomic_json(runtime_path, runtime)
        monotonic_start = time.monotonic()
        initial_elapsed = max(finite(runtime.get('elapsed_floor', 0), 'Durable elapsed budget'), (datetime.fromisoformat(timestamp()) - datetime.fromisoformat(runtime['started_at'])).total_seconds())
        def elapsed_time():
            wall = (datetime.fromisoformat(timestamp()) - datetime.fromisoformat(runtime['started_at'])).total_seconds()
            elapsed = max(wall, initial_elapsed + time.monotonic() - monotonic_start)
            runtime['elapsed_floor'] = elapsed
            atomic_json(runtime_path, runtime)
            return elapsed
        failures = 0
        for prior in current:
            if prior['state'] == 'failed':
                failures += 1
            elif prior['state'] == 'completed':
                failures = 0
        # Reconcile accepted jobs first, even if stopped, paused, or over budget.
        ordered = sorted(current, key=lambda r: 0 if r.get('provider_job_id') and r['state'] not in TERMINAL else 1)
        for item in ordered:
            if item['state'] in TERMINAL:
                continue
            path = directory / 'jobs' / item['id']
            state_path = path / 'state.json'
            pending = bool(item.get('provider_job_id'))
            delivery = recovered_delivery(path, item) if pending else None
            if delivery:
                item.update(state='completed', outputs=delivery)
                item.pop('error', None)
                finish(item)
                atomic_json(state_path, item)
                failures = 0
                continue
            elapsed = elapsed_time()
            if not pending and (read(directory / 'control.json')['action'] != 'run' or
                                failures >= value['limits']['failure_threshold'] or elapsed >= value['limits']['max_elapsed_seconds']):
                runtime['halt_reason'] = ('failure_threshold' if failures >= value['limits']['failure_threshold'] else
                                          'elapsed_limit' if elapsed >= value['limits']['max_elapsed_seconds'] else
                                          read(directory / 'control.json')['action'])
                atomic_json(runtime_path, runtime)
                break
            if not pending:
                if 'effective_settings' not in item and any((name == 'lora' or name.startswith('lora:')) and selection == 'off' for name, selection in item['settings'].items()):
                    raise ValueError('Legacy LoRA off plan needs new planning and approval before submission; persisted accepted jobs remain reconciliable.')
                # Asset preparation is non-rendering. Exact wire graph is durable before the billable POST.
                try:
                    submitted = provider.prepare(copy.deepcopy(item['workflow']), directory, value['bindings'], item['settings'])
                    graph_valid(submitted)
                    expected = copy.deepcopy(item['workflow'])
                    for name, targets in value['bindings'].items():
                        if name.startswith('reference:'):
                            for node, field in targets:
                                expected[node]['inputs'][field] = copy.deepcopy(submitted[node]['inputs'][field])
                    if digest(expected) != digest(submitted):
                        raise ValueError('Provider preparation changed instructions or workflow data beyond bound reference inputs.')
                except Exception as e:
                    item.update(state='preparation_failed', error=type(e).__name__)
                    if hasattr(provider, 'target_evidence'):
                        item['target_requirements'] = provider.target_evidence
                    atomic_json(state_path, item)
                    if item.get('target_requirements', {}).get('state') == 'missing':
                        raise ValueError(item['target_requirements']['action']) from e
                    raise ValueError('Preparation failed; no render submitted. Resume to retry preparation.') from e
                # A pause/stop arriving during upload must prevent the next POST.
                if read(directory / 'control.json')['action'] != 'run':
                    runtime['halt_reason'] = read(directory / 'control.json')['action']
                    atomic_json(runtime_path, runtime)
                    break
                elapsed = elapsed_time()
                if elapsed >= value['limits']['max_elapsed_seconds']:
                    runtime['halt_reason'] = 'elapsed_limit'
                    atomic_json(runtime_path, runtime)
                    break
                atomic_json(path / 'submitted-workflow.json', submitted)
                if hasattr(provider, 'target_evidence'):
                    item['target_requirements'] = provider.target_evidence
                item.update(state='submitting', submitted_workflow_hash=digest(submitted),
                            started_at=timestamp(), estimated_charge=value['estimate'], actual_charge=None,
                            submission_token=digest({'experiment': directory.name, 'job': item['id']}),
                            provider_job_id=None, outputs=[], simulated=getattr(provider, 'simulated', False) is True)
                atomic_json(state_path, item)
                try:
                    provider_id = provider.submit(submitted, item['submission_token'])
                    if not isinstance(provider_id, str) or not provider_id:
                        raise ValueError('Missing provider job ID')
                    item.update(state='accepted', provider_job_id=provider_id)
                    atomic_json(state_path, item)  # Persist acknowledgement immediately.
                except Exception as e:
                    # A returned ID survives a one-off acknowledgement-save failure.
                    # If all subsequent writes fail, disk still fences an unknown POST.
                    if item.get('provider_job_id'):
                        item.update(state='reconciliation_required', error='ACKNOWLEDGEMENT_SAVE_FAILED')
                        atomic_json(state_path, item)
                        raise ValueError('Known provider job requires reconciliation after its acknowledgement save failed; no rerender.') from e
                    # Only an explicit adapter rejection proves no job was accepted.
                    item.update(state='failed' if getattr(e, 'definitely_rejected', False) else 'ambiguous',
                                error=type(e).__name__)
                    if item['state'] == 'failed':
                        finish(item)
                    atomic_json(state_path, item)
                    if item['state'] == 'ambiguous':
                        raise ValueError('Submission uncertain; execution halted without retry.') from e
                    failures += 1
                    continue
            try:
                while True:
                    status = provider.status(item['provider_job_id'])
                    charge = status.get('actual_charge')
                    if charge is None:
                        charge = item.get('actual_charge')
                    if charge is not None:
                        if not isinstance(charge, dict) or set(charge) != {'value', 'unit', 'basis'} or charge['basis'] != 'provider_accounting' or not isinstance(charge['unit'], str) or not charge['unit']:
                            raise ValueError('Actual charge must have provider-accounting evidence and native unit.')
                        finite(charge['value'], 'Actual charge')
                    provider_seconds = status.get('provider_seconds')
                    if provider_seconds is None:
                        provider_seconds = item.get('provider_seconds')
                    if provider_seconds is not None:
                        finite(provider_seconds, 'Provider seconds')
                    item.update(provider_state=status['state'], provider_seconds=provider_seconds,
                                actual_charge=charge)
                    atomic_json(state_path, item)
                    if status['state'] in {'succeeded', 'failed', 'canceled', 'expired'}:
                        break
                    if status['state'] not in {'queued', 'running', 'canceling'}:
                        raise ValueError('Unknown provider state')
                    elapsed = elapsed_time()
                    if elapsed >= value['limits']['max_elapsed_seconds']:
                        raise TimeoutError('Elapsed limit; accepted job remains reconcilable.')
                    sleep(poll_seconds)
                if status['state'] == 'succeeded':
                    outputs = provider.outputs(status, value['output_nodes'])
                    if not outputs:
                        raise ValueError('Provider returned no selected outputs')
                    saved = []
                    for index, (content, extension) in enumerate(outputs):
                        if not isinstance(content, bytes) or not 0 < len(content) <= 40 * 1024 * 1024 or extension not in {'.png', '.jpg', '.webp'}:
                            raise ValueError('Output must be bounded image bytes.')
                        saved.append({'file': f'output-{index}{extension}', 'sha256': hashlib.sha256(content).hexdigest()})
                    atomic_json(path / 'delivery.json', {'outputs': saved, 'provider_job_id': item['provider_job_id'], 'submitted_workflow_hash': item['submitted_workflow_hash']})
                    for index, output in enumerate(outputs):
                        content, extension = output
                        if not isinstance(content, bytes) or not 0 < len(content) <= 40 * 1024 * 1024:
                            raise ValueError('Output must be bounded nonempty bytes.')
                        if extension not in {'.png', '.jpg', '.webp'}:
                            raise ValueError('Unsupported output type')
                        name = f'output-{index}{extension}'
                        atomic_bytes(path / name, content)

                    item.update(state='completed', outputs=saved)
                    item.pop('error', None)
                    failures = 0
                else:
                    item.update(state='failed', error=status.get('error', status['state']))
                    failures += 1
                finish(item)
                atomic_json(state_path, item)
            except Exception as e:
                # Never mark an accepted job as rerenderable on poll/save errors.
                item.update(state='reconciliation_required', error=type(e).__name__)
                atomic_json(state_path, item)
                raise ValueError('Accepted job requires status/output reconciliation; resume does not rerender.') from e
        if all(r['state'] in TERMINAL for r in records(directory, value)):
            runtime['halt_reason'] = 'finished'
            atomic_json(runtime_path, runtime)
        return summary(directory)


def finish(item):
    item['ended_at'] = timestamp()
    item['elapsed_seconds'] = max(0, (datetime.fromisoformat(item['ended_at']) - datetime.fromisoformat(item['started_at'])).total_seconds())


def attach(directory, job_id, provider_id):
    """Operator may associate an independently confirmed UUID, never infer non-acceptance."""
    from uuid import UUID
    if str(UUID(provider_id)) != provider_id:
        raise ValueError('Canonical provider UUID required.')
    directory = Path(directory).resolve()
    with locked(directory.parent / 'runner.lock'):
        value = load(directory)
        if job_id not in {j['id'] for j in value['jobs']}:
            raise ValueError('Unknown job')
        path = directory / 'jobs' / job_id / 'state.json'
        item = read(path)
        if item['state'] not in {'submitting', 'ambiguous'} or item.get('provider_job_id'):
            raise ValueError('Only an unacknowledged submission can be associated.')
        item.update(state='accepted', provider_job_id=provider_id, association='operator_confirmed', association_at=timestamp())
        atomic_json(path, item)


def review(directory, job_id, rating, notes=''):
    if rating not in {'keeper', 'modify', 'toss', 'unreviewed'} or not isinstance(notes, str) or len(notes) > 1000:
        raise ValueError('Review must be keeper/modify/toss/unreviewed; notes limited to 1000 characters.')
    directory = Path(directory).resolve()
    value = load(directory)
    item = next((r for r in records(directory, value) if r['id'] == job_id), None)
    if not item or item['state'] != 'completed':
        raise ValueError('Only completed results can be reviewed.')
    with locked(directory / 'reviews.lock'):
        path = directory / 'reviews.json'
        reviews = read(path) if path.exists() else {}
        reviews[job_id] = {'rating': rating, 'notes': notes, 'at': timestamp()}
        atomic_json(path, reviews)


def results(directory):
    directory = Path(directory)
    reviews = read(directory / 'reviews.json') if (directory / 'reviews.json').exists() else {}
    rows = []
    for r in records(directory):
        if r.get('submitted_workflow_hash') and r['state'] != 'corrupt':
            r['submitted_workflow'] = read(directory / 'jobs' / r['id'] / 'submitted-workflow.json')
        rows.append({**r, 'review': reviews.get(r['id'], {'rating': 'unreviewed', 'notes': ''})})
    return rows


def summary(directory):
    return summarize(results(directory))


def summarize(rows):
    completed = [r for r in rows if r['state'] == 'completed']
    reviewed = [r for r in completed if r['review']['rating'] != 'unreviewed']
    keepers = [r for r in reviewed if r['review']['rating'] == 'keeper']
    modify = sum(r['review']['rating'] == 'modify' for r in reviewed)
    attempted = [r for r in rows if r.get('started_at')]
    failed = [r for r in attempted if r['state'] == 'failed']
    terminal = [r for r in attempted if r['state'] in TERMINAL]
    known = [r for r in attempted if r.get('actual_charge') is not None]
    units = {r['actual_charge']['unit'] for r in known}
    # Cost per keeper includes all execution attempts, not only keeper jobs.
    costs = {u: {'known_total': sum(r['actual_charge']['value'] for r in known if r['actual_charge']['unit'] == u),
                 'known_samples': sum(r['actual_charge']['unit'] == u for r in known),
                 'complete': len(known) == len(attempted) and len(units) == 1 and len(terminal) == len(attempted) and not any(r['state'] == 'corrupt' for r in rows)} for u in sorted(units)}
    for cost in costs.values():
        cost['known_cost_per_keeper'] = cost['known_total'] / len(keepers) if keepers else None
    times = [r['elapsed_seconds'] for r in terminal]
    return {'corrupt': sum(r['state'] == 'corrupt' for r in rows), 'unresolved': sum(r['state'] in {'ambiguous', 'submitting', 'accepted', 'reconciliation_required'} for r in rows), 'planned': len(rows), 'attempted': len(attempted), 'terminal': len(terminal),
            'completed': len(completed), 'execution_failures': len(failed),
            'execution_failure_rate': len(failed) / len(terminal) if terminal else None,
            'failure_rate_denominator': 'terminal executions (completed + failed)',
            'reviewed': len(reviewed), 'unreviewed': len(completed) - len(reviewed),
            'review_coverage': len(reviewed) / len(completed) if completed else None,
            'keepers': len(keepers), 'modify': modify,
            'keeper_rate': len(keepers) / len(reviewed) if reviewed else None,
            'keeper_modify_rate': (len(keepers) + modify) / len(reviewed) if reviewed else None,
            'rating_denominator': 'reviewed completed results only',
            'seconds_per_keeper': sum(times) / len(keepers) if keepers else None,
            'timing_denominator': 'sum of terminal execution wall seconds / reviewed keepers; unresolved attempts excluded',
            'cost_denominator': 'readable attempted executions; corrupt records excluded and prevent complete totals', 'known_costs': costs, 'cost_unknown_samples': len(attempted) - len(known)}


def export(directory, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    rows = results(directory)
    atomic_json(destination / 'results.json', {'plan': load(directory), 'results': rows, 'summary': summarize(rows)})
    import io
    buffer = io.StringIO(newline='')
    columns = ['id', 'configuration', 'simulated', 'state', 'settings', 'submitted_workflow_hash', 'submission_token', 'provider_job_id',
               'started_at', 'ended_at', 'elapsed_seconds', 'estimated_charge', 'actual_charge', 'outputs', 'submitted_workflow', 'effective_settings', 'provider_seconds', 'error', 'rating', 'notes', 'notes_json']
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        values = {k: row.get(k) for k in columns}
        values.update(rating=row['review']['rating'], notes=row['review']['notes'], notes_json=json.dumps(row['review']['notes']))
        for k in ('settings', 'estimated_charge', 'actual_charge', 'outputs', 'submitted_workflow', 'effective_settings'):
            values[k] = json.dumps(values[k], sort_keys=True)
        # Prevent spreadsheet formula execution in optional user notes/text.
        for k, v in values.items():
            if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')):
                values[k] = "'" + v
        writer.writerow(values)
    atomic_bytes(destination / 'results.csv', buffer.getvalue().encode())


def execution_state(directory):
    directory = Path(directory).resolve()
    control_value = read(directory / 'control.json')['action']
    owner_path = directory.parent / 'runner.json'
    is_active = active(directory.parent / 'runner.lock') and owner_path.exists() and read(owner_path).get('experiment') == directory.name
    if is_active:
        return {'pause': 'pause_requested', 'stop': 'stop_requested', 'run': 'running'}[control_value]
    runtime = read(directory / 'runtime.json') if (directory / 'runtime.json').exists() else {}
    reason = runtime.get('halt_reason')
    if reason:
        return {'pause': 'paused', 'stop': 'stopped', 'finished': 'finished'}.get(reason, reason)
    return 'interrupted' if runtime else 'planned'
