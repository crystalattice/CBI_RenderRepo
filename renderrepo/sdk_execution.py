"""One approved installed-starter execution through the official Comfy SDK.

Uses installation-local atomic records/leases and the existing starter bindings.
This is an execution path, not a benchmark or a Cloud provisioning adapter.
"""
import argparse
import copy
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import time
import uuid

from . import local_test as profiles
from .installation_history import atomic_json, lease, timestamp


def digest(value):
    return hashlib.sha256(profiles.encoded(value)).hexdigest()


def folder(root, token):
    if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid SDK execution identity.')
    path = Path(root).resolve() / '.renderrepo' / 'sdk-runs' / token
    if any(p.is_symlink() for p in (path.parent.parent, path.parent, path)):
        raise ValueError('SDK records must be installation-local directories.')
    return path


def proxy_check(native_url, sdk_url):
    proxy = profiles.Native(sdk_url)  # Local-only HTTP, no credentials or redirects.
    health = proxy.request('/api/v2/health')
    if health.get('status') != 'healthy' or health.get('upstream', '').rstrip('/') != native_url.rstrip('/'):
        raise ValueError('SDK proxy upstream does not match the explicitly selected native Comfy server.')
    return health


def prepare(root, name, native_url, sdk_url, deadline=60):
    if type(deadline) is not int or not 1 <= deadline <= 60:
        raise ValueError('Choose a deadline from 1 to 60 seconds.')
    current = profiles.context(root, name, native_url)
    health = proxy_check(native_url, sdk_url)
    token = uuid.uuid4().hex
    path = folder(root, token)
    path.mkdir(parents=True)
    graph = profiles.prompt_for(current['workflow'])
    output_node = next(k for k, n in graph.items() if n['class_type'] == 'SaveImage')
    prefix = f'rr-sdk/{token}/result'
    original_prefix = graph[output_node]['inputs']['filename_prefix']
    graph[output_node]['inputs']['filename_prefix'] = prefix
    for item in current['inputs']:
        content = (Path(current['input_dir']) / item['filename']).read_bytes()
        (path / item['filename']).write_bytes(content)
    plan = {'id': token, 'created_at': timestamp(), 'context': current,
            'sdk_url': sdk_url.rstrip('/'), 'proxy': health, 'sdk': {'library': 'comfy-sdk', 'version': version('comfy-sdk')},
            'workflow': graph, 'output_node': output_node, 'output_prefix': prefix,
            'overrides': [{'node': output_node, 'field': 'filename_prefix', 'original': original_prefix, 'submitted': prefix},
                          *[{'node': i['node'], 'field': 'image', 'original': i['filename'],
                             'submitted': 'SDK asset handle for exactly the recorded input bytes'} for i in current['inputs']]],
            'runs': 1, 'deadline_seconds': deadline, 'idempotency_key': 'renderrepo-' + token,
            'output_directory': str(path / 'outputs'),
            'warning': 'Explicit local SDK execution consumes CPU/GPU/RAM and writes files. No API/provider nodes or models. Deadline stops waiting, not proof of stopping; SDK queue retries/network time can delay observation. No performance claim.'}
    approval = digest(plan)
    atomic_json(path / 'plan.json', plan)
    atomic_json(path / 'state.json', {'id': token, 'state': 'planned', 'approval_sha256': approval, 'outputs': []})
    atomic_json(path / 'control.json', {'stop': False})
    return {'plan': plan, 'approval_sha256': approval}


def load(root, token):
    path = folder(root, token)
    plan = profiles.read(path / 'plan.json')
    state = profiles.read(path / 'state.json')
    if plan['id'] != token or state['id'] != token or digest(plan) != state['approval_sha256']:
        raise ValueError('SDK plan changed; prepare a new attempt.')
    return path, plan, state


def client_for(plan):
    # Refuse even the presence of a provider credential, without reading its value.
    # This CLI is intentionally local-only; Cloud plans are prepared documentation.
    if 'COMFY_API_KEY' in os.environ:
        raise ValueError('Local SDK execution requires COMFY_API_KEY unset; no credential is needed.')
    from comfy_sdk import Comfy
    previous = os.environ.get('COMFY_BASE_URL')
    os.environ['COMFY_BASE_URL'] = plan['sdk_url']
    try:
        client = Comfy(timeout=5, client_info='renderrepo')
    finally:
        if previous is None:
            os.environ.pop('COMFY_BASE_URL', None)
        else:
            os.environ['COMFY_BASE_URL'] = previous
    return client


def collect(path, plan, state, client):
    job = client.jobs.get(state['job_id'])
    if job.id != state['job_id']:
        raise ValueError('SDK returned a different job identity.')
    state.update(provider_state=job.status, observed_at=timestamp(), execution_may_continue=job.status not in {'succeeded', 'failed', 'canceled', 'expired'})
    if state['execution_may_continue']:
        if time.time() >= state['deadline_epoch']:
            state.update(state='timed_out', deadline_exceeded=True, note='Waiting deadline reached; operation may continue. Reconcile the known job, never resubmit this attempt.')
        atomic_json(path / 'state.json', state)
        return state
    if job.status != 'succeeded':
        state.update(state='cancelled' if job.status == 'canceled' else 'failed', failure={'status': job.status, 'detail': 'See the named Comfy job for node-level error details.'})
    else:
        outputs = job.get_outputs(plan['output_node'])
        if len(outputs) != 1 or outputs[0].type != 'image' or outputs[0].content_type != 'image/png' or outputs[0].size_bytes > 10 * 1024 * 1024:
            raise ValueError('Expected one small batch-1 PNG from the approved output node.')
        content = outputs[0].to_bytes()
        if len(content) > 10 * 1024 * 1024:
            raise ValueError('Oversized SDK output.')
        from PIL import Image, ImageChops
        with Image.open(path / plan['context']['inputs'][0]['filename']) as opened:
            base = opened.convert('RGB')
        name = plan['context']['package']['name']
        if name.endswith('resize'):
            expected = Image.new('RGB', (160, 100))
            expected.putdata([base.getpixel((2*x+1, 2*y+1)) for y in range(100) for x in range(160)])
        elif name.endswith('portrait-crop'):
            expected = base.crop((110, 0, 210, 200))
        else:
            expected = base.copy()
            with Image.open(path / plan['context']['inputs'][1]['filename']) as overlay:
                expected.paste(overlay.convert('RGB'), (224, 120))
        with Image.open(io.BytesIO(content)) as opened:
            actual = opened.convert('RGB')
        if actual.size != expected.size or ImageChops.difference(actual, expected).getbbox() is not None:
            raise ValueError('SDK output pixels differ from the approved starter operation.')
        target = path / 'outputs' / 'result.png'
        if target.parent.is_symlink() or target.is_symlink():
            raise ValueError('SDK output storage must not be symlinked; existing files were kept.')
        target.parent.mkdir(exist_ok=True)
        if target.exists() and target.read_bytes() != content:
            raise ValueError('Retained output changed; existing bytes were kept.')
        if not target.exists():
            if target.with_suffix('.partial').is_symlink():
                raise ValueError('SDK output temporary file is symlinked; existing files were kept.')
            temporary = target.with_suffix('.partial')
            with temporary.open('wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        state.update(state='completed', outputs=[{'path': str(target), 'sha256': hashlib.sha256(content).hexdigest(),
                     'dimensions': list(actual.size), 'verification': 'Every decoded RGB pixel matches the advertised operation.'}])
    atomic_json(path / 'state.json', state)
    return state


def start(root, token, approval, client_factory=client_for):
    path, plan, state = load(root, token)
    with lease(path / 'worker.lock'):
        path, plan, state = load(root, token)
        if state['state'] != 'planned' or approval != state['approval_sha256']:
            raise ValueError('Approve this exact plan once; known or ambiguous submissions must be reconciled, never resubmitted.')
        if version('comfy-sdk') != plan['sdk']['version']:
            raise ValueError('SDK version changed; prepare a new plan.')
        current = profiles.context(root, plan['context']['package']['name'], plan['context']['server'])
        if digest(current) != digest(plan['context']):
            raise ValueError('Installed workflow, inputs or runtime changed; prepare a new plan.')
        proxy_check(current['server'], plan['sdk_url'])
        for item in current['inputs']:
            if hashlib.sha256((path / item['filename']).read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Prepared SDK input changed.')
        with client_factory(plan) as client:
            workflow = client.workflows.from_json(copy.deepcopy(plan['workflow']))
            for item in current['inputs']:
                asset = client.assets.from_file(path / item['filename'])
                asset.commit()  # Upload preparation precedes the one fenced submission.
                workflow.set_input(item['node'], 'image', asset.as_reference())
            atomic_json(path / 'submitted-workflow.json', workflow.json)
            state['submitted_workflow_sha256'] = digest(workflow.json)
            if profiles.read(path / 'control.json')['stop']:
                state.update(state='cancelled', execution_may_continue=False)
                atomic_json(path / 'state.json', state)
                return state
            state.update(state='submitting', started_at=timestamp(), deadline_epoch=time.time()+plan['deadline_seconds'], execution_may_continue=True)
            atomic_json(path / 'state.json', state)
            try:
                job = client.submit(workflow, idempotency_key=plan['idempotency_key'])
                state.update(state='accepted', job_id=job.id)
                atomic_json(path / 'state.json', state)
            except BaseException as error:
                # Include interrupts and a lost ACK write. A retained id is enough
                # for reconciliation; no exception authorizes another submit.
                state.update(state='unresolved', error=type(error).__name__)
                atomic_json(path / 'state.json', state)
                raise
            while True:
                if profiles.read(path / 'control.json')['stop'] and not state.get('cancel_requested'):
                    known = client.jobs.get(state['job_id'])
                    if known.id != state['job_id']:
                        raise ValueError('SDK returned a different job identity.')
                    known.cancel()
                    state['cancel_requested'] = True
                result = collect(path, plan, state, client)
                if result['state'] in {'completed', 'failed', 'cancelled', 'timed_out'}:
                    return result
                time.sleep(.2)


def reconcile(root, token, client_factory=client_for):
    path, plan, state = load(root, token)
    with lease(path / 'worker.lock'):
        path, plan, state = load(root, token)
        if state['state'] in {'completed', 'failed', 'cancelled'}:
            return state
        if not state.get('job_id'):
            if state['state'] == 'submitting':
                state.update(state='unresolved', execution_may_continue=True)
                atomic_json(path / 'state.json', state)
            return state  # No id means no safe lookup. Never submit from here.
        with client_factory(plan) as client:
            return collect(path, plan, state, client)


def stop(root, token, client_factory=client_for):
    path, plan, state = load(root, token)
    atomic_json(path / 'control.json', {'stop': True})
    try:
        lock = lease(path / 'worker.lock')
    except ValueError:
        return dict(state, stop_requested=True, note='Worker will cancel only the known SDK job when it can; an in-flight request may continue.')
    with lock:
        path, plan, state = load(root, token)
        if state['state'] in {'completed', 'failed', 'cancelled'}:
            return state
        if state['state'] == 'planned':
            state.update(state='cancelled', execution_may_continue=False)
        elif state.get('job_id'):
            with client_factory(plan) as client:
                job = client.jobs.get(state['job_id'])
                if job.id != state['job_id']:
                    raise ValueError('SDK returned a different job identity.')
                job.cancel()  # SDK job-scoped cancel; never a global native interrupt.
            state.update(state='cancel_requested', execution_may_continue=True,
                         note='Cancellation requested for the known job only; reconcile to confirm. No new submission.')
        else:
            state.update(state='unresolved', execution_may_continue=True,
                         note='No acknowledged job id. Cannot safely target cancellation; inspect server records.')
        atomic_json(path / 'state.json', state)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('name', choices=sorted(profiles.PROFILES))
    plan.add_argument('--comfy-url', required=True)
    plan.add_argument('--sdk-url', required=True)
    for action in ('start', 'status', 'reconcile', 'stop'):
        command = commands.add_parser(action)
        command.add_argument('id')
        if action == 'start':
            command.add_argument('--approve', required=True)
    for command in commands.choices.values():
        command.add_argument('--comfy-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'plan':
        result = prepare(args.comfy_dir, args.name, args.comfy_url, args.sdk_url)
    elif args.command == 'start':
        result = start(args.comfy_dir, args.id, args.approve)
    elif args.command == 'reconcile':
        result = reconcile(args.comfy_dir, args.id)
    elif args.command == 'stop':
        result = stop(args.comfy_dir, args.id)
    else:
        _, plan, state = load(args.comfy_dir, args.id)
        result = dict(state, plan=plan)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
