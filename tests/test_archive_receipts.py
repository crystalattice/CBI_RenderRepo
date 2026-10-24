import contextlib
import io
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml

from renderrepo.cli import main
from renderrepo.receipts import read_receipts


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.author = self.root / 'author'
        self.author.mkdir()
        self.comfy = self.root / 'receiver/ComfyUI'
        self.comfy.mkdir(parents=True)
        self.models = self.root / 'shared'
        self.models.mkdir()
        (self.models / 'tiny.safetensors').write_bytes(b'weights')
        (self.author / 'example.png').write_bytes(b'example')
        self.workflow = self.author / 'workflow.json'
        self.workflow.write_text(json.dumps({'nodes': [
            {'id': 1, 'type': 'UNETLoader', 'widgets_values': ['tiny.safetensors'],
             'properties': {'models': [{'name': 'tiny.safetensors', 'directory': 'diffusion_models',
                                        'url': 'https://example.org/tiny'}]}},
            {'id': 2, 'type': 'LoadImage', 'widgets_values': ['example.png', 'image']}], 'links': []}))
        self.archive = self.root / 'demo.rrpkg'

    def cli(self, *args, answers=()):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream), patch('builtins.input', side_effect=answers):
            code = main(list(map(str, args)))
        return code, stream.getvalue()

    def pack(self, *extra):
        return self.cli('pack', self.workflow, '--output', self.archive, '--name', 'demo',
                        '--artifact-dir', self.models, '--artifact-dir', self.author,
                        '--bundle-input', 'example.png', *extra)

    def install(self, *extra, answers=()):
        return self.cli('install', self.archive, '--comfy-dir', self.comfy,
                        '--artifact-dir', self.models, *extra, answers=answers)

    def remove(self, *extra, answers=()):
        return self.cli('remove', 'demo', '--comfy-dir', self.comfy, *extra, answers=answers)

    def test_portable_handoff_removal(self):
        original = self.workflow.read_bytes()
        self.assertEqual(self.pack()[0], 0)
        with zipfile.ZipFile(self.archive) as z:
            self.assertEqual(set(z.namelist()), {'workflow.json', 'manifest.yaml', 'capsule.json', 'assets/example.png'})
            self.assertEqual(z.read('workflow.json'), original)
        shutil.rmtree(self.author)
        code, text = self.install('--yes')
        self.assertEqual(code, 0, text)
        receipt = read_receipts(self.comfy)[0]
        self.assertEqual(receipt['version'], '0.1.0')
        self.assertEqual(receipt['status'], 'installed')
        self.assertEqual(len(receipt['artifacts']), 2)
        self.assertTrue(all(len(a['sha256']) == 64 for a in receipt['artifacts']))
        self.assertNotIn('renderrepo-package-', json.dumps(receipt))
        self.assertEqual(self.cli('list', '--comfy-dir', self.comfy)[0], 0)
        self.assertEqual(self.remove('--yes')[0], 0)
        self.assertEqual((self.models / 'tiny.safetensors').read_bytes(), b'weights')
        self.assertFalse((self.comfy / 'models/diffusion_models/tiny.safetensors').is_symlink())
        self.assertFalse((self.comfy / 'input/example.png').exists())
        self.assertFalse((self.comfy / 'user/default/workflows/demo.json').exists())
        self.assertEqual(read_receipts(self.comfy), [])

    def test_inspect_archive_read_only(self):
        self.pack()
        code, text = self.cli('inspect', self.archive, '--artifact-dir', self.models)
        self.assertEqual(code, 1, text)  # bundled input is not in configured machine roots yet
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_declined_install_no_receipt(self):
        self.pack()
        self.assertEqual(self.install(answers=['n'])[0], 1)
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_declined_remove_no_changes(self):
        self.pack()
        self.install('--yes')
        self.assertEqual(self.remove(answers=['n'])[0], 1)
        self.assertEqual(len(read_receipts(self.comfy)), 1)
        self.assertTrue((self.comfy / 'input/example.png').exists())

    def test_modified_workflow_and_input_preserved(self):
        self.pack()
        self.install('--yes')
        workflow = self.comfy / 'user/default/workflows/demo.json'
        image = self.comfy / 'input/example.png'
        workflow.write_bytes(b'user workflow')
        image.write_bytes(b'user image')
        self.assertEqual(self.remove('--yes')[0], 0)
        self.assertEqual(workflow.read_bytes(), b'user workflow')
        self.assertEqual(image.read_bytes(), b'user image')

    def test_modified_link_preserved(self):
        self.pack()
        self.install('--yes')
        link = self.comfy / 'models/diffusion_models/tiny.safetensors'
        link.unlink()
        link.symlink_to(self.models / 'another.safetensors')
        self.remove('--yes')
        self.assertTrue(link.is_symlink())

    def test_preexisting_workflow_not_owned(self):
        self.pack()
        target = self.comfy / 'user/default/workflows/demo.json'
        target.parent.mkdir(parents=True)
        target.write_bytes(self.workflow.read_bytes())
        self.install('--yes')
        self.remove('--yes')
        self.assertTrue(target.exists())

    def test_reinstall_retains_ownership(self):
        self.pack()
        self.install('--yes')
        self.assertEqual(self.install('--yes')[0], 0)
        self.remove('--yes')
        self.assertFalse((self.comfy / 'user/default/workflows/demo.json').exists())

    def test_two_packages_share_links_and_input(self):
        self.pack()
        self.install('--yes')
        second = self.root / 'second.rrpkg'
        code, text = self.cli('pack', self.workflow, '--output', second, '--name', 'second',
                              '--artifact-dir', self.author, '--bundle-input', 'example.png')
        self.assertEqual(code, 0, text)
        code, text = self.cli('install', second, '--comfy-dir', self.comfy, '--yes')
        self.assertEqual(code, 0, text)
        self.remove('--yes')
        self.assertTrue((self.comfy / 'models/diffusion_models/tiny.safetensors').is_symlink())
        self.assertTrue((self.comfy / 'input/example.png').exists())
        self.assertEqual(self.cli('remove', 'second', '--comfy-dir', self.comfy, '--yes')[0], 0)
        self.assertFalse((self.comfy / 'models/diffusion_models/tiny.safetensors').is_symlink())
        self.assertFalse((self.comfy / 'input/example.png').exists())
        self.assertTrue((self.models / 'tiny.safetensors').exists())

    def test_different_registered_version_refused(self):
        self.pack()
        self.install('--yes')
        self.archive.unlink()
        self.pack('--version', '0.2.0')
        self.assertEqual(self.install('--yes')[0], 2)
        self.assertEqual(read_receipts(self.comfy)[0]['version'], '0.1.0')

    def test_selected_external_weights_retained_without_copy(self):
        self.pack()
        source = self.models / 'tiny.safetensors'
        separate = self.root / 'source.bin'
        shutil.move(source, separate)
        self.assertEqual(self.install('--source', f'tiny.safetensors={separate}', '--yes')[0], 0)
        target = self.comfy / 'models/diffusion_models/tiny.safetensors'
        self.remove('--yes')
        self.assertFalse(target.exists())
        self.assertEqual(separate.read_bytes(), b'weights')

    def test_partial_install_recorded_and_removable(self):
        self.pack()
        with patch('renderrepo.comfy.install_artifacts', return_value=1):
            self.assertEqual(self.install('--yes')[0], 1)
        self.assertEqual(read_receipts(self.comfy)[0]['status'], 'partial')
        self.assertEqual(self.remove('--yes')[0], 0)

    def test_missing_package_remove_error(self):
        self.assertEqual(self.remove('--yes')[0], 2)

    def test_empty_list(self):
        code, text = self.cli('list', '--comfy-dir', self.comfy)
        self.assertEqual(code, 0)
        self.assertIn('No RenderRepo', text)

    def test_archive_no_overwrite(self):
        self.pack()
        original = self.archive.read_bytes()
        self.assertEqual(self.pack()[0], 2)
        self.assertEqual(self.archive.read_bytes(), original)

    def malicious(self, members):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name, value in members:
                archive.writestr(name, value)
        code, text = self.cli('inspect', self.archive)
        self.assertEqual(code, 2, text)
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_archive_traversal(self):
        self.malicious([('../escape', b'bad'), ('manifest.yaml', b'{}')])
        self.assertFalse((self.root / 'escape').exists())

    def test_archive_absolute_path(self):
        self.malicious([('/absolute', b'bad')])

    def test_archive_windows_path(self):
        self.malicious([('C:/escape', b'bad')])

    def test_archive_duplicate_members(self):
        self.malicious([('manifest.yaml', b'{}'), ('MANIFEST.YAML', b'{}')])

    def test_archive_symlink_rejected(self):
        info = zipfile.ZipInfo('assets/link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.malicious([(info, b'/etc/passwd')])

    def test_archive_limits(self):
        with patch('renderrepo.archive.MAX_BYTES', 1):
            self.malicious([('manifest.yaml', b'{}')])

    def test_corrupt_archive(self):
        self.archive.write_bytes(b'not a zip')
        self.assertEqual(self.cli('inspect', self.archive)[0], 2)

    def test_invalid_version(self):
        self.assertEqual(self.pack('--version', '')[0], 2)
        self.assertFalse(self.archive.exists())
