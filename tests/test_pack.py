import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from renderrepo.cli import main, load_package
from renderrepo.pack import discover

FIXTURES = Path(__file__).parent / 'fixtures'


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workflow = self.root / 'source.json'
        self.output = self.root / 'package'
        self.models = self.root / 'existing'
        self.models.mkdir()
        self.comfy = self.root / 'ComfyUI'
        self.comfy.mkdir()
        self.graph = {'nodes': [
            {'id': 1, 'type': 'UNETLoader', 'widgets_values': ['model.safetensors', 'default'],
             'properties': {'cnr_id': 'comfy-core', 'ver': '1', 'models': [
                 {'name': 'model.safetensors', 'url': 'https://example.org/model', 'directory': 'diffusion_models'}]}},
            {'id': 2, 'type': 'LoadImage', 'widgets_values': ['sub/input.png', 'image']}], 'links': []}
        self.workflow.write_text(json.dumps(self.graph))

    def cli(self, *args, answers=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), patch('builtins.input', side_effect=answers):
            code = main(list(map(str, args)))
        return code, out.getvalue()

    def pack(self, *extra):
        return self.cli('pack', self.workflow, '--output', self.output,
                        '--artifact-dir', self.models, *extra)

    def install(self, *extra, answers=()):
        return self.cli('install', self.output, '--comfy-dir', self.comfy,
                        '--artifact-dir', self.models, *extra, answers=answers)

    def test_all_real_exports(self):
        expected = {'image_flux2.json': (4, 1), 'image_krea2_turbo_t2i.json': (4, 0),
                    'image_qwen_image_2_1_image_edit.json': (4, 2),
                    'video_minimax_h3_i2v.json': (5, 1),
                    'video_wan21_scail2_character_replacement.json': (7, 2)}
        for filename, counts in expected.items():
            with self.subTest(filename=filename):
                source = FIXTURES / filename
                output = self.root / filename.removesuffix('.json')
                code, text = self.cli('pack', source, '--output', output)
                self.assertEqual(code, 0, text)
                manifest = load_package(output)
                self.assertEqual((len(manifest['artifacts']), len(manifest['inputs'])), counts)
                self.assertEqual((output / 'workflow.json').read_bytes(), source.read_bytes())
                self.assertTrue(all(a['sources'] for a in manifest['artifacts']))
        qwen = load_package(self.root / 'image_qwen_image_2_1_image_edit')
        self.assertTrue(any('runtime package' in x for x in qwen['unresolved']))

    def test_wan_original_fixture(self):
        source = Path(__file__).parent.parent / 'examples/wan2.2-i2v/workflow.json'
        artifacts, inputs, runtime, unresolved = discover(json.loads(source.read_text()))
        self.assertEqual((len(artifacts), len(inputs)), (6, 1))

    def test_fingerprint_and_preserve_bytes(self):
        (self.models / 'model.safetensors').write_bytes(b'weights')
        self.assertEqual(self.pack()[0], 0)
        item = load_package(self.output)['artifacts'][0]
        self.assertEqual(item['sha256'], hashlib.sha256(b'weights').hexdigest())
        self.assertEqual(item['size_bytes'], 7)
        self.assertEqual((self.output / 'workflow.json').read_bytes(), self.workflow.read_bytes())

    def test_missing_identity_and_user_input_explicit(self):
        self.assertEqual(self.pack()[0], 0)
        m = load_package(self.output)
        self.assertNotIn('sha256', m['artifacts'][0])
        self.assertEqual(m['inputs'][0]['mode'], 'user')
        self.assertTrue(any('identity unavailable' in x for x in m['unresolved']))

    def test_no_overwrite_package(self):
        self.pack()
        self.assertEqual(self.pack()[0], 2)

    def test_bundle_input_and_install_end_to_end(self):
        (self.models / 'model.safetensors').write_bytes(b'weights')
        (self.models / 'sub').mkdir()
        (self.models / 'sub/input.png').write_bytes(b'example')
        self.assertEqual(self.pack('--bundle-input', 'sub/input.png')[0], 0)
        (self.models / 'sub/input.png').unlink()
        code, text = self.install('--yes')
        self.assertEqual(code, 0, text)
        self.assertEqual((self.comfy / 'input/sub/input.png').read_bytes(), b'example')
        model = self.comfy / 'models/diffusion_models/model.safetensors'
        self.assertTrue(model.is_symlink())
        self.assertEqual(model.resolve(), self.models / 'model.safetensors')
        self.assertEqual((self.comfy / 'user/default/workflows/source.json').read_bytes(), self.workflow.read_bytes())
        self.assertEqual(self.install('--yes')[0], 0)

    def test_shared_model_root_wired_to_comfy(self):
        (self.models / 'model.safetensors').write_bytes(b'weights')
        self.pack()
        shared = self.root / 'shared'
        code, text = self.install('--model-dir', shared, '--yes')
        self.assertEqual(code, 0, text)
        self.assertEqual((self.comfy / 'models/diffusion_models/model.safetensors').read_bytes(), b'weights')
        self.assertTrue((shared / 'diffusion_models/model.safetensors').is_symlink())

    def test_decline_makes_no_writes(self):
        (self.models / 'model.safetensors').write_bytes(b'weights')
        self.pack()
        code, text = self.install(answers=['n'])
        self.assertEqual(code, 1, text)
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_missing_source_makes_no_writes(self):
        self.graph['nodes'][0]['properties']['models'][0].pop('url')
        self.workflow.write_text(json.dumps(self.graph))
        self.pack()
        self.assertEqual(self.install('--yes')[0], 1)
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_manual_source_placement(self):
        self.pack()
        source = self.root / 'download.bin'
        source.write_bytes(b'weights')
        code, text = self.install('--source', f'model.safetensors={source}', '--yes')
        self.assertEqual(code, 0, text)
        self.assertEqual((self.comfy / 'models/diffusion_models/model.safetensors').read_bytes(), b'weights')

    def test_conflicting_workflow_refused(self):
        (self.models / 'model.safetensors').write_bytes(b'weights')
        self.pack()
        self.install('--yes')
        target = self.comfy / 'user/default/workflows/source.json'
        target.write_bytes(b'other')
        self.assertEqual(self.install('--yes')[0], 2)
        self.assertEqual(target.read_bytes(), b'other')

    def test_conflicting_model_paths_refused(self):
        n = json.loads(json.dumps(self.graph['nodes'][0]))
        n['type'] = 'VAELoader'
        n['properties']['models'][0]['directory'] = 'vae'
        self.graph['nodes'].append(n)
        self.workflow.write_text(json.dumps(self.graph))
        self.assertEqual(self.pack()[0], 2)
        self.assertFalse(self.output.exists())

    def test_stale_metadata_not_downloaded(self):
        self.graph['nodes'][0]['widgets_values'][0] = 'selected.safetensors'
        artifacts, _, _, issues = discover(self.graph)
        self.assertEqual([a['filename'] for a in artifacts], ['selected.safetensors'])
        self.assertEqual(artifacts[0]['sources'], [])
        self.assertTrue(any('stale' in x for x in issues))

    def test_unused_subgraphs_not_dependencies(self):
        self.graph['definitions'] = {'subgraphs': [{'id': 'unused', 'nodes': [
            {'type': 'UNETLoader', 'widgets_values': ['unused.safetensors']}]}]}
        self.assertEqual(len(discover(self.graph)[0]), 1)

    def test_subgraph_override_reported(self):
        self.graph = {'nodes': [{'id': 1, 'type': 'sub', 'widgets_values': {'unet_name': 'override.safetensors'}}],
                      'definitions': {'subgraphs': [{'id': 'sub', 'nodes': self.graph['nodes']}]}}
        self.assertTrue(any('override' in x for x in discover(self.graph)[3]))

    def test_named_subgraph_loader_selection_uses_explicit_link(self):
        loader = self.graph['nodes'][0]
        loader['inputs'] = [{'name': 'unet_name', 'widget': {'name': 'unet_name'}, 'link': 73}]
        graph = {'nodes': [
            {'id': 57, 'type': 'sub', 'widgets_values': ['selected.safetensors'],
             'widgets_values_named': {'unet_name': 'selected.safetensors'}},
            {'id': 58, 'type': 'sub', 'widgets_values_named': {'unet_name': 'other.safetensors'}}],
            'definitions': {'subgraphs': [{'id': 'sub', 'inputNode': {'id': -10},
                'inputs': [{'name': 'unet_name', 'linkIds': [73]}], 'nodes': [loader],
                'links': [{'id': 73, 'origin_id': -10, 'origin_slot': 0,
                           'target_id': 1, 'target_slot': 0}]}]}}
        original = json.dumps(graph, sort_keys=True)
        artifacts, _, _, issues = discover(graph)
        self.assertEqual([a['filename'] for a in artifacts], ['selected.safetensors', 'other.safetensors'])
        self.assertTrue(all(a['sources'] == [] for a in artifacts))
        self.assertTrue(any('stale model metadata' in x for x in issues))
        self.assertEqual(json.dumps(graph, sort_keys=True), original)
        from renderrepo.readiness import inventory
        models = inventory({'artifacts': artifacts}, graph)['models']
        self.assertEqual([m['filename'] for m in models], ['selected.safetensors', 'other.safetensors'])
        graph['nodes'][0]['widgets_values_named']['unet_name'] = '../escape.safetensors'
        with self.assertRaisesRegex(ValueError, 'unsafe relative path'):
            discover(graph)

    def test_subgraph_override_requires_matching_link_endpoint(self):
        self.graph['nodes'][0]['inputs'] = [
            {'name': 'unet_name', 'widget': {'name': 'unet_name'}, 'link': 73}]
        graph = {'nodes': [{'id': 57, 'type': 'sub',
                           'widgets_values_named': {'unet_name': 'selected.safetensors'}}],
                 'definitions': {'subgraphs': [{'id': 'sub', 'inputNode': {'id': -10},
                     'inputs': [{'name': 'unet_name', 'linkIds': [73]}],
                     'nodes': self.graph['nodes'],
                     'links': [{'id': 73, 'origin_id': 999, 'origin_slot': 0,
                                'target_id': 1, 'target_slot': 0}]}]}}
        artifacts, _, _, issues = discover(graph)
        self.assertEqual(artifacts[0]['filename'], 'model.safetensors')
        self.assertTrue(any('override' in x for x in issues))

    def test_api_graph_supported(self):
        self.workflow.write_text(json.dumps({'1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'api.safetensors'}}}))
        self.assertEqual(self.pack()[0], 0)
        self.assertEqual(load_package(self.output)['artifacts'][0]['filename'], 'api.safetensors')

    def test_path_traversal_rejected(self):
        self.graph['nodes'][0]['widgets_values'][0] = '../escape.safetensors'
        self.workflow.write_text(json.dumps(self.graph))
        self.assertEqual(self.pack()[0], 2)

    def test_target_traversal_rejected(self):
        self.pack()
        m = load_package(self.output)
        m['artifacts'][0]['target'] = 'models/../model.safetensors'
        (self.output / 'manifest.yaml').write_text(yaml.safe_dump(m))
        self.assertEqual(self.install('--yes')[0], 2)

    def test_invalid_graph_no_output(self):
        self.workflow.write_text('{"hello": 1}')
        self.assertEqual(self.pack()[0], 2)
        self.assertFalse(self.output.exists())

    def test_unknown_bundle_refused(self):
        self.assertEqual(self.pack('--bundle-input', 'unknown.png')[0], 2)
