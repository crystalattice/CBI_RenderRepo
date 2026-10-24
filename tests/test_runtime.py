import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

from renderrepo.cli import main, load_package
from renderrepo.runtime import capture_runtime, git_provenance, graph_nodes


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = {}
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                cls.requests.append(self.path)
                body = json.dumps(cls.info).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.__class__.info = {'SaveImage': {}}
        self.requests.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        self.graph = {'1': {'class_type': 'SaveImage', 'inputs': {}}}
        self.manifest = {'schema_version': 0, 'name': 'runtime-test', 'workflow': 'workflow.json', 'artifacts': []}
        self.comfy = self.root / 'ComfyUI'
        self.comfy.mkdir()
        self.write()

    def write(self):
        (self.package / 'workflow.json').write_text(json.dumps(self.graph))
        (self.package / 'manifest.yaml').write_text(yaml.safe_dump(self.manifest))

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(map(str, args)))
        return code, out.getvalue()

    def doctor(self, *args):
        return self.cli('doctor', self.package, '--comfy-url', self.url, *args)

    def test_available_nodes(self):
        code, text = self.doctor()
        self.assertEqual(code, 0, text)
        self.assertIn('NODE FOUND: SaveImage', text)
        self.assertEqual(self.requests, ['/object_info'])

    def test_missing_node_then_supplied(self):
        self.graph['2'] = {'class_type': 'CustomNode', 'inputs': {}}
        self.write()
        self.assertEqual(self.doctor()[0], 1)
        self.info['CustomNode'] = {}
        self.assertEqual(self.doctor()[0], 0)

    def test_nested_used_subgraphs(self):
        self.graph = {'nodes': [{'id': 1, 'type': 'outer'}, {'id': 2, 'type': 'MarkdownNote'}],
                      'definitions': {'subgraphs': [
                          {'id': 'outer', 'nodes': [{'id': 3, 'type': 'inner'}]},
                          {'id': 'inner', 'nodes': [{'id': 4, 'type': 'SaveImage'}]},
                          {'id': 'unused', 'nodes': [{'id': 5, 'type': 'NotInstalled'}]}]}}
        self.write()
        code, text = self.doctor()
        self.assertEqual(code, 0, text)
        self.assertNotIn('NotInstalled', text)
        self.assertNotIn('MISSING NODE: outer', text)

    def model(self, choices):
        self.graph = {'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'tiny.safetensors'}}}
        self.info.clear()
        self.info['UNETLoader'] = {'input': {'required': {'unet_name': [choices, {}]}}}
        self.write()

    def test_model_visible(self):
        self.model(['tiny.safetensors'])
        self.assertEqual(self.doctor()[0], 0)

    def test_model_not_visible(self):
        self.model(['other.safetensors'])
        code, text = self.doctor()
        self.assertEqual(code, 1, text)
        self.assertIn('MODEL NOT VISIBLE', text)

    def test_unknown_model_combo(self):
        self.model('COMBO')
        self.assertEqual(self.doctor()[0], 3)

    def test_dynamic_model_unknown(self):
        self.model(['tiny.safetensors'])
        self.graph['1']['inputs']['unet_name'] = ['2', 0]
        self.write()
        self.assertEqual(self.doctor()[0], 3)

    def test_artifact_identity_unknown_without_roots(self):
        self.model(['tiny.safetensors'])
        self.manifest['artifacts'] = [{'filename': 'tiny.safetensors'}]
        self.write()
        self.assertEqual(self.doctor()[0], 3)

    def test_artifact_hash_mismatch(self):
        self.manifest['artifacts'] = [{'filename': 'tiny.safetensors', 'sha256': '0' * 64}]
        (self.comfy / 'models').mkdir()
        (self.comfy / 'models/tiny.safetensors').write_bytes(b'wrong')
        self.write()
        code, text = self.doctor('--comfy-dir', self.comfy)
        self.assertEqual(code, 1, text)
        self.assertIn('HASH MISMATCH', text)

    def test_user_input_required(self):
        self.manifest['inputs'] = [{'filename': 'input.png', 'kind': 'image', 'mode': 'user'}]
        (self.comfy / 'input').mkdir()
        self.write()
        self.assertEqual(self.doctor('--comfy-dir', self.comfy)[0], 1)
        (self.comfy / 'input/input.png').write_bytes(b'input')
        self.assertEqual(self.doctor('--comfy-dir', self.comfy)[0], 0)

    def test_remote_user_input_advertised(self):
        self.manifest['inputs'] = [{'filename': 'input.png', 'kind': 'image', 'mode': 'user'}]
        self.info['LoadImage'] = {'input': {'required': {'image': [['input.png'], {}]}}}
        self.write()
        self.assertEqual(self.doctor()[0], 0)

    def test_remote_user_input_unknown(self):
        self.manifest['inputs'] = [{'filename': 'input.png', 'kind': 'image', 'mode': 'user'}]
        self.write()
        self.assertEqual(self.doctor()[0], 3)

    def test_revision_match_and_mismatch(self):
        recorded = {'comfy': {'revision': 'abc', 'dirty': False}, 'custom_nodes': []}
        self.manifest['runtime'] = {'local_installation': recorded}
        self.write()
        with patch('renderrepo.runtime.capture_runtime', return_value=recorded):
            self.assertEqual(self.doctor('--comfy-dir', self.comfy)[0], 0)
        with patch('renderrepo.runtime.capture_runtime', return_value={'comfy': {'revision': 'other'}, 'custom_nodes': []}):
            self.assertEqual(self.doctor('--comfy-dir', self.comfy)[0], 1)

    def test_capture_dirty_is_not_reproducible(self):
        recorded = {'comfy': {'revision': 'abc', 'dirty': True}, 'custom_nodes': []}
        self.manifest['runtime'] = {'local_installation': recorded}
        self.write()
        with patch('renderrepo.runtime.capture_runtime', return_value={'comfy': {'revision': 'abc', 'dirty': False}, 'custom_nodes': []}):
            self.assertEqual(self.doctor('--comfy-dir', self.comfy)[0], 3)

    def test_revision_requires_local_root(self):
        self.manifest['runtime'] = {'local_installation': {'comfy': {'revision': 'abc'}, 'custom_nodes': []}}
        self.write()
        self.assertEqual(self.doctor()[0], 3)

    def test_custom_code_hash_mismatch(self):
        self.manifest['runtime'] = {'local_installation': {'comfy': {'revision': 'abc'},
            'custom_nodes': [{'name': 'node.py', 'sha256': 'a' * 64}]}}
        self.write()
        with patch('renderrepo.runtime.capture_runtime', return_value={'comfy': {'revision': 'abc', 'dirty': False}, 'custom_nodes': []}):
            self.assertEqual(self.doctor('--comfy-dir', self.comfy)[0], 1)

    def test_pack_captures_git_and_plain_file_provenance(self):
        def git(*args):
            subprocess.run(['git', '-C', str(self.comfy), *args], check=True, capture_output=True)
        git('init')
        (self.comfy / 'tracked.txt').write_text('runtime')
        git('add', 'tracked.txt')
        git('-c', 'user.name=RR Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'runtime')
        (self.comfy / 'custom_nodes').mkdir()
        (self.comfy / 'custom_nodes/plain.py').write_text('NODE_CLASS_MAPPINGS = {}')
        runtime = capture_runtime(self.comfy)
        self.assertEqual(len(runtime['comfy']['revision']), 40)
        self.assertTrue(runtime['comfy']['dirty'])
        self.assertEqual(len(runtime['custom_nodes'][0]['sha256']), 64)
        output = self.root / 'captured'
        code, text = self.cli('pack', self.package / 'workflow.json', '--output', output, '--comfy-dir', self.comfy)
        self.assertEqual(code, 0, text)
        self.assertIn('local_installation', load_package(output)['runtime'])

    def test_child_directory_does_not_claim_parent_git_revision(self):
        def result(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, str(self.root), '')
        with patch('subprocess.run', side_effect=result):
            self.assertIsNone(git_provenance(self.comfy)['revision'])

    def test_remote_credentials_not_persisted(self):
        def result(args, **kwargs):
            command = args[3:]
            value = str(self.comfy) if command[0] == 'rev-parse' and command[1] == '--show-toplevel' else (
                'abc' if command[0] == 'rev-parse' else '' if command[0] == 'status' else
                'https://user:secret@github.com/example/repo.git?token=private')
            return subprocess.CompletedProcess(args, 0, value, '')
        with patch('subprocess.run', side_effect=result):
            runtime = git_provenance(self.comfy)
        self.assertEqual(runtime['source'], 'https://github.com/example/repo.git')
        self.assertNotIn('secret', json.dumps(runtime))

    def test_bad_response(self):
        self.__class__.info = []
        self.assertEqual(self.doctor()[0], 2)

    def test_reject_credentials_in_comfy_url(self):
        code, text = self.cli('doctor', self.package, '--comfy-url', 'http://user:secret@example.org')
        self.assertEqual(code, 2)
        self.assertEqual(self.requests, [])


    def test_rr_state_does_not_dirty_runtime_code(self):
        def git(*args):
            subprocess.run(['git', '-C', str(self.comfy), *args], check=True, capture_output=True)
        git('init')
        (self.comfy / 'tracked.txt').write_text('runtime')
        git('add', 'tracked.txt')
        git('-c', 'user.name=RR Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'runtime')
        (self.comfy / '.renderrepo').mkdir()
        (self.comfy / '.renderrepo/receipt.json').write_text('{}')
        self.assertFalse(git_provenance(self.comfy)['dirty'])
        (self.comfy / 'tracked.txt').write_text('modified code')
        self.assertTrue(git_provenance(self.comfy)['dirty'])

    def test_invalid_runtime_provenance_rejected(self):
        self.manifest['runtime'] = {'local_installation': {'comfy': [], 'custom_nodes': []}}
        self.write()
        self.assertEqual(self.doctor()[0], 2)

    def test_invalid_node_type_error(self):
        self.graph = {'nodes': [{'id': 1, 'type': []}]}
        self.write()
        self.assertEqual(self.doctor()[0], 2)

    def test_invalid_capability_inputs_error(self):
        self.model(['tiny.safetensors'])
        self.info['UNETLoader']['input']['required'] = []
        self.assertEqual(self.doctor()[0], 2)

    def test_doctor_archive(self):
        archive = self.root / 'test.rrpkg'
        self.cli('pack', self.package / 'workflow.json', '--output', archive)
        self.assertEqual(self.cli('doctor', archive, '--comfy-url', self.url)[0], 0)
