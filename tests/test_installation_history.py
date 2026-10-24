import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import unittest
import os
import subprocess
import sys
from unittest.mock import patch

import test_comfy_extension as fixtures
from renderrepo.comfy_extension import PackageUI
from renderrepo.installation_history import atomic_json, lease, within_scope
from renderrepo.nodes import git, install_requirements


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ExtensionTests.setUp
    package = fixtures.ExtensionTests.package
    complete = fixtures.ExtensionTests.complete

    def backend(self):
        ui = PackageUI(self.comfy)
        self.addCleanup(ui.temporary.cleanup)
        return ui

    async def until(self, predicate):
        for _ in range(500):
            value = predicate()
            if value:
                return value
            await asyncio.sleep(.02)
        self.fail('Timed out waiting for local fixture worker')

    async def test_history_exposes_transfer_controls_and_failure_without_changing_lifecycle(self):
        job = await self.ui.preview(self.package(), {})
        token = job['session']
        before = (self.ui.get(token)['directory'] / 'job.json').read_bytes()
        status = dict(state='running', outcome=None, worker_active=True,
            transfer={'state': 'paused', 'bytes': 10, 'total_bytes': 100},
            control={'action': 'pause', 'rate_mbps': 1}, last_activity='2026-10-16T00:00:00Z',
            workflow_available=False, workflow_path=None, result=None)
        with patch.object(self.ui, 'status', return_value=status):
            entry = self.ui.history()[0]
            self.assertEqual(entry['state'], 'running')
            self.assertEqual(entry['transfer']['state'], 'paused')
            self.assertEqual(entry['control']['action'], 'pause')
        failure = 'FAILED SOURCE: input.bin: http://localhost/file: HTTP Error 404: Not Found'
        status.update(state='done', outcome='failed', worker_active=False,
            result={'exit_code': 1, 'log': 'verbose diagnostics\n' + failure + '\nretry trace'})
        with patch.object(self.ui, 'status', return_value=status):
            self.assertEqual(self.ui.history()[0]['failure'], failure)
        self.assertEqual((self.ui.get(token)['directory'] / 'job.json').read_bytes(), before)

    async def test_completed_history_survives_backend_recreation_and_removal_keeps_installed_files(self):
        source = self.root / 'source.bin'; source.write_bytes(b'model')
        self.manifest['artifacts'] = [{'filename': 'tiny.bin', 'target': 'models/loras/tiny.bin',
            'sha256': hashlib.sha256(b'model').hexdigest(), 'sources': [{'uri': str(source)}]}]
        job = await self.ui.preview(self.package(), {})
        token = job['session']
        await self.complete(token)
        node = self.comfy / 'custom_nodes/example/node.py'; node.parent.mkdir(parents=True); node.write_text('node')
        python_package = self.root / 'shared-python.py'; python_package.write_text('shared')
        ui = self.backend()
        entry = ui.history()[0]
        self.assertEqual((entry['name'], entry['version'], entry['outcome']), ('demo', '1.0', 'completed'))
        self.assertTrue(entry['last_activity'])
        self.assertTrue(ui.status(token)['workflow_available'])
        self.assertIn('INSTALLED WORKFLOW', ui.status(token)['result']['log'])
        self.assertEqual(json.loads(ui.workflow(token)), self.graph)
        installed = Path(entry['workflow_path'])
        ui.discard(token)
        self.assertEqual(ui.history(), [])
        self.assertTrue(installed.is_file())
        self.assertEqual((self.comfy / 'models/loras/tiny.bin').read_bytes(), b'model')
        self.assertEqual(node.read_text(), 'node')
        self.assertEqual(python_package.read_text(), 'shared')
        self.assertTrue(list((self.comfy / '.renderrepo/receipts').glob('*.json')))

    async def test_unfinished_record_recovers_without_starting_work(self):
        job = await self.ui.preview(self.package(), {})
        session = self.ui.get(job['session'])
        session.update(state='running', approved_scope=job['plan']['scope'])
        self.ui.persist(session)
        ui = self.backend()
        status = ui.status(job['session'])
        self.assertEqual(status['state'], 'interrupted')
        self.assertIn('nothing restarts automatically', status['result']['log'])
        self.assertFalse((self.comfy / 'user').exists())
        self.assertEqual(ui.get(job['session'])['archive_sha256'], job['archive_sha256'])
        self.assertEqual((await ui.resume(job['session']))['state'], 'running')
        await ui.get(job['session'])['task']
        self.assertTrue(ui.status(job['session'])['workflow_available'])

    async def test_recovery_cannot_overwrite_worker_completion_during_lease_probe(self):
        job = await self.ui.preview(self.package(), {})
        token = job['session']; session = self.ui.get(token)
        session.update(state='running')
        self.ui.persist(session)
        path = session['directory'] / 'job.json'
        def finish(_path):
            record = json.loads(path.read_text())
            record.update(state='done', outcome='completed', result={'exit_code': 0, 'log': 'Finished'})
            atomic_json(path, record)
            return False
        with patch('renderrepo.comfy_extension.active', side_effect=finish):
            recovered = self.backend()
        self.assertEqual(recovered.status(token)['outcome'], 'completed')
        self.assertEqual(json.loads(path.read_text())['state'], 'done')

    async def test_browser_reconnection_and_restart_resume_reuse_and_range_without_upload(self):
        payloads = {'/first.bin': b'a' * 65536, '/second.bin': b'b' * 262144}
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                data = payloads[self.path]
                value = self.headers.get('Range'); requests.append((self.path, value))
                offset = int(value[6:-1]) if value else 0
                self.send_response(206 if value else 200)
                self.send_header('Content-Length', str(len(data) - offset))
                if value:
                    self.send_header('Content-Range', f'bytes {offset}-{len(data)-1}/{len(data)}')
                self.end_headers()
                self.wfile.write(data[offset:])
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.manifest['artifacts'] = [{'filename': name[1:], 'target': 'models/loras/' + name[1:],
            'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'sources': [{'uri': f'http://127.0.0.1:{server.server_port}{name}'}]}
            for name, data in payloads.items()]
        job = await self.ui.preview(self.package(), {})
        token = job['session']
        await self.ui.approve(token, True)
        original = self.ui.get(token)
        self.ui.control_download(token, {'action': 'run', 'rate_mbps': .2})
        destination = self.comfy / 'models/loras'
        await self.until(lambda: (destination / 'first.bin').exists() and
                         any(p.stat().st_size > 0 for p in destination.glob('*.part')))
        self.ui.control_download(token, {'action': 'pause', 'rate_mbps': .2})
        await self.until(lambda: (self.ui.status(token).get('transfer') or {}).get('state') == 'paused')
        # A new browser/backend reconnects to the same live worker and controls.
        reconnect = self.backend()
        restored = reconnect.status(token)
        self.assertEqual(restored['state'], 'interrupted')
        self.assertTrue(restored['worker_active'])
        self.assertEqual(restored['control']['action'], 'pause')
        self.assertIn('RR_TRANSFER ', restored['progress'])
        with self.assertRaisesRegex(ValueError, 'still running'):
            await reconnect.resume(token)
        with self.assertRaisesRegex(ValueError, 'still active'):
            reconnect.discard(token)
        # Stop only this disposable fixture's worker; persisted state is untouched.
        original['process'].terminate()
        await original['task']
        partial_size = next(destination.glob('*.part')).stat().st_size
        recovered = self.backend()
        self.assertEqual(recovered.status(token)['state'], 'interrupted')
        self.assertEqual((await recovered.resume(token))['state'], 'running')
        recovered.control_download(token, {'action': 'run', 'rate_mbps': 0})
        await recovered.get(token)['task']
        result = recovered.status(token)
        self.assertEqual(result['result']['exit_code'], 0, result)
        self.assertEqual(sum(path == '/first.bin' for path, _ in requests), 1)
        self.assertIn(('/second.bin', f'bytes={partial_size}-'), requests)
        self.assertIn('REUSE: first.bin', result['progress'])
        self.assertEqual((destination / 'second.bin').read_bytes(), payloads['/second.bin'])
        self.assertEqual(len(result['attempts']), 2)
        self.assertTrue(result['workflow_available'])

    async def test_changed_destination_revises_plan_before_new_approval(self):
        source = self.root / 'example.bin'; source.write_bytes(b'input')
        self.manifest['artifacts'] = [{'filename': 'example.bin', 'target': 'input/example.bin',
                                      'sources': [{'uri': str(source)}]}]
        job = await self.ui.preview(self.package(), {})
        token = job['session']; session = self.ui.get(token)
        session.update(state='interrupted', approved_scope=job['plan']['scope'])
        self.ui.persist(session)
        ui = self.backend(); ui.input_dir = self.root / 'new-input'
        result = await ui.resume(token)
        self.assertTrue(result['approval_required'])
        self.assertIn(str(ui.input_dir), result['plan']['log'])
        self.assertFalse(ui.input_dir.exists())
        await ui.approve(token, True); await ui.get(token)['task']
        self.assertEqual((ui.input_dir / 'example.bin').read_bytes(), b'input')

    async def test_worker_leases_block_duplicate_even_with_interrupted_record(self):
        job = await self.ui.preview(self.package(), {})
        token = job['session']; session = self.ui.get(token)
        session.update(state='interrupted', approved_scope=job['plan']['scope'])
        self.ui.persist(session)
        with lease(self.ui.install_lock), lease(session['directory'] / 'worker.lock'):
            ui = self.backend()
            with self.assertRaisesRegex(ValueError, 'still active'):
                await ui.resume(token)
            self.assertFalse((self.comfy / 'user').exists())

    async def test_corrupt_and_missing_job_files_are_isolated_and_removable(self):
        good = await self.ui.preview(self.package(), {})
        bad = []
        for value in ('{bad json', None):
            token = secrets.token_urlsafe(32); bad.append(token)
            directory = self.ui.store / token; directory.mkdir()
            if value:
                (directory / 'job.json').write_text(value)
        ui = self.backend()
        entries = {item['session']: item for item in ui.history()}
        self.assertEqual(entries[good['session']]['state'], 'reviewed')
        for token in bad:
            self.assertEqual(entries[token]['state'], 'corrupt')
            self.assertTrue(entries[token]['error'])
            ui.discard(token)
        self.assertEqual(len(ui.history()), 1)
        self.assertTrue(ui.get(good['session'])['package'].is_dir())

    async def test_retained_package_mutation_cannot_reuse_approval(self):
        job = await self.ui.preview(self.package(), {})
        token = job['session']; session = self.ui.get(token)
        session.update(state='interrupted', approved_scope=job['plan']['scope'])
        self.ui.persist(session)
        (session['package'] / 'workflow.json').write_text(json.dumps(dict(self.graph, extra={'changed': True})))
        with self.assertRaisesRegex(ValueError, 'Retained package changed'):
            await self.backend().resume(token)
        self.assertFalse((self.comfy / 'user').exists())

    def test_scope_never_expands_sources_code_or_python_without_approval(self):
        original = {'workflow': {'path': '/workflow'}, 'package': 'hash', 'nodes': [{'revision': 'old'}],
                    'python': {'path': '/python'}, 'artifacts': [
                        {'filename': 'model', 'operation': 'acquire', 'destination': '/model', 'sources': ['https://old']} ]}
        self.assertTrue(within_scope(original, original))
        for key, value in [('python', {'path': '/new-python'}), ('nodes', [{'revision': 'new'}]),
                           ('artifacts', [{**original['artifacts'][0], 'sources': ['https://new']}])]:
            self.assertFalse(within_scope(original, {**original, key: value}))

    def test_git_and_pip_children_inherit_worker_leases(self):
        target = self.root / 'node'; target.mkdir()
        (target / 'requirements.txt').write_text('')
        with lease(self.ui.install_lock) as lock, patch.dict(os.environ, {'RENDERREPO_WORKER_FDS': str(lock.fileno())}):
            with patch('renderrepo.nodes.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'ok', '')) as run:
                git(None, '--version')
                self.assertEqual(run.call_args.kwargs['pass_fds'], (lock.fileno(),))
                install_requirements({'target': target, 'python': Path(sys.executable), 'name': 'node'}, 1)
                self.assertEqual(run.call_args.kwargs['pass_fds'], (lock.fileno(),))

    def test_failed_atomic_write_preserves_previous_record(self):
        path = self.root / 'job.json'
        atomic_json(path, {'state': 'interrupted'})
        with self.assertRaises(TypeError):
            atomic_json(path, {'state': object()})
        self.assertEqual(json.loads(path.read_text()), {'state': 'interrupted'})
        self.assertEqual([p.name for p in self.root.iterdir() if p.is_file()], ['job.json'])


class NodeScopeConsentTests(unittest.TestCase):
    def test_reuse_cannot_become_code_install_and_legacy_full_approvals_remain_valid(self):
        from renderrepo.installation_history import within_scope
        node={'name':'fixture','source':'/owned/git','revision':'a'*40,'target':'/comfy/custom_nodes/fixture'}
        reuse={'workflow':{},'nodes':[{**node,'operation':'reuse'}]}
        install={'workflow':{},'nodes':[{**node,'operation':'install'}]}
        legacy={'workflow':{},'nodes':[node]}
        self.assertFalse(within_scope(reuse,install))
        self.assertTrue(within_scope(install,reuse))
        self.assertTrue(within_scope(legacy,install))
        self.assertTrue(within_scope(legacy,reuse))
        changed={'workflow':{},'nodes':[{**node,'source':'/different/git','operation':'reuse'}]}
        self.assertFalse(within_scope(reuse,changed))
        self.assertFalse(within_scope({'workflow':{},'nodes':None},install))
        self.assertFalse(within_scope({'workflow':{},'nodes':[None]},install))
