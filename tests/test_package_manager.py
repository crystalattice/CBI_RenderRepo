"""Real CLI/bridge fixtures; no user Comfy, models, code installers or providers."""
import asyncio
import contextlib
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import yaml
from renderrepo.cli import main
from renderrepo.comfy_extension import PackageUI
from renderrepo.discovery import ranked, tokens
from renderrepo.package_link import retrieve_link
from renderrepo.setup import choose, setup_comfy


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo = self.root/'catalog'; self.repo.mkdir()
        self.comfy = self.root/'Comfy with spaces'; self.comfy.mkdir()
        self.requests = []; requests = self.requests
        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                return super().do_GET()
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(self.repo)))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.settings = ['--sources',str(self.root/'sources.yaml'),'--cache-dir',str(self.root/'cache')]

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def cli(self, *args, answer='n'):
        out=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out),patch('builtins.input',return_value=answer) as approval:
            result=main(list(map(str,args)))
        return result,out.getvalue(),approval.call_count

    def package(self,name='repair',discovery=None):
        archive=self.repo/(name+'.rrpkg')
        manifest={'schema_version':0,'name':name,'version':'1.0','workflow':'workflow.json','artifacts':[]}
        if discovery is not None:manifest['discovery']=discovery
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('manifest.yaml',yaml.safe_dump(manifest));z.writestr('workflow.json',json.dumps({'nodes':[{'id':1,'type':'SaveImage'}]}))
        return archive

    def test_synonyms_specificity_and_incidental_description(self):
        packages=[{'name':'restore','discovery':{'tasks':['Restore old photographs','Repair scratched photos'],'required_inputs':['A damaged photo']}},
                  {'name':'touchup','discovery':{'tasks':['Photo touch up']}},
                  {'name':'video','description':'Repair a video; includes a photo restoration example'},
                  {'name':'background','discovery':{'tasks':['Remove photo background']}},
                  {'name':'legacy','description':'Photo repair workflow'}]
        queries=['fix photo','photo touch up','repair photograph','photo restoration']
        results=[[p['name'] for p in ranked(packages,q)] for q in queries]
        self.assertTrue(all(r==results[0] for r in results));self.assertEqual(set(results[0][:2]),{'restore','touchup'})
        self.assertEqual([p['name'] for p in ranked(packages,'repair scratched photograph')],['restore'])
        self.assertEqual(ranked(packages,'remove photo background')[0]['name'],'background')
        self.assertEqual(tokens('PHOTO touch-up!'),tokens('Repair photograph'))
        self.assertIn('legacy',results[0])

    def test_metadata_export_index_and_cli_search(self):
        workflow=self.root/'workflow.json';workflow.write_text(json.dumps({'nodes':[{'id':1,'type':'SaveImage'}]}))
        metadata={'title':'Rescue an old photograph','summary':'Repair family pictures','tasks':['Photo restoration'], 'tags':['old photos'], 'required_inputs':['A photograph'], 'examples':['Family scans'], 'limitations':['Publisher example, no quality guarantee']}
        meta=self.root/'metadata.json';meta.write_text(json.dumps(metadata))
        archive=self.repo/'packed.rrpkg'
        self.assertEqual(self.cli('pack',workflow,'--output',archive,'--name','rescue','--discovery',meta)[0],0)
        self.assertEqual(self.cli('index',self.repo)[0],0)
        self.assertEqual(json.loads((self.repo/'index.json').read_text())['packages'][0]['discovery'],metadata)
        self.cli('source','add','personal',self.repo,*self.settings);self.cli('refresh',*self.settings)
        result,out,_=self.cli('search','fix photo',*self.settings)
        self.assertEqual(result,0);self.assertIn('Rescue an old photograph',out);self.assertIn('Publisher input requirements',out)
        self.assertEqual(self.cli('source','official',*self.settings)[0],0)
        self.assertEqual(self.cli('search','fix photo',*self.settings)[0],0)
        self.cli('source','add','renderrepo-official',self.repo,*self.settings)
        before=(self.root/'sources.yaml').read_bytes()
        self.assertEqual(self.cli('source','official',str(self.repo),*self.settings)[0],2)
        self.assertEqual((self.root/'sources.yaml').read_bytes(),before)

    def test_named_file_and_url_prepare_same_plan_with_one_install_consent(self):
        archive=self.package();self.cli('index',self.repo)
        self.cli('source','add','local',self.repo,*self.settings);self.cli('refresh',*self.settings)
        for target in ['repair',archive,self.url+'/repair.rrpkg']:
            result,out,questions=self.cli('install',target,'--comfy-dir',self.comfy,*self.settings)
            self.assertEqual(result,1,out);self.assertEqual(questions,1,out)
            self.assertIn('WORKFLOW:',out)
            self.assertFalse((self.comfy/'user').exists())
        self.assertIn('/repair.rrpkg',self.requests)
        with patch('renderrepo.setup.choose_comfy',return_value=self.comfy):
            result,out,_=self.cli('install','repair',*self.settings,'--yes')
        self.assertEqual(result,0,out)

    def test_direct_pages_and_size_rejected_without_installation(self):
        self.package();(self.repo/'page.rrpkg').write_text('<html>Login</html>')
        result,out,_=self.cli('install',self.url+'/page.rrpkg','--comfy-dir',self.comfy,*self.settings)
        self.assertEqual(result,2);self.assertNotIn('INSTALLED WORKFLOW',out)
        for url in ['https://github.com/owner/repo/blob/main/a.rrpkg','https://mega.nz/file/a']:
            with self.assertRaisesRegex(ValueError,'share/landing'):
                retrieve_link(url,self.root/'cache')
        with patch('renderrepo.package_link.MAX_PACKAGE_BYTES',10):
            with self.assertRaisesRegex(ValueError,'inspection limit'):
                retrieve_link(self.url+'/repair.rrpkg',self.root/'cache')
        self.assertFalse((self.comfy/'user').exists())

    def test_catalog_add_immediate_discovery_official_unconfigured_conflicts(self):
        self.package(discovery={'tasks':['repair photo']});self.cli('index',self.repo)
        ui=PackageUI(self.comfy);self.addCleanup(ui.temporary.cleanup)
        self.assertIsNone(ui.repositories()['official_catalog'])
        data=ui.repository_source({'action':'add','uri':self.url+'/index.json'})
        self.assertEqual(len(data['packages']),1);self.assertFalse(data['sources'][0]['stale'])
        other=ui.repository_source({'action':'add','uri':str(self.repo),'name':'Other catalog'})
        self.assertEqual(len(other['packages']),2)
        self.assertEqual(other['sources'][1]['display_name'],'Other catalog')
        self.assertEqual(len({p['repository'] for p in other['packages']}),2)
        self.assertFalse(ui.history());self.assertFalse((self.comfy/'user').exists())
        args=ui.repository_args(package=Path('repair'),version=None,yes=True)
        from renderrepo.repository import select_package
        with self.assertRaisesRegex(ValueError,'multiple repositories'):select_package(args)

    def test_bridge_named_and_link_review_are_durable_and_unapproved(self):
        self.package();self.cli('index',self.repo)
        async def run():
            ui=PackageUI(self.comfy)
            try:
                data=ui.repository_source({'action':'add','uri':str(self.repo)})
                item=data['packages'][0]
                for values in [None,{'name':item['name'],'version':item['version'],'source':item['repository'],'sha256':item['sha256']}]:
                    op=await ui.retrieve_link({'url':self.url+'/repair.rrpkg'}) if values is None else await ui.repository_retrieve(values)
                    await ui.repository_operations[op['token']]['task']
                    state=ui.repository_operation(op['token']);self.assertEqual(state['state'],'done',state)
                    review=state['result'];self.assertTrue(review['can_install']);self.assertEqual(review['name'],'repair')
                    self.assertIsNone(ui.get(review['session']).get('approved_scope'))
                self.assertFalse((self.comfy/'user').exists())
                reopened=PackageUI(self.comfy)
                try:self.assertEqual(len(reopened.history()),2)
                finally:reopened.temporary.cleanup()
            finally:ui.temporary.cleanup()
        asyncio.run(run())

    def test_imported_same_name_is_not_claimed_by_unrelated_catalog(self):
        archive=self.package();self.cli('index',self.repo)
        async def run():
            ui=PackageUI(self.comfy)
            try:
                review=await ui.preview(archive.read_bytes(),{})
                await ui.approve(review['session'],True)
                await ui.get(review['session'])['task']
                self.assertEqual(ui.status(review['session'])['outcome'],'completed')
                data=ui.repository_source({'action':'add','uri':str(self.repo)})
                self.assertFalse(data['packages'][0]['installed_identity'])
                self.assertEqual(data['installed'][0]['name'],'repair')
            finally:ui.temporary.cleanup()
        asyncio.run(run())


class SetupTests(unittest.TestCase):
    def test_setup_spaces_check_rerun_partial_and_conflict(self):
        from renderrepo import setup
        with tempfile.TemporaryDirectory(prefix='rr setup ') as tmp:
            root=Path(tmp);comfy=root/'Comfy with spaces';comfy.mkdir();(comfy/'main.py').touch()
            python=comfy/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
            checkout=Path(__file__).resolve().parents[1]
            args=SimpleNamespace(checkout=checkout,comfy_dir=comfy,python=None,check=True,yes=True)
            absent={'version':[3,13],'module':None,'prefix':str(comfy/'.venv'),'pip':False}
            installed={**absent,'module':str(checkout/'renderrepo/__init__.py')}
            with patch.object(setup,'probe',return_value=absent),patch('subprocess.run') as run:
                self.assertEqual(setup_comfy(args),1);run.assert_not_called()
            args.check=False
            with patch.object(setup,'probe',side_effect=[absent,installed]),patch('shutil.which',return_value='/usr/bin/uv'),patch('subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(setup_comfy(args),0);self.assertIn(str(python),run.call_args.args[0])
            with patch.object(setup,'probe',return_value=installed),patch('subprocess.run') as run:
                self.assertEqual(setup_comfy(args),0);run.assert_not_called()
            link=comfy/'custom_nodes/comfyui_renderrepo';link.unlink();link.mkdir();(link/'user-file').write_text('keep')
            with patch.object(setup,'probe',return_value=installed):
                with self.assertRaisesRegex(ValueError,'will not overwrite'):setup_comfy(args)
            self.assertEqual((link/'user-file').read_text(),'keep')

    def test_failed_setup_leaves_extension_absent_and_rerun_recovers(self):
        from renderrepo import setup
        with tempfile.TemporaryDirectory() as tmp:
            comfy=Path(tmp);(comfy/'main.py').touch();python=comfy/'python';python.touch()
            checkout=Path(__file__).resolve().parents[1]
            args=SimpleNamespace(checkout=checkout,comfy_dir=comfy,python=python,check=False,yes=True)
            absent={'version':[3,13],'module':None,'pip':False}
            installed={**absent,'module':str(checkout/'renderrepo/__init__.py')}
            with patch.object(setup,'probe',return_value=absent),patch('shutil.which',return_value='/usr/bin/uv'),patch('subprocess.run',return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(ValueError,'installation failed'):setup_comfy(args)
            self.assertFalse((comfy/'custom_nodes').exists())
            with patch.object(setup,'probe',side_effect=[installed,installed]),patch('subprocess.run') as install:
                self.assertEqual(setup_comfy(args),0);install.assert_not_called()

    def test_bounded_discovery_requires_selection_for_multiple_installations(self):
        from renderrepo.setup import choose_comfy
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for path in [root/'ComfyUI',root/'Apps/ComfyUI']:
                path.mkdir(parents=True);(path/'main.py').touch()
            with patch('pathlib.Path.home',return_value=root),patch('pathlib.Path.cwd',return_value=root):
                with self.assertRaisesRegex(ValueError,'Multiple Comfy installation'):choose_comfy(None,False)

    def test_multiple_environments_and_missing_pip_do_not_guess(self):
        from renderrepo import setup
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'one';b=root/'two';a.touch();b.touch()
            with self.assertRaisesRegex(ValueError,'Multiple'):choose([a,b],'environments',False)
            (root/'main.py').touch()
            args=SimpleNamespace(checkout=Path(__file__).resolve().parents[1],comfy_dir=root,python=a,check=False,yes=True)
            with patch.object(setup,'probe',return_value={'version':[3,13],'module':None,'pip':False}),patch('shutil.which',return_value=None):
                with self.assertRaisesRegex(ValueError,'no pip and uv is unavailable'):setup_comfy(args)
            self.assertFalse((root/'custom_nodes').exists())
