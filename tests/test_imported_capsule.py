import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from renderrepo.archive import package_directory
from renderrepo.capsule import verify
from renderrepo.cli import load_package
from renderrepo.comfy_extension import PackageUI


class ImportedCapsuleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Comfy'; self.root.mkdir()
        self.ui = PackageUI(self.root); self.addCleanup(self.ui.temporary.cleanup)
        self.raw = b'{\r\n  "nodes": [{"id": 1, "type": "SaveImage", "widgets_values": ["original"]}], "links": []\r\n}\r\n'
        self.values = {'name': 'distinct-upstream-capsule', 'version': '0.1.0',
                       'repository': 'https://github.com/Comfy-Org/workflow_templates',
                       'revision': '67984637eefcd4825dcf5c134e39034785be1682',
                       'path': 'templates/image_z_image_turbo.json'}

    async def test_review_open_and_capsule_keep_original_bytes_without_install(self):
        review = await self.ui.preview_workflow(self.raw, 'distinct-staging.json')
        token = review['session']; session = self.ui.get(token)
        before = (session['package'] / 'capsule.json').read_bytes()
        self.assertEqual(self.ui.imported_original(token), self.raw)
        result = self.ui.save_imported_capsule(token, self.values)
        self.assertTrue(result['byte_identical'])
        self.assertEqual(result['original_json'].encode(), self.raw)
        self.assertEqual(result['source_sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result['workflow_sha256'], result['source_sha256'])
        self.assertEqual(result['capsule']['schema_version'], 1)
        self.assertEqual(result['capsule']['profile'], 'renderrepo')
        self.assertEqual(result['capsule']['predecessor'], json.loads(before)['sha256'])
        self.assertEqual(result['capsule']['provenance']['source_records'][0]['revision'], self.values['revision'])
        archive = self.ui.imported_capsule_archive(token)
        with package_directory(archive) as folder:
            m = load_package(folder); descriptor = verify(folder, m)
            self.assertEqual((folder / m['workflow']).read_bytes(), self.raw)
            self.assertEqual(descriptor, result['capsule'])
            self.assertEqual(m['name'], self.values['name'])
            self.assertFalse(descriptor['observations'])
        self.assertEqual((session['package'] / 'capsule.json').read_bytes(), before)
        self.assertFalse((self.root / 'user').exists())
        self.assertFalse((self.root / 'models').exists())
        self.assertFalse((self.root / '.renderrepo/receipts').exists())
        self.assertEqual({x['path'] for x in result['contents']}, {'workflow.json', 'manifest.yaml', 'capsule.json'})
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.ui.save_imported_capsule(token, self.values)

    async def test_original_tampering_and_invalid_source_are_rejected(self):
        r = await self.ui.preview_workflow(self.raw, 'staging.json'); token = r['session']
        for key, value in [('name', '../escape'), ('revision', 'main'), ('path', '../workflow.json'),
                           ('repository', 'https://user:secret@example.com/repo')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ui.save_imported_capsule(token, {**self.values, key: value})
        (self.ui.get(token)['directory'] / 'original-workflow.json').write_bytes(self.raw + b' ')
        with self.assertRaisesRegex(ValueError, 'Original workflow changed'):
            self.ui.imported_original(token)

    async def test_pinned_upstream_json_roundtrip_and_unknown_execution(self):
        raw = (Path(__file__).parent / 'fixtures/upstream_z_image_turbo_67984637.json').read_bytes()
        r = await self.ui.preview_workflow(raw, 'new-upstream-staging.json')
        self.assertEqual(len(r['capabilities']['nodes']), 10)
        self.assertNotIn('MarkdownNote', r['capabilities']['nodes'])
        saved = self.ui.save_imported_capsule(r['session'], self.values)
        self.assertEqual(saved['original_json'].encode(), raw)
        self.assertEqual(saved['capsule']['provenance']['source_records'][0]['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(saved['capsule']['observations'])
        self.assertNotIn('target_check', saved['capsule']['claims'])
        self.assertFalse((self.root / 'user').exists())
        self.assertFalse((self.root / 'models').exists())
