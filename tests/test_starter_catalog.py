"""Starter authoring/build contract; actual GUI/pixel evidence is separate."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import yaml
from scripts.build_starter_catalog import ASSETS, PACKAGES, build
from renderrepo.discovery import ranked
from renderrepo.repository import validate_index


class StarterCatalogTests(unittest.TestCase):
    def test_portable_build_fingerprints_search_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'starter catalog with spaces'
            index = build(root)
            data = json.loads(index.read_text())
            packages = validate_index(data, 'http://127.0.0.1:8198/catalog%20with%20spaces/index.json')
            self.assertEqual({p['name'] for p in packages}, set(PACKAGES))
            for item in data['packages']:
                self.assertEqual(item['version'], '0.1.0')
                self.assertNotIn('/', item['uri'])
                archive = root / item['uri']
                self.assertEqual(item['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
                self.assertEqual(item['size_bytes'], archive.stat().st_size)
                with zipfile.ZipFile(archive) as package:
                    manifest = yaml.safe_load(package.read('manifest.yaml'))
                    graph = json.loads(package.read('workflow.json'))
                    self.assertEqual(manifest['runtime']['compatibility'], 'unverified')
                    self.assertEqual(manifest['unresolved'], [])
                    self.assertEqual(manifest['runtime']['node_packages'], [{'id': 'comfy-core', 'recorded_version': '52f98af2e2e42c421070a3e147c161c47cdeaf22'}])
                    expected = ['rr-starter-base.png'] + (['rr-starter-overlay.png'] if item['name'].endswith('overlay') else [])
                    self.assertEqual([i['filename'] for i in manifest['inputs']], expected)
                    self.assertTrue(all(i['mode'] == 'bundled' for i in manifest['inputs']))
                    for artifact in manifest['artifacts']:
                        self.assertEqual(artifact['target'], 'input/' + artifact['filename'])
                        payload = package.read('assets/' + artifact['filename'])
                        self.assertEqual(hashlib.sha256(payload).hexdigest(), artifact['sha256'])
                        self.assertEqual(payload, (ASSETS / artifact['filename']).read_bytes())
                    self.assertEqual(len(package.namelist()), len(expected) + 4)
                    example = next(n for n in package.namelist() if n.startswith('examples/'))
                    self.assertEqual(package.read(example), (ASSETS / 'outputs' / Path(example).name).read_bytes())
                    prefix = next(n['widgets_values'][0] for n in graph['nodes'] if n['type'] == 'SaveImage')
                    self.assertTrue(prefix.startswith('rr-starter/' + item['name'].removeprefix('rr-starter-') + '/'))
            for query, suffix in [('resize photo', 'resize'), ('make image smaller', 'resize'),
                                  ('crop portrait', 'portrait-crop'), ('crop to aspect ratio', 'portrait-crop'),
                                  ('combine pictures', 'overlay'), ('overlay images', 'overlay')]:
                results = ranked(data['packages'], query)
                self.assertEqual(results[0]['name'], 'rr-starter-' + suffix, query)
                if suffix == 'overlay':
                    self.assertEqual(len(results), 1, query)
            self.assertEqual(ranked(data['packages'], 'restore photo'), [])
            note = root / 'keep.txt'; note.write_text('preserve')
            before = index.read_bytes()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                build(root)
            self.assertEqual(note.read_text(), 'preserve')
            self.assertEqual(index.read_bytes(), before)
            link = Path(temporary) / 'link'; link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                build(link)
