import contextlib
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import yaml

from renderrepo.cli import main
from renderrepo.receipts import read_receipts
from renderrepo.repository import version_key, cache_path, cache_status, sources


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'publisher'
        self.repo.mkdir()
        self.comfy = self.root / 'receiver/ComfyUI'
        self.comfy.mkdir(parents=True)
        self.config = self.root / 'receiver/sources.yaml'
        self.cache = self.root / 'receiver/cache'
        self.workflow = self.root / 'workflow.json'
        self.workflow.write_text(json.dumps({'1': {'class_type': 'SaveImage', 'inputs': {}}}))
        self.requests = []
        requests = self.requests
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append(self.path)
                super().do_GET()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(self.repo)))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def cli(self, *args, answers=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), patch('builtins.input', side_effect=answers):
            code = main(list(map(str, args)))
        return code, out.getvalue()

    def pack(self, version='1.0', name='demo'):
        return self.cli('pack', self.workflow, '--output', self.repo / f'{name}-{version}.rrpkg',
                        '--name', name, '--version', version)

    def source(self, name='test', uri=None):
        return self.cli('source', 'add', name, uri or self.url, '--sources', self.config)

    def command(self, command, *extra, answers=()):
        return self.cli(command, *extra, '--sources', self.config, '--cache-dir', self.cache, answers=answers)

    def install(self, *extra, answers=()):
        return self.command('install', 'demo', '--comfy-dir', self.comfy, *extra, answers=answers)

    def publish(self):
        self.assertEqual(self.cli('index', self.repo)[0], 0)

    def ready(self):
        self.pack()
        self.publish()
        self.source()
        self.assertEqual(self.command('update')[0], 0)
        self.requests.clear()

    def test_two_version_http_acceptance(self):
        self.ready()
        self.pack('2.0')
        self.publish()
        self.command('update')
        self.requests.clear()
        code, text = self.install('--version', '1.0', '--yes')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.requests, ['/demo-1.0.rrpkg'])
        self.assertEqual(read_receipts(self.comfy)[0]['version'], '1.0')
        self.requests.clear()
        code, text = self.command('update', '--comfy-dir', self.comfy)
        self.assertEqual(code, 0, text)
        self.assertIn('1.0 -> 2.0 [test]', text)
        self.assertEqual(self.requests, ['/index.json'])
        self.requests.clear()
        self.assertIn('2.0', self.command('search', 'DEM')[1])
        self.assertIn('UPDATE AVAILABLE', self.command('updates', '--comfy-dir', self.comfy)[1])
        self.assertEqual(self.requests, [])


    def test_update_after_model_install_never_fetches_models(self):
        weight = self.repo / 'tiny.safetensors'
        weight.write_bytes(b'tiny weights')
        self.workflow.write_text(json.dumps({'nodes': [{
            'id': 1, 'type': 'UNETLoader', 'widgets_values': ['tiny.safetensors'],
            'properties': {'models': [{'name': 'tiny.safetensors', 'directory': 'diffusion_models',
                                       'url': self.url + '/tiny.safetensors'}]}}], 'links': []}))
        code, text = self.cli('pack', self.workflow, '--output', self.repo / 'demo-1.rrpkg',
                              '--name', 'demo', '--version', '1', '--artifact-dir', self.repo)
        self.assertEqual(code, 0, text)
        self.publish()
        self.source()
        self.command('update')
        self.requests.clear()
        self.assertEqual(self.install('--yes')[0], 0)
        self.assertEqual(self.requests, ['/demo-1.rrpkg', '/tiny.safetensors'])
        self.cli('pack', self.workflow, '--output', self.repo / 'demo-2.rrpkg', '--name', 'demo',
                 '--version', '2', '--artifact-dir', self.repo)
        self.publish()
        self.requests.clear()
        code, text = self.command('update', '--comfy-dir', self.comfy)
        self.assertEqual(code, 0, text)
        self.assertIn('1 -> 2', text)
        self.assertEqual(self.requests, ['/index.json'])
        self.assertEqual(read_receipts(self.comfy)[0]['version'], '1')

    def test_changed_same_name_repository_does_not_change_update_origin(self):
        self.ready()
        self.install('--yes')
        other = self.root / 'other'
        other.mkdir()
        self.cli('pack', self.workflow, '--output', other / 'demo.rrpkg', '--name', 'demo', '--version', '9')
        self.cli('index', other)
        self.cli('source', 'remove', 'test', '--sources', self.config)
        self.source('test', other)
        self.command('update')
        self.assertNotIn('UPDATE AVAILABLE', self.command('updates', '--comfy-dir', self.comfy)[1])

    def test_local_repository(self):
        self.pack()
        self.publish()
        self.source(uri=self.repo)
        self.assertEqual(self.command('update')[0], 0)
        self.assertEqual(self.install('--yes')[0], 0)
        self.assertEqual(self.requests, [])

    def test_latest_version_numeric(self):
        self.pack('1.9')
        self.pack('1.10')
        self.publish()
        self.source()
        self.command('update')
        self.assertEqual(self.install('--yes')[0], 0)
        self.assertEqual(read_receipts(self.comfy)[0]['version'], '1.10')

    def test_inspection_fetches_archive_but_decline_prevents_install(self):
        self.ready()
        self.assertEqual(self.install(answers=['n'])[0], 1)
        self.assertEqual(self.requests, ['/demo-1.0.rrpkg'])
        self.assertEqual(list(self.comfy.iterdir()), [])
        self.assertTrue((self.cache / 'packages').exists())

    def test_decline_artifact_plan_after_fetch(self):
        self.ready()
        self.assertEqual(self.install(answers=['n'])[0], 1)
        self.assertEqual(self.requests, ['/demo-1.0.rrpkg'])
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_cache_reuse_no_archive_refetch(self):
        self.ready()
        self.install('--yes')
        self.requests.clear()
        self.assertEqual(self.install('--yes')[0], 0)
        self.assertEqual(self.requests, [])

    def test_corrupted_archive_digest_rejected(self):
        self.ready()
        path = self.repo / 'demo-1.0.rrpkg'
        raw = bytearray(path.read_bytes())
        raw[0] ^= 1
        path.write_bytes(raw)
        code, text = self.install('--yes')
        self.assertEqual(code, 2, text)
        self.assertIn('sha256', text)
        self.assertEqual(list(self.comfy.iterdir()), [])
        self.assertFalse(list((self.cache / 'packages').glob('*.part')))

    def test_corrupted_cached_archive_refetched(self):
        self.ready()
        self.install('--yes')
        archive = next((self.cache / 'packages').glob('*.rrpkg'))
        archive.write_bytes(b'corrupt')
        self.requests.clear()
        self.assertEqual(self.install('--yes')[0], 0)
        self.assertEqual(self.requests, ['/demo-1.0.rrpkg'])

    def test_ambiguous_repository_requires_choice(self):
        self.ready()
        self.source('second')
        self.command('update')
        self.requests.clear()
        self.assertEqual(self.install('--yes')[0], 2)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.install('--repo', 'second', '--yes')[0], 0)
        self.assertEqual(read_receipts(self.comfy)[0]['repository']['name'], 'second')

    def test_update_reports_only_pinned_repository(self):
        self.ready()
        self.install('--yes')
        other = self.root / 'other'
        other.mkdir()
        self.cli('pack', self.workflow, '--output', other / 'demo.rrpkg', '--name', 'demo', '--version', '9')
        self.cli('index', other)
        self.source('other', other)
        self.command('update')
        self.assertNotIn('UPDATE AVAILABLE', self.command('updates', '--comfy-dir', self.comfy)[1])

    def test_failure_retains_previous_cache(self):
        self.ready()
        cached = next(p for p in (self.cache / 'indexes').glob('*.json') if not p.name.endswith('.status.json'))
        old = cached.read_bytes()
        (self.repo / 'index.json').write_text('{bad json')
        self.assertEqual(self.command('update')[0], 1)
        self.assertEqual(cached.read_bytes(), old)
        self.assertIn('demo', self.command('search')[1])
        self.assertIn('STALE', self.command('search')[1])
        self.assertIn('STALE', self.cli('source', 'list', '--sources', self.config, '--cache-dir', self.cache)[1])

    def test_descriptions_index_generation_and_discovery(self):
        code, text = self.cli('pack', self.workflow, '--output', self.repo / 'described.rrpkg', '--name', 'demo', '--version', '1.0', '--description', 'Clothing swap reference workflow')
        self.assertEqual(code, 0, text)
        self.publish()
        index = json.loads((self.repo / 'index.json').read_text())
        self.assertEqual(index['index_version'], 1)
        self.assertEqual(index['packages'][0]['description'], 'Clothing swap reference workflow')
        self.source(uri=self.repo)
        self.assertEqual(self.command('refresh')[0], 0)
        code, text = self.command('search', 'CLOTHING')
        self.assertEqual(code, 0, text)
        self.assertIn('demo 1.0 [test]', text)
        self.assertIn('Clothing swap reference workflow', text)
        self.assertIn('demo', self.command('packages')[1])
        self.assertEqual(self.requests, [])

    def test_source_disable_enable_persists_and_excludes_all_acquisition(self):
        self.ready()
        self.assertEqual(self.cli('source', 'disable', 'test', '--sources', self.config)[0], 0)
        self.assertFalse(yaml.safe_load(self.config.read_text())['repositories'][0]['enabled'])
        self.assertIn('disabled', self.cli('source', 'list', '--sources', self.config, '--cache-dir', self.cache)[1])
        self.assertNotIn('demo', self.command('packages')[1])
        self.assertIn('disabled', self.command('refresh')[1])
        code, text = self.install('--repo', 'test', '--yes')
        self.assertEqual(code, 2)
        self.assertIn('disabled', text)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.cli('source', 'enable', 'test', '--sources', self.config)[0], 0)
        self.assertTrue(yaml.safe_load(self.config.read_text())['repositories'][0]['enabled'])
        self.assertIn('demo', self.command('search')[1])
        self.assertEqual(self.requests, [])

    def test_stale_cache_recovers_and_preserves_last_valid_data(self):
        self.ready()
        self.install('--yes')
        self.requests.clear()
        args = SimpleNamespace(sources=self.config, cache_dir=self.cache)
        repo = sources(args)[0]
        cache = cache_path(args, repo)
        original = cache.read_bytes()
        receipts = {p: p.read_bytes() for p in (self.comfy / '.renderrepo/receipts').glob('*.json')}
        good = (self.repo / 'index.json').read_bytes()
        (self.repo / 'index.json').write_text('null')
        self.assertEqual(self.command('refresh')[0], 1)
        self.assertEqual(cache.read_bytes(), original)
        self.assertTrue(cache_status(args, repo)['stale'])
        self.assertIn('[STALE]', self.command('packages')[1])
        (self.repo / 'index.json').write_bytes(good)
        self.assertEqual(self.command('refresh')[0], 0)
        self.assertFalse(cache_status(args, repo)['stale'])
        self.assertNotIn('STALE', self.command('search')[1])
        self.assertEqual(self.requests, ['/index.json', '/index.json'])
        for p, data in receipts.items():
            self.assertEqual(p.read_bytes(), data)

    def test_refresh_failure_does_not_prevent_independent_source_refresh(self):
        self.ready()
        self.source('local', self.repo)
        config = yaml.safe_load(self.config.read_text())
        config['repositories'][0]['uri'] = self.url + '/missing/index.json'
        self.config.write_text(yaml.safe_dump(config))
        code, text = self.command('refresh')
        self.assertEqual(code, 1)
        self.assertIn('FAILED: test', text)
        self.assertIn('UPDATED: local', text)
        self.assertIn('demo 1.0 [local]', self.command('packages')[1])
        self.assertEqual(self.requests, ['/missing/index.json'])

    def test_refresh_and_discovery_never_acquire_or_run_installers(self):
        self.ready()
        self.requests.clear()
        with patch('renderrepo.repository.fetch_artifact', side_effect=AssertionError('No archives/models')), patch('subprocess.run', side_effect=AssertionError('No pip, Git or code installation')):
            self.assertEqual(self.command('refresh')[0], 0)
            self.assertEqual(self.command('search')[0], 0)
            self.assertEqual(self.command('packages')[0], 0)
            self.assertEqual(self.command('updates', '--comfy-dir', self.comfy)[0], 0)
        self.assertEqual(self.requests, ['/index.json'])
        self.assertEqual(list(self.comfy.iterdir()), [])
        self.assertFalse((self.cache / 'packages').exists())

    def test_malformed_index_fields_keep_cache_and_report_stale(self):
        self.ready()
        args = SimpleNamespace(sources=self.config, cache_dir=self.cache)
        cache = cache_path(args, sources(args)[0])
        old = cache.read_bytes()
        original = json.loads((self.repo / 'index.json').read_text())
        malformed = [None, [], {'index_version': True, 'packages': []}, {'index_version': 2, 'packages': []}]
        for field, value in [('description', 7), ('name', ''), ('uri', 'file:///etc/passwd'), ('unexpected', 'x')]:
            index = json.loads(json.dumps(original))
            index['packages'][0][field] = value
            malformed.append(index)
        missing = json.loads(json.dumps(original))
        del missing['packages'][0]['description']
        malformed.append(missing)
        duplicate = json.loads(json.dumps(original))
        duplicate['packages'].append(dict(duplicate['packages'][0], version='1.0.0'))
        malformed.append(duplicate)
        for index in malformed:
            with self.subTest(index=index):
                (self.repo / 'index.json').write_text(json.dumps(index))
                self.assertEqual(self.command('refresh')[0], 1)
                self.assertEqual(cache.read_bytes(), old)
                self.assertIn('STALE', self.command('search')[1])

    def test_legacy_index_and_configuration_remain_readable(self):
        self.ready()
        index = json.loads((self.repo / 'index.json').read_text())
        index['index_version'] = 0
        del index['packages'][0]['description']
        (self.repo / 'index.json').write_text(json.dumps(index))
        config = yaml.safe_load(self.config.read_text())
        del config['repositories'][0]['enabled']
        self.config.write_text(yaml.safe_dump(config))
        self.assertEqual(self.command('refresh')[0], 0)
        self.assertIn('demo', self.command('packages')[1])

    def test_invalid_enabled_type_and_unknown_selected_source(self):
        self.ready()
        config = yaml.safe_load(self.config.read_text())
        config['repositories'][0]['enabled'] = 'false'
        self.config.write_text(yaml.safe_dump(config))
        self.assertEqual(self.command('refresh')[0], 2)
        config['repositories'][0]['enabled'] = True
        self.config.write_text(yaml.safe_dump(config))
        self.assertEqual(self.command('refresh', '--repo', 'missing')[0], 2)
        self.assertEqual(self.command('search', '--repo', 'missing')[0], 2)
        self.assertEqual(self.cli('source', 'disable', 'missing', '--sources', self.config)[0], 2)
        self.assertEqual(self.requests, [])

    def test_atomic_cache_publication_failure_retains_previous_index(self):
        self.ready()
        args = SimpleNamespace(sources=self.config, cache_dir=self.cache)
        repo = sources(args)[0]
        path = cache_path(args, repo)
        old = path.read_bytes()
        self.pack('2')
        self.publish()
        import os
        real_replace = os.replace

        def replace(source, target):
            if Path(target) == path:
                raise OSError('simulated atomic replace failure')
            return real_replace(source, target)
        with patch('renderrepo.repository.os.replace', side_effect=replace):
            self.assertEqual(self.command('refresh')[0], 1)
        self.assertEqual(path.read_bytes(), old)
        self.assertTrue(cache_status(args, repo)['stale'])
        self.assertNotIn('demo 2 [', self.command('search')[1])
        self.assertEqual(self.command('refresh')[0], 0)
        self.assertIn('demo 2 [test]', self.command('search')[1])

    def test_cache_status_missing_or_mismatched_is_visibly_stale(self):
        self.ready()
        args = SimpleNamespace(sources=self.config, cache_dir=self.cache)
        from renderrepo.repository import status_path
        repo = sources(args)[0]
        status = status_path(args, repo)
        status.unlink()
        self.assertIn('STALE', self.command('packages')[1])
        self.command('refresh')
        state = json.loads(status.read_text())
        state['cache_sha256'] = '0' * 64
        status.write_text(json.dumps(state))
        self.assertIn('cache/status mismatch', self.command('search')[1])

    def test_update_candidates_from_stale_cache_are_labelled(self):
        self.ready()
        self.install('--yes')
        self.pack('2')
        self.publish()
        self.command('refresh')
        (self.repo / 'index.json').write_text('{broken')
        self.command('refresh')
        code, text = self.command('updates', '--comfy-dir', self.comfy)
        self.assertEqual(code, 0)
        self.assertIn('1.0 -> 2 [test] [STALE]', text)
        self.requests.clear()
        self.cli('source', 'disable', 'test', '--sources', self.config)
        self.assertNotIn('UPDATE AVAILABLE', self.command('updates', '--comfy-dir', self.comfy)[1])
        self.assertEqual(self.requests, [])

    def test_removed_source_not_searched(self):
        self.ready()
        self.cli('source', 'remove', 'test', '--sources', self.config)
        self.assertNotIn('demo', self.command('search')[1])

    def test_changed_source_does_not_reuse_old_index(self):
        self.ready()
        config = yaml.safe_load(self.config.read_text())
        config['repositories'][0]['uri'] = self.url + '/other/index.json'
        self.config.write_text(yaml.safe_dump(config))
        self.assertNotIn('demo', self.command('search')[1])

    def test_invalid_package_identity_rejected(self):
        self.ready()
        index = json.loads((self.repo / 'index.json').read_text())
        index['packages'][0]['version'] = '2'
        (self.repo / 'index.json').write_text(json.dumps(index))
        self.command('update')
        self.assertEqual(self.install('--yes')[0], 2)
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_unknown_version_and_repository(self):
        self.ready()
        self.assertEqual(self.install('--version', '99', '--yes')[0], 2)
        self.assertEqual(self.install('--repo', 'missing', '--yes')[0], 2)

    def test_missing_cache_requires_update(self):
        self.pack()
        self.publish()
        self.source()
        self.assertEqual(self.install('--yes')[0], 2)
        self.assertEqual(self.requests, [])

    def test_equivalent_versions_duplicate(self):
        self.pack('1')
        self.pack('1.0.0')
        self.assertEqual(self.cli('index', self.repo)[0], 2)

    def test_opaque_versions_not_indexed(self):
        self.pack('beta')
        self.assertEqual(self.cli('index', self.repo)[0], 2)

    def test_numeric_version_rules(self):
        self.assertGreater(version_key('1.10'), version_key('1.9'))
        self.assertEqual(version_key('1'), version_key('1.0.0'))
        for version in ['01', '1.02', 'v1', '1.0-beta', '', 1]:
            with self.assertRaises(ValueError):
                version_key(version)

    def test_duplicate_source_and_invalid_name(self):
        self.source()
        self.assertEqual(self.source()[0], 2)
        self.assertEqual(self.source('../bad')[0], 2)

    def test_explicit_index_uri_and_space_in_archive(self):
        self.pack(name='demo space')
        self.publish()
        self.source(uri=self.url + '/index.json')
        self.command('update')
        code, text = self.command('install', 'demo space', '--comfy-dir', self.comfy, '--yes')
        self.assertEqual(code, 0, text)
        self.assertIn('/demo%20space-1.0.rrpkg', self.requests)

    def test_http_catalog_cannot_read_local_files(self):
        self.ready()
        index = json.loads((self.repo / 'index.json').read_text())
        index['packages'][0]['uri'] = (self.repo / 'demo-1.0.rrpkg').as_uri()
        (self.repo / 'index.json').write_text(json.dumps(index))
        self.assertEqual(self.command('update')[0], 1)

    def test_empty_configuration_and_index(self):
        self.assertEqual(self.command('update')[0], 0)
        self.assertEqual(self.command('search')[0], 0)
        self.assertEqual(self.cli('index', self.repo)[0], 0)

    def test_index_size_limit(self):
        self.ready()
        with patch('renderrepo.repository.MAX_INDEX_BYTES', 1):
            self.assertEqual(self.command('update')[0], 1)

    def test_bad_index_validation_preserves_cache(self):
        self.ready()
        original = json.loads((self.repo / 'index.json').read_text())
        for field, value in [('sha256', 'bad'), ('size_bytes', True), ('version', '1beta')]:
            data = json.loads(json.dumps(original))
            data['packages'][0][field] = value
            (self.repo / 'index.json').write_text(json.dumps(data))
            self.assertEqual(self.command('update')[0], 1)

    def test_relative_manual_configuration(self):
        self.pack()
        self.publish()
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(yaml.safe_dump({'repositories': [{'name': 'local', 'uri': '../publisher'}]}))
        self.assertEqual(self.command('update')[0], 0)
        self.assertIn('demo', self.command('search')[1])

    def test_changed_workflow_update_requires_removal_and_has_one_current_receipt(self):
        self.ready()
        self.assertEqual(self.install(answers=['y', 'y'])[0], 0)
        receipt = read_receipts(self.comfy)[0]
        target = Path(receipt['workflow_path'])
        old_workflow = target.read_bytes()
        receipts_before = {p: p.read_bytes() for p in self.comfy.glob('.renderrepo/receipts/*.json')}
        self.workflow.write_text(json.dumps({'1': {'class_type': 'SaveImage',
                                                 'inputs': {'filename_prefix': 'changed'}}}))
        self.pack('2.0')
        self.publish()
        self.command('refresh')
        self.assertIn('1.0 -> 2.0', self.command('updates', '--comfy-dir', self.comfy)[1])
        code, text = self.install('--version', '2.0', answers=['y'])
        self.assertEqual(code, 2, text)
        self.assertIn('review removal with renderrepo remove', text)
        self.assertEqual(target.read_bytes(), old_workflow)
        self.assertEqual({p: p.read_bytes() for p in receipts_before}, receipts_before)
        code, text = self.cli('remove', 'demo', '--comfy-dir', self.comfy, answers=['n'])
        self.assertEqual(code, 1, text)
        self.assertEqual(target.read_bytes(), old_workflow)
        self.assertEqual(self.cli('remove', 'demo', '--comfy-dir', self.comfy, answers=['y'])[0], 0)
        self.assertFalse(target.exists())
        self.assertEqual(read_receipts(self.comfy), [])
        self.assertEqual(self.install('--version', '2.0', '--dry-run')[0], 0)
        self.assertFalse(target.exists())
        self.assertEqual(self.install('--version', '2.0', answers=['n'])[0], 1)
        self.assertEqual(read_receipts(self.comfy), [])
        self.assertEqual(self.install('--version', '2.0', answers=['y'])[0], 0)
        records = read_receipts(self.comfy)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['version'], '2.0')
        self.assertEqual(records[0]['status'], 'installed')
        self.assertEqual(target.read_bytes(), self.workflow.read_bytes())
        self.assertEqual(list(target.parent.glob('*.json')), [target])
        entry = next(p for p in json.loads((self.repo / 'index.json').read_text())['packages']
                     if p['version'] == '2.0')
        self.assertEqual(records[0]['repository']['archive_sha256'], entry['sha256'])
        self.assertNotIn('UPDATE AVAILABLE', self.command('updates', '--comfy-dir', self.comfy)[1])

    def test_changed_workflow_update_preserves_user_edits_after_removal(self):
        self.ready()
        self.install('--yes')
        target = Path(read_receipts(self.comfy)[0]['workflow_path'])
        target.write_bytes(b'user edited workflow')
        self.workflow.write_text(json.dumps({'1': {'class_type': 'SaveImage',
                                                 'inputs': {'filename_prefix': 'changed'}}}))
        self.pack('2.0')
        self.publish()
        self.command('refresh')
        code, text = self.install('--version', '2.0', '--yes')
        self.assertEqual(code, 2, text)
        self.assertIn('renderrepo remove', text)
        self.assertEqual(read_receipts(self.comfy)[0]['version'], '1.0')
        code, text = self.cli('remove', 'demo', '--comfy-dir', self.comfy, answers=['y'])
        self.assertEqual(code, 0, text)
        self.assertIn('KEEP MODIFIED', text)
        self.assertEqual(read_receipts(self.comfy), [])
        code, text = self.install('--version', '2.0', '--yes')
        self.assertEqual(code, 2, text)
        self.assertIn('back up or move', text)
        self.assertEqual(target.read_bytes(), b'user edited workflow')
        self.assertEqual(list(target.parent.glob('*.json')), [target])
        self.assertEqual(read_receipts(self.comfy), [])
