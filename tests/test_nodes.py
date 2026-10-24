import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

from renderrepo.cli import main
from renderrepo.nodes import validate_nodes
from renderrepo.receipts import read_receipts


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'origin'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        (self.source / '__init__.py').write_text('NODE_CLASS_MAPPINGS = {}\n')
        (self.source / '.gitignore').write_text('__pycache__/\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.sha = self.git('rev-parse', 'HEAD')
        self.comfy = self.root / 'ComfyUI'
        self.comfy.mkdir()
        self.package = self.root / 'package'
        self.package.mkdir()
        (self.package / 'workflow.json').write_text('{"1":{"class_type":"Test","inputs":{}}}')
        self.manifest = {'schema_version': 0, 'name': 'demo', 'version': '1.0', 'workflow': 'workflow.json',
                         'artifacts': [], 'runtime': {'custom_nodes': [self.node()]}}
        self.write()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True).strip()

    def node(self):
        return dict(name='test-node', source=str(self.source), revision=self.sha)

    def write(self):
        (self.package / 'manifest.yaml').write_text(yaml.safe_dump(self.manifest))

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(map(str, args)))
        return code, out.getvalue()

    def install(self, *args):
        return self.cli('install', self.package, '--comfy-dir', self.comfy, '--install-nodes', *args)

    def test_decline_no_git_or_writes(self):
        with patch('builtins.input', return_value='n'), patch('renderrepo.nodes.git') as git:
            code, _ = self.install()
        self.assertEqual(code, 1)
        git.assert_not_called()
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_requires_explicit_code_option(self):
        code, _ = self.cli('install', self.package, '--comfy-dir', self.comfy, '--yes')
        self.assertEqual(code, 2)
        self.assertEqual(list(self.comfy.iterdir()), [])

    def test_exact_commit_receipt_repeat_and_remove(self):
        (self.source / '__init__.py').write_text('later version\n')
        self.git('commit', '-qam', 'newer')
        code, out = self.install('--yes')
        self.assertEqual(code, 0, out)
        target = self.comfy / 'custom_nodes/test-node'
        self.assertEqual(subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip(),self.sha)
        receipt = read_receipts(self.comfy)[0]
        self.assertEqual(receipt['custom_nodes'][0]['revision'], self.sha)
        self.assertTrue(any(i['kind']=='git' for i in receipt['managed']))
        self.assertEqual(self.install('--yes')[0], 0)
        self.assertEqual(self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')[0],0)
        self.assertFalse(target.exists())
        self.assertTrue(self.source.exists())

    def test_modified_preserved_on_install_and_remove(self):
        self.assertEqual(self.install('--yes')[0],0)
        target = self.comfy / 'custom_nodes/test-node'
        (target / '__init__.py').write_text('changed\n')
        self.assertEqual(self.install('--yes')[0],2)
        code,out=self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')
        self.assertEqual(code,0)
        self.assertIn('KEEP MODIFIED',out)
        self.assertTrue(target.exists())

    def test_preexisting_is_not_owned(self):
        target=self.comfy/'custom_nodes/test-node'
        target.parent.mkdir()
        subprocess.run(['git','clone','-q',str(self.source),str(target)],check=True)
        self.assertEqual(self.install('--yes')[0],0)
        self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')
        self.assertTrue(target.exists())

    def test_shared_checkout_ownership(self):
        self.assertEqual(self.install('--yes')[0],0)
        self.manifest['name']='second'; self.write()
        self.assertEqual(self.install('--yes')[0],0)
        self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')
        self.assertTrue((self.comfy/'custom_nodes/test-node').exists())
        self.cli('remove','second','--comfy-dir',self.comfy,'--yes')
        self.assertFalse((self.comfy/'custom_nodes/test-node').exists())

    def add_requirements(self):
        (self.source/'requirements.txt').write_text('# no external packages\n')
        self.git('add','.');self.git('commit','-qm','requirements')
        self.sha=self.git('rev-parse','HEAD')
        self.manifest['runtime']['custom_nodes']=[self.node()];self.write()

    def test_requirements_without_python_is_partial(self):
        self.add_requirements()
        code,out=self.install('--yes')
        self.assertEqual(code,3,out)
        self.assertEqual(read_receipts(self.comfy)[0]['status'],'partial')
        self.assertEqual(read_receipts(self.comfy)[0]['custom_nodes'][0]['dependencies'],'unverified')

    def test_explicit_python_installs_empty_requirements(self):
        self.add_requirements()
        real_run = subprocess.run
        def run(command, **kwargs):
            if command[:3] == [sys.executable, '-m', 'pip']:
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, **kwargs)
        with patch('renderrepo.nodes.subprocess.run', side_effect=run) as mocked:
            code,out=self.install('--yes','--comfy-python',sys.executable)
        self.assertEqual(code,0,out)
        target = self.comfy / 'custom_nodes/test-node'
        mocked.assert_any_call([sys.executable, '-m', 'pip', 'install', '-r', str(target / 'requirements.txt')],
                               cwd=target, timeout=60, check=False)
        self.assertEqual(read_receipts(self.comfy)[0]['custom_nodes'][0]['dependencies'],'installed')

    def test_pip_failure_keeps_partial_receipt(self):
        self.add_requirements()
        real_run = subprocess.run
        def run(command, **kwargs):
            if command[:3] == [sys.executable, '-m', 'pip']:
                return subprocess.CompletedProcess(command, 1, stderr='No module named pip')
            return real_run(command, **kwargs)
        with patch('renderrepo.nodes.subprocess.run', side_effect=run):
            code,out=self.install('--yes','--comfy-python',sys.executable)
        self.assertEqual(code,2,out)
        self.assertEqual(read_receipts(self.comfy)[0]['status'],'partial')
        self.assertEqual(read_receipts(self.comfy)[0]['custom_nodes'][0]['dependencies'],'pending')
        self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')
        self.assertFalse((self.comfy/'custom_nodes/test-node').exists())

    def test_unknown_commit_no_final_checkout(self):
        self.manifest['runtime']['custom_nodes'][0]['revision']='1'*40;self.write()
        self.assertEqual(self.install('--yes')[0],2)
        self.assertFalse((self.comfy/'custom_nodes/test-node').exists())

    def test_pack_selects_node_and_fingerprints_commit(self):
        target=self.comfy/'custom_nodes/test-node';target.parent.mkdir()
        subprocess.run(['git','clone','-q',str(self.source),str(target)],check=True)
        output=self.root/'packed'
        code,out=self.cli('pack',self.package/'workflow.json','--output',output,'--comfy-dir',self.comfy,
                          '--custom-node','test-node','--node-source',f'test-node={self.source}')
        self.assertEqual(code,0,out)
        m=yaml.safe_load((output/'manifest.yaml').read_text())
        self.assertEqual(m['runtime']['custom_nodes'],[self.node()])
        (target/'__init__.py').write_text('dirty')
        self.assertEqual(self.cli('pack',self.package/'workflow.json','--output',self.root/'dirty',
                          '--comfy-dir',self.comfy,'--custom-node','test-node')[0],2)

    def test_invalid_declarations(self):
        for change in ({'name':'../bad'},{'revision':'main'},{'source':'ext::bad'},
                       {'source':'https://user:secret@example.com/repo'},{'source':'relative'},{'revision':123}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_nodes([dict(self.node(),**change)])

    def test_cache_files_do_not_prevent_removal(self):
        self.assertEqual(self.install('--yes')[0],0)
        target=self.comfy/'custom_nodes/test-node'
        (target/'__pycache__').mkdir();(target/'__pycache__/foo.pyc').write_bytes(b'cache')
        self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')
        self.assertFalse(target.exists())

    def test_submodules_not_implicitly_installed(self):
        (self.source/'.gitmodules').write_text('[submodule "x"]\n')
        self.git('add','.');self.git('commit','-qm','submodule')
        self.manifest['runtime']['custom_nodes'][0]['revision']=self.git('rev-parse','HEAD');self.write()
        self.assertEqual(self.install('--yes')[0],2)
        self.assertFalse((self.comfy/'custom_nodes/test-node').exists())

    def test_modified_mode_preserved(self):
        self.assertEqual(self.install('--yes')[0],0)
        target=self.comfy/'custom_nodes/test-node'
        (target/'__init__.py').chmod(0o755)
        self.cli('remove','demo','--comfy-dir',self.comfy,'--yes')
        self.assertTrue(target.exists())

    def test_symlink_destination_preserved(self):
        folder=self.comfy/'custom_nodes';folder.mkdir()
        (folder/'test-node').symlink_to(self.source,target_is_directory=True)
        code,out=self.install('--yes')
        self.assertEqual(code,2,out)
        self.assertTrue((folder/'test-node').is_symlink())

    def test_python_path_checked_before_writes(self):
        code,out=self.install('--yes','--comfy-python',self.root/'missing-python')
        self.assertEqual(code,2,out)
        self.assertEqual(list(self.comfy.iterdir()),[])
