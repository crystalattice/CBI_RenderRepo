"""Mocked SDK transport. These cases are not Cloud or hardware qualification."""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from renderrepo import sdk_execution as sdk
from renderrepo.installation_history import atomic_json, lease
from renderrepo.permutation.provider import ComfyProvider


class SDKExecutionTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'input'
        self.inputs.mkdir()
        source = (sdk.profiles.assets() / 'rr-starter-base.png').read_bytes()
        (self.inputs / 'rr-starter-base.png').write_bytes(source)
        self.context = {'package': {'name': 'rr-starter-resize', 'version': '0.1.0'},
                        'workflow': json.loads((sdk.profiles.assets() / 'rr-starter-resize.json').read_text()),
                        'inputs': [{'node': '1', 'filename': 'rr-starter-base.png', 'sha256': sdk.profiles.digest(source), 'dimensions': [320, 200], 'batch': 1}],
                        'input_dir': str(self.inputs), 'server': 'http://127.0.0.1:1', 'runtime': {'fixture': True}}
        with Image.open(io.BytesIO(source)) as base:
            stream = io.BytesIO()
            base.convert('RGB').resize((160, 100), Image.Resampling.NEAREST).save(stream, format='PNG')
            self.output = stream.getvalue()
        self.submits = []
        self.cancelled = []
        self.job_status = 'succeeded'
        self.fail_submit = False
        self.fail_download = False
        self.job_id = 'recorded-job'
        self.gets = []
        self.upload_hook = None
        self.submit_hook = None
        self.real_proxy_check = sdk.proxy_check
        patches = [patch.object(sdk.profiles, 'context', side_effect=lambda *a: copy.deepcopy(self.context)),
                   patch.object(sdk, 'proxy_check', return_value={'status': 'healthy', 'upstream': self.context['server']}),
                   patch.object(sdk, 'version', return_value='0.4.0')]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)
        owner = self
        class Workflow:
            def __init__(self, graph): self.json = graph
            def set_input(self, node, field, value): self.json[node]['inputs'][field] = value
        class Asset:
            def commit(self):
                if owner.upload_hook: owner.upload_hook()
            def as_reference(self): return {'__type': 'core/ASSET', 'info': {'id': 'fixture-asset'}}
        class Output:
            type = 'image'
            content_type = 'image/png'
            size_bytes = len(owner.output)
            def to_bytes(self):
                if owner.fail_download: raise OSError('simulated retrieval failure')
                return owner.output
        class Job:
            id = 'recorded-job'
            @property
            def status(self): return owner.job_status
            def get_outputs(self, node): return [Output()]
            def cancel(self): owner.cancelled.append(self.id)
        def get(job_id):
            owner.gets.append(job_id)
            job = Job()
            job.id = owner.job_id
            return job
        class Client:
            workflows = SimpleNamespace(from_json=Workflow)
            assets = SimpleNamespace(from_file=lambda file: Asset())
            jobs = SimpleNamespace(get=get)
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def submit(self, workflow, idempotency_key):
                owner.submits.append((copy.deepcopy(workflow.json), idempotency_key))
                if owner.submit_hook: owner.submit_hook()
                if owner.fail_submit: raise OSError('simulated lost acknowledgement')
                return Job()
        self.factory = lambda plan: Client()

    def plan(self):
        return sdk.prepare(self.root, 'rr-starter-resize', 'http://127.0.0.1:1', 'http://127.0.0.1:2')

    def start(self, plan):
        return sdk.start(self.root, plan['plan']['id'], plan['approval_sha256'], self.factory)

    def test_prepare_no_submission_and_one_approved_real_sdk_contract(self):
        plan = self.plan()
        self.assertEqual(self.submits, [])
        self.assertEqual(plan['plan']['runs'], 1)
        self.assertEqual(plan['plan']['context']['inputs'][0]['dimensions'], [320, 200])
        result = self.start(plan)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['outputs'][0]['dimensions'], [160, 100])
        self.assertEqual(self.submits[0][1], plan['plan']['idempotency_key'])
        path, _, _ = sdk.load(self.root, plan['plan']['id'])
        self.assertEqual(sdk.profiles.read(path/'submitted-workflow.json'), self.submits[0][0])
        self.assertEqual(sdk.reconcile(self.root, plan['plan']['id'], self.factory), result)
        with self.assertRaises(ValueError): self.start(plan)
        self.assertEqual(len(self.submits), 1)

    def test_wrong_approval_changed_context_version_input_and_plan_block(self):
        plan = self.plan()
        with self.assertRaises(ValueError): sdk.start(self.root, plan['plan']['id'], 'bad', self.factory)
        self.context['runtime'] = {'changed': True}
        with self.assertRaises(ValueError): self.start(plan)
        self.context['runtime'] = {'fixture': True}
        with patch.object(sdk, 'version', return_value='changed'):
            with self.assertRaises(ValueError): self.start(plan)
        path, _, _ = sdk.load(self.root, plan['plan']['id'])
        (path / 'rr-starter-base.png').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.start(plan)
        atomic_json(path/'plan.json', {'id': plan['plan']['id']})
        with self.assertRaises(ValueError): sdk.load(self.root, plan['plan']['id'])
        self.assertEqual(self.submits, [])

    def test_ambiguous_ack_and_backend_restart_never_submit_again(self):
        plan = self.plan()
        self.fail_submit = True
        with self.assertRaises(OSError): self.start(plan)
        result = sdk.reconcile(self.root, plan['plan']['id'], self.factory)
        self.assertEqual(result['state'], 'unresolved')
        self.assertEqual(self.gets, [])
        with self.assertRaises(ValueError): self.start(plan)
        self.assertEqual(len(self.submits), 1)

    def test_accepted_job_retrieval_failure_reconciles_no_duplicate(self):
        plan = self.plan()
        self.fail_download = True
        with self.assertRaises(OSError): self.start(plan)
        _, _, state = sdk.load(self.root, plan['plan']['id'])
        self.assertEqual(state['job_id'], 'recorded-job')
        self.fail_download = False
        result = sdk.reconcile(self.root, plan['plan']['id'], self.factory)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(self.submits), 1)

    def test_ack_write_failure_retains_id_for_reconciliation(self):
        plan = self.plan()
        write = sdk.atomic_json
        failed = False
        def atomic(path, value):
            nonlocal failed
            if value.get('state') == 'accepted' and not failed:
                failed = True
                raise OSError('simulated ACK persistence failure')
            return write(path, value)
        with patch.object(sdk, 'atomic_json', side_effect=atomic):
            with self.assertRaises(OSError): self.start(plan)
        self.assertEqual(sdk.reconcile(self.root, plan['plan']['id'], self.factory)['state'], 'completed')
        self.assertEqual(len(self.submits), 1)

    def test_duplicate_runner_lock_and_incomplete_submitting_fence(self):
        plan = self.plan()
        path, _, state = sdk.load(self.root, plan['plan']['id'])
        with lease(path/'worker.lock'):
            with self.assertRaises(ValueError): self.start(plan)
        state.update(state='submitting')
        atomic_json(path/'state.json', state)
        self.assertEqual(sdk.reconcile(self.root, plan['plan']['id'], self.factory)['state'], 'unresolved')
        self.assertEqual(self.submits, [])

    def test_timeout_may_continue_and_later_completion_keeps_deadline_evidence(self):
        plan = self.plan()
        path, _, state = sdk.load(self.root, plan['plan']['id'])
        state.update(state='accepted', job_id='recorded-job', deadline_epoch=time.time()-1)
        atomic_json(path/'state.json', state)
        self.job_status = 'running'
        result = sdk.reconcile(self.root, plan['plan']['id'], self.factory)
        self.assertEqual(result['state'], 'timed_out')
        self.assertTrue(result['execution_may_continue'])
        self.job_status = 'succeeded'
        result = sdk.reconcile(self.root, plan['plan']['id'], self.factory)
        self.assertTrue(result['deadline_exceeded'])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.submits, [])

    def test_execution_failure_and_wrong_pixels_are_not_success(self):
        plan = self.plan()
        self.job_status = 'failed'
        self.assertEqual(self.start(plan)['state'], 'failed')
        self.job_status = 'succeeded'
        other = self.plan()
        self.output = b'not a PNG'
        with self.assertRaises(Exception): self.start(other)
        self.assertNotEqual(sdk.load(self.root, other['plan']['id'])[2]['state'], 'completed')

    def test_stop_during_upload_prevents_submit_and_stop_running_is_scoped(self):
        plan = self.plan()
        self.upload_hook = lambda: sdk.stop(self.root, plan['plan']['id'], self.factory)
        self.assertEqual(self.start(plan)['state'], 'cancelled')
        self.assertEqual(self.submits, [])
        self.upload_hook = None
        other = self.plan()
        self.submit_hook = lambda: sdk.stop(self.root, other['plan']['id'], self.factory)
        self.start(other)
        self.assertEqual(self.cancelled, ['recorded-job'])

    def test_proxy_mismatch_and_credential_presence_rejected(self):
        with patch.object(sdk.profiles.Native, 'request', return_value={'status':'healthy','upstream':'http://127.0.0.1:other'}):
            with self.assertRaises(ValueError):
                self.real_proxy_check('http://127.0.0.1:1', 'http://127.0.0.1:2')
        with patch.dict('os.environ', {'COMFY_API_KEY':'fake-fixture-never-sent'}):
            with self.assertRaises(ValueError): sdk.client_for({'sdk_url':'http://127.0.0.1:2'})


class ManagedCapabilitiesTests(unittest.TestCase):
    def test_missing_nodes_and_models_block_before_upload(self):
        for inventory, graph in [({}, {'1':{'class_type':'Unavailable','inputs':{}}}),
            ({'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': [['available']]}}}},
             {'1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'absent'}}})]:
            provider = ComfyProvider()
            calls = []
            provider.http = SimpleNamespace(request=lambda *a: (inventory, None), upload=lambda *a: calls.append(a))
            with self.assertRaisesRegex(ValueError, 'explicitly select another target'):
                provider.prepare(graph, Path('/unused'), {}, {})
            self.assertEqual(calls, [])
            self.assertEqual(provider.target_evidence['state'], 'missing')
            self.assertFalse(provider.target_evidence['installation_supported'])

    def test_unavailable_inventory_or_dynamic_model_is_unknown(self):
        provider = ComfyProvider()
        def unavailable(*a): raise OSError('simulated unsupported capability endpoint')
        provider.http = SimpleNamespace(request=unavailable)
        graph = {'1':{'class_type':'SaveImage','inputs':{}}}
        self.assertEqual(provider.prepare(graph, Path('/unused'), {}, {}), graph)
        self.assertEqual(provider.target_evidence['state'], 'unknown')
        provider.http = SimpleNamespace(request=lambda *a: ({'CheckpointLoaderSimple':{}}, None))
        provider.prepare({'1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'x'}}}, Path('/unused'), {}, {})
        self.assertEqual(provider.target_evidence['state'], 'unknown')
