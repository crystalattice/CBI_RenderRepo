import asyncio
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import yaml

from renderrepo.comfy_extension import PackageUI, allowed_request
from renderrepo.receipts import read_receipts


class ExtensionTests(unittest.IsolatedAsyncioTestCase):
    def test_local_file_search_includes_explicit_model_roots(self):
        folder = self.root / 'external models';folder.mkdir()
        path = folder / 'NSFW%20Qwen%20LoRA.safetensors';path.write_bytes(b'model')
        result = self.ui.search_local_files({'query': 'qwen', 'directories': [str(folder)]})
        self.assertEqual(result['files'], [{'name': path.name, 'path': str(path)}])
        self.assertFalse(result['truncated'])
        with self.assertRaises(ValueError):self.ui.search_local_files({'query': '', 'directories': []})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name);self.comfy = self.root/'ComfyUI';self.comfy.mkdir()
        self.ui = PackageUI(self.comfy);self.addCleanup(self.ui.temporary.cleanup)
        self.graph={'last_node_id':1,'last_link_id':0,'nodes':[{'id':1,'type':'SaveImage','widgets_values':['demo']}],
                    'links':[],'groups':[],'config':{},'extra':{},'version':0.4,
                    'definitions':{'subgraphs':[{'id':'demo-subgraph','nodes':[{'id':2,'type':'SaveImage'}]}]}}
        self.manifest={'schema_version':0,'name':'demo','version':'1.0','workflow':'workflow.json','artifacts':[]}

    def package(self, graph=None):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.yaml',yaml.safe_dump(self.manifest))
            archive.writestr('workflow.json',json.dumps(self.graph if graph is None else graph))
        return stream.getvalue()

    async def complete(self,token):
        response = await self.ui.approve(token,True)
        if response['state'] == 'running':
            await self.ui.get(token)['task']
        return self.ui.status(token)

    async def test_plan_explains_local_sources_and_unverified_nodes(self):
        source = self.root / 'local model%20name.bin'
        source.write_bytes(b'local model')
        self.manifest['artifacts'] = [{'filename': 'model.bin', 'target': 'models/loras/model.bin'}]
        self.manifest['unresolved'] = ['model.bin: source unavailable',
            'node CLIPLoader: runtime package metadata unavailable']
        plan = await self.ui.preview(self.package(), {'sources': [f'model.bin={source}']})
        self.assertTrue(plan['can_install'], plan)
        log = plan['plan']['log']
        self.assertIn('REUSE: model.bin', log)
        self.assertIn(f'from {source}', log)
        self.assertNotIn('model.bin: source unavailable', log)
        self.assertIn('This does not mean the nodes are missing.', log)
        self.assertNotIn('DOWNLOAD: model.bin', log)
        self.assertFalse((self.comfy / 'models').exists())
        await self.ui.approve(plan['session'], True)
        await self.ui.get(plan['session'])['task']
        status = self.ui.status(plan['session'])
        self.assertIsNone(status['transfer'])
        self.assertNotIn('RR_TRANSFER ', status['progress'])
        self.assertEqual((self.comfy/'models/loras/model.bin').resolve(),source)

    async def test_preview_and_cancel_leave_comfy_untouched(self):
        plan=await self.ui.preview(self.package(),{})
        self.assertTrue(plan['can_install'])
        self.assertIn('PLAN ONLY',plan['plan']['log'])
        self.assertEqual([p.name for p in self.comfy.iterdir()],['.renderrepo'])
        self.assertFalse(read_receipts(self.comfy))
        with self.assertRaises(ValueError):await self.ui.approve(plan['session'],False)
        self.ui.discard(plan['session'])
        self.assertFalse(list((self.comfy / '.renderrepo/jobs').iterdir()))
        self.assertFalse(read_receipts(self.comfy))
        self.assertFalse(self.ui.sessions)

    async def test_install_and_workflow_preserve_subgraphs(self):
        plan=await self.ui.preview(self.package(),{})
        result=await self.complete(plan['session'])
        self.assertEqual(result['result']['exit_code'],0,result)
        self.assertTrue(result['workflow_available'])
        self.assertEqual(json.loads(self.ui.workflow(plan['session'])),self.graph)
        receipt=read_receipts(self.comfy)[0]
        self.assertEqual(Path(receipt['workflow_path']).read_bytes(),self.ui.workflow(plan['session']))
        with self.assertRaises(ValueError):await self.ui.approve(plan['session'],True)

    async def test_changed_plan_requires_fresh_review(self):
        plan=await self.ui.preview(self.package(),{})
        folder=self.comfy/'user/default/workflows';folder.mkdir(parents=True)
        (folder/'demo.json').write_text('{"different":true}')
        result=await self.complete(plan['session'])
        self.assertEqual(result['result']['exit_code'],2)
        self.assertIn('plan changed',result['result']['log'])
        self.assertFalse(read_receipts(self.comfy))

    async def test_unresolved_source_manual_review_and_install(self):
        self.manifest['artifacts']=[{'filename':'tiny.bin','target':'models/checkpoints/tiny.bin'}]
        plan=await self.ui.preview(self.package(),{})
        self.assertFalse(plan['can_install'])
        source=self.root/'tiny.bin';source.write_bytes(b'data')
        plan=await self.ui.review(plan['session'],{'sources':[f'tiny.bin={source}']})
        self.assertTrue(plan['can_install'])
        self.assertFalse((self.comfy/'models').exists())
        result=await self.complete(plan['session'])
        self.assertEqual(result['result']['exit_code'],0,result)
        self.assertEqual((self.comfy/'models/checkpoints/tiny.bin').read_bytes(),b'data')

    async def test_http_artifact_only_requested_after_approval(self):
        source=self.root/'tiny.bin';source.write_bytes(b'data');requests=[]
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(source.parent),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):requests.append(self.path);super().do_GET()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.manifest['artifacts']=[{'filename':'tiny.bin','target':'models/checkpoints/tiny.bin',
             'sources':[{'uri':f'http://127.0.0.1:{server.server_port}/tiny.bin'}]}]
        plan=await self.ui.preview(self.package(),{})
        self.assertEqual(requests,[])
        self.assertFalse((self.comfy/'models').exists())
        self.assertEqual((await self.complete(plan['session']))['result']['exit_code'],0)
        self.assertEqual(requests,['/tiny.bin'])

    async def test_export_current_editor_graph(self):
        exported=await self.ui.export({'workflow':self.graph,'name':'exported','version':'2.0'})
        self.assertEqual(exported['state'],'running')
        await self.ui.get(exported['session'])['task']
        self.assertEqual(self.ui.status(exported['session'])['state'],'exported')
        archive=self.ui.get(exported['session'])['archive']
        with zipfile.ZipFile(archive) as packed:
            self.assertEqual(json.loads(packed.read('workflow.json')),self.graph)
            self.assertEqual(yaml.safe_load(packed.read('manifest.yaml'))['name'],'exported')
        self.assertEqual(list(self.comfy.iterdir()),[])

    async def test_export_job_reports_progress_and_failure(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def command(argv, progress):
            progress('Hashing test-model.bin')
            entered.set()
            await release.wait()
            return {'exit_code': 2, 'log': 'Model file could not be read'}
        with patch.object(self.ui, 'command', command):
            job = await self.ui.export({'workflow': self.graph, 'name': 'failed-export'})
            await entered.wait()
            state = self.ui.status(job['session'])
            self.assertEqual(state['state'], 'running')
            self.assertIn('Hashing test-model.bin', state['progress'])
            with self.assertRaises(ValueError): self.ui.discard(job['session'])
            with self.assertRaises(ValueError):
                await self.ui.export({'workflow': self.graph, 'name': 'duplicate'})
            release.set()
            await self.ui.get(job['session'])['task']
            state = self.ui.status(job['session'])
            self.assertEqual(state['state'], 'done')
            self.assertEqual(state['result']['exit_code'], 2)
            self.assertIn('could not be read', state['result']['log'])
            self.assertFalse(self.ui.busy)
            self.ui.discard(job['session'])

    async def test_api_prompt_not_imported_into_editor(self):
        with self.assertRaisesRegex(ValueError,'editor-format'):
            await self.ui.preview(self.package({'1':{'class_type':'SaveImage','inputs':{}}}),{})
        self.assertFalse(self.ui.sessions)

    async def test_invalid_options_and_expired_session(self):
        with self.assertRaises(ValueError):self.ui.options({'python_dependencies':'yes'})
        with self.assertRaises(ValueError):self.ui.options({'comfy_dir':'/other'})
        with self.assertRaises(ValueError):self.ui.get('unknown')

    async def test_overlarge_expansion_rejected(self):
        with patch('renderrepo.comfy_extension.MAX_UPLOAD',1000),self.assertRaises(ValueError):
            await self.ui.preview(self.package(dict(self.graph,extra={'long':'x'*2000})),{})
        self.assertFalse(self.ui.sessions)

    async def test_serial_install_and_running_session_not_discarded(self):
        plan=await self.ui.preview(self.package(),{})
        self.ui.busy=True
        with self.assertRaises(ValueError):await self.ui.approve(plan['session'],True)
        self.ui.busy=False
        await self.ui.approve(plan['session'],True)
        with self.assertRaises(ValueError):self.ui.discard(plan['session'])
        await self.ui.get(plan['session'])['task']

    def test_local_origin_guard(self):
        request=SimpleNamespace(remote='127.0.0.1',host='localhost:8188',headers={'X-RenderRepo-Request':'1','Origin':'http://localhost:8188'})
        self.assertTrue(allowed_request(request))
        request.headers['Origin']='https://evil.example';self.assertFalse(allowed_request(request))
        request.headers.pop('Origin');request.remote='192.168.1.1';self.assertFalse(allowed_request(request))
        request.remote='127.0.0.1';request.headers.clear();self.assertFalse(allowed_request(request))

    def test_inventory_keeps_dirty_and_non_git_nodes_visible(self):
        folder = self.comfy/'custom_nodes'; folder.mkdir()
        repo = folder/'ComfyUI-GGUF'; repo.mkdir()
        def git(*args):
            return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        git('init'); git('config', 'user.email', 'test@example.org'); git('config', 'user.name', 'Test')
        (repo/'node.py').write_text('# node')
        git('add', '.'); git('commit', '-m', 'node')
        git('remote', 'add', 'origin', 'https://github.com/city96/ComfyUI-GGUF.git')
        clean = self.ui.node_inventory()[0]
        self.assertTrue(clean['exportable'])
        (repo/'uv.lock').write_text('lock')
        (folder/'zip-node').mkdir()
        (folder/'loose.py').write_text('# loose')
        inventory = {node['name']: node for node in self.ui.node_inventory()}
        self.assertFalse(inventory['ComfyUI-GGUF']['exportable'])
        self.assertIn('local changes', inventory['ComfyUI-GGUF']['unavailable_reason'])
        self.assertIn('missing Git history', inventory['zip-node']['unavailable_reason'])
        self.assertIn('origin URL', inventory['loose.py']['unavailable_reason'])
        self.assertTrue((repo/'uv.lock').exists())

    async def test_configured_input_directory_is_used(self):
        input_dir=self.root/'custom-input'
        self.ui.input_dir=input_dir
        source=self.root/'input.ppm';source.write_bytes(b'example')
        self.manifest['artifacts']=[{'filename':'input.ppm','target':'input/input.ppm','sources':[{'uri':str(source)}]}]
        plan=await self.ui.preview(self.package(),{})
        self.assertEqual((await self.complete(plan['session']))['result']['exit_code'],0)
        self.assertEqual((input_dir/'input.ppm').read_bytes(),b'example')
        self.assertFalse((self.comfy/'input').exists())


class RepositoryExtensionTests(unittest.IsolatedAsyncioTestCase):
    setUp = ExtensionTests.setUp
    package = ExtensionTests.package
    complete = ExtensionTests.complete
    # Only the new cases run here; inherited cases run in ExtensionTests above.
    def prepare_repository(self, version='0.1.0'):
        from renderrepo.cli import main
        publisher = self.root / 'publisher'
        publisher.mkdir(exist_ok=True)
        self.manifest['version'] = version
        self.manifest['description'] = 'Tiny repository workflow'
        self.graph['nodes'][0]['widgets_values'] = [version]
        (publisher / f'demo-{version}.rrpkg').write_bytes(self.package())
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(main(['index', str(publisher)]), 0)
        return publisher

    async def operation(self, response):
        await self.ui.repository_operations[response['token']]['task']
        return self.ui.repository_operation(response['token'])

    async def test_repository_review_install_receipt_updates_and_reopen(self):
        publisher = self.prepare_repository()
        self.ui.repository_source({'action': 'add', 'name': 'personal', 'uri': str(publisher)})
        with patch('renderrepo.repository.fetch_artifact', side_effect=AssertionError('no archives in refresh')):
            op = await self.operation(await self.ui.repository_refresh({'source': None}))
        self.assertEqual(op['sources'][0]['state'], 'success')
        self.assertEqual(op['progress'], 'Refresh finished: 1 succeeded, 0 failed.')
        self.assertGreaterEqual(op['finished_at'], op['started_at'])
        data = self.ui.repositories()
        selected = data['packages'][0]
        self.assertEqual(selected['description'], 'Tiny repository workflow')
        self.assertIsNone(selected['installed_version'])
        self.assertTrue(data['sources'][0]['refreshed_at'])
        values = dict(name='demo', source='personal', version='0.1.0', sha256=selected['sha256'], approved=False)
        with self.assertRaises(ValueError):
            await self.ui.repository_retrieve(values)
        values['approved'] = True
        op = await self.operation(await self.ui.repository_retrieve(values))
        self.assertEqual(op['state'], 'done', op)
        review = op['result']
        self.assertTrue(review['can_install'], review)
        self.assertEqual(read_receipts(self.comfy), [])
        result = await self.complete(review['session'])
        self.assertEqual(result['result']['exit_code'], 0, result)
        receipt = read_receipts(self.comfy)[0]
        self.assertEqual(receipt['repository']['name'], 'personal')
        self.assertEqual(receipt['repository']['archive_sha256'], selected['sha256'])
        self.prepare_repository('0.2.0')
        await self.operation(await self.ui.repository_refresh({'source': 'personal'}))
        updates = self.ui.repositories()['updates']
        self.assertEqual([(u['installed_version'], u['version']) for u in updates], [('0.1.0', '0.2.0')])
        # Reviewing a replacement must not unregister the installed package or delete edits.
        workflow = Path(receipt['workflow_path'])
        workflow.write_bytes(b'user edits')
        selected = next(p for p in self.ui.repositories()['packages'] if p['version'] == '0.2.0')
        op = await self.operation(await self.ui.repository_retrieve(dict(values, version='0.2.0', sha256=selected['sha256'])))
        self.assertEqual(op['state'], 'done', op)
        self.assertFalse(op['result']['can_install'])
        self.assertIn('renderrepo remove', op['result']['plan']['log'])
        self.assertEqual(workflow.read_bytes(), b'user edits')
        self.assertEqual(read_receipts(self.comfy)[0]['version'], '0.1.0')
        reopened = PackageUI(self.comfy)
        self.addCleanup(reopened.temporary.cleanup)
        self.assertEqual(reopened.status(review['session'])['outcome'], 'completed')
        self.assertEqual(reopened.get(review['session'])['repository_info'], receipt['repository'])
        self.assertEqual(reopened.repositories()['updates'][0]['version'], '0.2.0')

    async def test_repository_http_stale_conflicts_and_same_size_hash_rejection(self):
        import functools
        publisher = self.prepare_repository()
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SimpleHTTPRequestHandler, directory=str(publisher)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.ui.repository_source({'action': 'add', 'name': 'http', 'uri': f'http://127.0.0.1:{server.server_port}/index.json'})
            await self.operation(await self.ui.repository_refresh({'source': 'http'}))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()
        op = await self.operation(await self.ui.repository_refresh({'source': 'http'}))
        self.assertEqual(op['sources'][0]['state'], 'failed')
        self.assertEqual(op['progress'], 'Refresh finished: 0 succeeded, 1 failed.')
        self.assertGreaterEqual(op['finished_at'], op['started_at'])
        self.assertTrue(self.ui.repositories()['packages'][0]['cache_stale'])
        self.assertTrue(self.ui.repositories()['sources'][0]['stale'])
        self.ui.repository_source({'action': 'add', 'name': 'local', 'uri': str(publisher)})
        await self.operation(await self.ui.repository_refresh({'source': 'local'}))
        self.assertEqual({p['repository'] for p in self.ui.repositories()['packages']}, {'http', 'local'})
        from renderrepo.repository import select_package
        args = self.ui.repository_args(package=Path('demo'), version='0.1.0')
        with self.assertRaisesRegex(ValueError, 'multiple repositories'):
            select_package(args, report=lambda message: None)
        selected = next(p for p in self.ui.repositories()['packages'] if p['repository'] == 'local')
        archive = publisher / 'demo-0.1.0.rrpkg'
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 1
        archive.write_bytes(data)
        op = await self.operation(await self.ui.repository_retrieve(dict(name='demo', source='local', version='0.1.0', sha256=selected['sha256'], approved=True)))
        self.assertEqual(op['state'], 'failed')
        self.assertIn('sha256 mismatch', op['error'])
        self.assertEqual(read_receipts(self.comfy), [])
        self.assertFalse((self.comfy / 'user').exists())
        self.ui.repository_source({'action': 'disable', 'name': 'local'})
        self.assertEqual({p['repository'] for p in self.ui.repositories()['packages']}, {'http'})
        with self.assertRaisesRegex(ValueError, 'disabled'):
            await self.ui.repository_retrieve(dict(name='demo', source='local', version='0.1.0', sha256=selected['sha256'], approved=True))
        self.ui.repository_source({'action': 'enable', 'name': 'local'})
        self.ui.repository_source({'action': 'remove', 'name': 'local'})
        self.assertEqual([s['name'] for s in self.ui.repositories()['sources']], ['http'])

    async def test_repository_refresh_is_nonblocking_and_provenance_is_bound_to_approval(self):
        import time
        from renderrepo.installation_history import within_scope
        publisher = self.prepare_repository()
        self.ui.repository_source({'action': 'add', 'name': 'local', 'uri': str(publisher)})
        from renderrepo.repository import refresh_repository
        def slow(*args):
            time.sleep(.2)
            return refresh_repository(*args)
        with patch('renderrepo.repository.refresh_repository', slow):
            op = await self.ui.repository_refresh({'source': None})
            await asyncio.sleep(.02)
            self.assertEqual(self.ui.repository_operation(op['token'])['state'], 'running')
            self.assertEqual(self.ui.history(), [])
            with self.assertRaisesRegex(ValueError, 'operation active'):
                self.ui.repository_source({'action': 'remove', 'name': 'local'})
            await self.operation(op)
        selected = self.ui.repositories()['packages'][0]
        op = await self.operation(await self.ui.repository_retrieve(dict(name='demo', source='local', version='0.1.0', sha256=selected['sha256'], approved=True)))
        session = self.ui.get(op['result']['session'])
        scope = session['plan']['scope']
        changed = dict(scope, repository=dict(scope['repository'], name='changed'))
        self.assertFalse(within_scope(scope, changed))
        (session['directory'] / 'repository.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'provenance changed'):
            self.ui.get(op['result']['session'])

class BridgeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_logs_distinguish_request_rejection_and_backend_exception(self):
        from renderrepo.comfy_extension import register
        class Routes:
            handlers = {}
            def __getattr__(self, method):
                def route(path):
                    def bind(handler):
                        self.handlers[(method, path)] = handler
                        return handler
                    return bind
                return route
        class Content:
            async def iter_chunked(self, size):
                yield b'not a ZIP archive'
        web = SimpleNamespace(json_response=lambda data, status=200: SimpleNamespace(payload=data, status=status))
        with tempfile.TemporaryDirectory() as directory:
            routes = Routes()
            folders = SimpleNamespace(base_path=directory, folder_names_and_paths={}, get_input_directory=lambda: directory)
            with patch.dict('sys.modules', {'aiohttp': SimpleNamespace(web=web)}):
                ui = register(routes, folders)
            self.addCleanup(ui.temporary.cleanup)
            handler = routes.handlers[('post', '/renderrepo/preview')]
            request = SimpleNamespace(remote='127.0.0.1', headers={'X-RenderRepo-Request':'1'}, host='127.0.0.1:8188',
                                      method='POST', path='/api/renderrepo/preview', content=Content())
            with self.assertLogs('renderrepo.comfy_extension', level='INFO') as captured:
                result = await handler(request)
            self.assertEqual(result.status, 400)
            self.assertTrue(any('POST /api/renderrepo/preview -> 400' in line for line in captured.output))
            from unittest.mock import AsyncMock
            with patch.object(ui, 'preview', AsyncMock(side_effect=RuntimeError('controlled failure'))):
                with self.assertLogs('renderrepo.comfy_extension', level='INFO') as captured:
                    result = await handler(request)
            self.assertEqual(result.status, 500)
            self.assertIn('inspect the Comfy traceback', result.payload['error'])
            self.assertTrue(any('POST /api/renderrepo/preview -> 500' in line for line in captured.output))
            self.assertTrue(any('controlled failure' in line for line in captured.output))
            request.headers = {}
            with self.assertLogs('renderrepo.comfy_extension', level='INFO'):
                result = await handler(request)
            self.assertEqual(result.status, 403)
            self.assertEqual(read_receipts(Path(directory)), [])

    async def test_prepared_repository_fixture_reviews_installs_and_opens_without_artifacts(self):
        from scripts.prepare_repository_gui_fixture import prepare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('sys.stdout', io.StringIO()):
                fixture = prepare(root / 'fixture')
            comfy = root / 'ComfyUI'
            comfy.mkdir()
            ui = PackageUI(comfy)
            self.addCleanup(ui.temporary.cleanup)
            ui.repository_source({'action':'add', 'name':fixture['source_name'], 'uri':fixture['source_path']})
            response = await ui.repository_refresh({'source':fixture['source_name']})
            await ui.repository_operations[response['token']]['task']
            selected = ui.repositories()['packages'][0]
            response = await ui.repository_retrieve({'name':selected['name'], 'version':selected['version'],
                'source':selected['repository'], 'sha256':selected['sha256'], 'approved':True})
            await ui.repository_operations[response['token']]['task']
            review = ui.repository_operation(response['token'])['result']
            self.assertTrue(review['can_install'], review)
            self.assertEqual(review['manifest']['artifacts'], [])
            self.assertEqual(read_receipts(comfy), [])
            await ui.approve(review['session'], True)
            await ui.get(review['session'])['task']
            status = ui.status(review['session'])
            self.assertEqual(status['outcome'], 'completed')
            graph = json.loads(ui.workflow(review['session']))
            self.assertEqual([(n['id'], n['type']) for n in graph['nodes']], [(1, 'SaveImage')])
            receipt = read_receipts(comfy)[0]
            self.assertEqual(receipt['repository']['archive_sha256'], fixture['package']['sha256'])
            self.assertEqual(receipt['workflow_sha256'], fixture['workflow_sha256'])
            self.assertEqual(receipt['artifacts'], [])
            ui.repository_source({'action':'remove', 'name':fixture['source_name']})
            ui.discard(review['session'])
            self.assertEqual(ui.history(), [])
            self.assertEqual(ui.repositories()['installed'][0]['version'], '0.1.0')
            workflow = Path(receipt['workflow_path'])
            edited = json.loads(workflow.read_bytes())
            edited['nodes'][0]['title'] = 'My preserved edit'
            workflow.write_text(json.dumps(edited))
            self.assertTrue(ui.repositories()['installed'][0]['workflow_modified'])
            before = workflow.read_bytes()
            self.assertEqual(json.loads(ui.installed_workflow(receipt['name'])), edited)
            self.assertEqual(workflow.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                ui.installed_workflow('not-installed')
