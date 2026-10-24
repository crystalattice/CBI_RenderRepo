import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from renderrepo.comfy_extension import PackageUI
from renderrepo.receipts import read_receipts, remove_package

class StorefrontTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'Comfy';self.root.mkdir()
        self.models=Path(self.tmp.name)/'external';self.models.mkdir()
        self.ui=PackageUI(self.root,[self.models]);self.addCleanup(self.ui.temporary.cleanup)
        self.ui.target_url='http://127.0.0.1:9999'
        self.observed={'nodes':{'SaveImage':{},'CheckpointLoaderSimple':{}},'system':{'system':{},'devices':[]},'errors':{},'observed_at':'2026-10-18T00:00:00Z','server':self.ui.target_url}
        self.snapshot=patch('renderrepo.readiness.snapshot',side_effect=lambda *a:self.observed);self.snapshot.start();self.addCleanup(self.snapshot.stop)
    def graph(self,model=None,node='SaveImage'):
        graph={'nodes':[{'id':1,'type':node,'widgets_values':[]}],'links':[],'version':0.4}
        if model: graph['nodes'].append({'id':2,'type':'CheckpointLoaderSimple','widgets_values':[model]})
        return json.dumps(graph).encode()
    async def prepare(self,data,name='imported.json'):
        return await self.ui.preview_workflow(data,name)
    async def complete(self,result):
        response=await self.ui.approve(result['session'],True)
        self.assertEqual(response['state'],'running',response)
        await self.ui.get(result['session'])['task']
        status=self.ui.status(result['session']);self.assertEqual(status['result']['exit_code'],0,status)
        return status
    async def test_original_bytes_nested_json_and_restart(self):
        graph=json.loads(self.graph());graph['nodes'].append({'id':2,'type':'inner'})
        graph['definitions']={'subgraphs':[{'id':'inner','nodes':[{'id':3,'type':'SaveImage'}]}]}
        data=json.dumps(graph,indent=2).encode();r=await self.prepare(data)
        self.assertTrue(r['can_install'],r);self.assertEqual(r['target_check']['state'],'available')
        self.assertEqual((self.ui.get(r['session'])['directory']/'original-workflow.json').read_bytes(),data)
        fresh=PackageUI(self.root,[self.models]);self.addCleanup(fresh.temporary.cleanup)
        self.assertEqual(fresh.get(r['session'])['imported_workflow']['sha256'],hashlib.sha256(data).hexdigest())
        result=await self.complete(r);self.assertEqual(Path(result['workflow_path']).read_bytes(),data)
    async def test_api_prompt_explicitly_rejected(self):
        with self.assertRaisesRegex(ValueError,'API-format'):await self.prepare(b'{"1":{"class_type":"SaveImage","inputs":{}}}')

    async def test_invalid_review_options_rejected_before_source_selection(self):
        result = await self.prepare(self.graph())
        for options in ([], {'sources': [42]}, {'sources': 'not-a-list'}):
            with self.assertRaises(ValueError):
                await self.ui.review(result['session'], options)

    async def test_default_download_source_is_one_reviewed_choice(self):
        graph=json.loads(self.graph('missing.bin'))
        graph['nodes'][1]['properties']={'models':[
            {'name':'missing.bin','directory':'checkpoints','url':'http://127.0.0.1:9998/first'},
            {'name':'missing.bin','directory':'checkpoints','url':'http://127.0.0.1:9998/second'}]}
        result=await self.prepare(json.dumps(graph).encode())
        self.assertEqual(result['plan']['scope']['artifacts'][0]['sources'],['http://127.0.0.1:9998/first'])
        self.assertEqual(result['options']['sources'],['missing.bin=http://127.0.0.1:9998/first'])

    async def test_file_acquisition_is_blocked_on_noninstallable_target(self):
        self.ui.target_capabilities['install_artifacts']=False
        result=await self.prepare(self.graph('missing.bin'))
        self.assertFalse(result['can_install'])
        self.assertIn('missing.bin',result['target_check']['blockers'])
    async def test_unknown_inventory_is_unknown_not_unsupported(self):
        self.observed['nodes']=None
        r=await self.prepare(self.graph());self.assertEqual(r['target_check']['state'],'unknown');self.assertTrue(r['can_install'])
    async def test_noninstallable_missing_node_blocks_before_any_transfer(self):
        self.ui.target_capabilities={'install_nodes':False,'install_artifacts':False}
        r=await self.prepare(self.graph('missing.bin','MOSSFixture'))
        self.assertFalse(r['can_install']);self.assertEqual(r['target_check']['nodes'][0]['state'],'unavailable')
        self.assertFalse((self.root/'models').exists())
        with self.assertRaises(ValueError):await self.ui.approve(r['session'],True)
    async def test_declared_missing_custom_node_resolves_without_fetching_code(self):
        r=await self.prepare(self.graph(node='MOSSFixture'))
        self.assertEqual(r['target_check']['nodes'][0]['state'],'needs_source')
        r=await self.ui.resolve_requirement(r['session'],{'kind':'node','name':'MOSSFixture','repository':'fixture-nodes','source':'https://example.invalid/declared-fixture.git','revision':'a'*40})
        self.assertTrue(r['can_install'],r);self.assertEqual(r['target_check']['state'],'resolvable')
        self.assertEqual(r['plan']['scope']['nodes'][0]['revision'],'a'*40)
        self.assertFalse((self.root/'custom_nodes').exists())
    async def test_target_change_invalidates_approval(self):
        r=await self.prepare(self.graph());self.observed['nodes']={}
        changed=await self.ui.approve(r['session'],True)
        self.assertTrue(changed['approval_required']);self.assertFalse(changed['can_install'])
        self.assertFalse((self.root/'user').exists())
    async def test_focused_model_resolution_preserves_other_model_and_restart(self):
        existing=self.models/'known.bin';existing.write_bytes(b'known')
        data=json.loads(self.graph('known.bin'));data['nodes'].append({'id':3,'type':'CheckpointLoaderSimple','widgets_values':['unresolved.bin']})
        original=json.dumps(data).encode();r=await self.prepare(original)
        self.assertFalse(r['can_install']);other=self.models/'chosen-other-name.bin';other.write_bytes(b'new')
        r=await self.ui.resolve_requirement(r['session'],{'kind':'artifact','name':'unresolved.bin','source':str(other)})
        self.assertTrue(r['can_install'],r);self.assertEqual([a['operation'] for a in r['plan']['scope']['artifacts']],['reuse','reuse'])
        fresh=PackageUI(self.root,[self.models]);self.addCleanup(fresh.temporary.cleanup)
        self.assertIn(str(other),fresh.status(r['session'])['options']['sources'][0])
        await self.complete(r)
        self.assertTrue((self.root/'models/checkpoints/unresolved.bin').is_symlink())
        self.assertEqual((self.root/'models/checkpoints/unresolved.bin').resolve(),other)
        self.assertEqual((self.ui.get(r['session'])['directory']/'original-workflow.json').read_bytes(),original)
        saved=json.loads((self.root/'.renderrepo/user-mappings.json').read_text());self.assertIn('not official',saved['artifacts']['unresolved.bin']['provenance'])
    async def test_shared_verified_artifact_zero_second_download_and_removal(self):
        model=self.models/'weights.bin';model.write_bytes(b'tiny fixture never loaded')
        digest=hashlib.sha256(model.read_bytes()).hexdigest()
        # Trusted retained package evidence, not a filename-only identity assertion.
        evidence={'filename':'weights.bin','target':'models/checkpoints/weights.bin','sha256':digest,'size_bytes':model.stat().st_size,'sources':[]}
        catalogs={'packages':[{'dependency_inventory':{'artifacts':[evidence]}}]}
        with patch.object(self.ui,'repositories',return_value=catalogs):
            first=await self.prepare(self.graph('weights.bin'),'first.json');await self.complete(first)
            second=await self.prepare(self.graph('weights.bin'),'second.json');await self.complete(second)
        scope=second['plan']['scope']['artifacts'][0]
        self.assertEqual(scope['operation'],'reuse');self.assertEqual(scope['reuse_path'],str(model))
        self.assertEqual(scope['sha256'],digest)
        remove_package(SimpleNamespace(comfy_dir=self.root,name='first',yes=True))
        self.assertEqual((self.root/'models/checkpoints/weights.bin').resolve(),model)
        self.assertEqual(model.read_bytes(),b'tiny fixture never loaded')
        self.assertEqual([r['name'] for r in read_receipts(self.root)],['second'])
    async def test_same_filename_wrong_hash_never_reused_or_overwritten(self):
        wrong=self.root/'models/checkpoints/weights.bin';wrong.parent.mkdir(parents=True);wrong.write_bytes(b'wrong')
        evidence={'filename':'weights.bin','target':'models/checkpoints/weights.bin','sha256':hashlib.sha256(b'right').hexdigest(),'sources':[{'uri':'https://example.invalid/no-call'}]}
        with patch.object(self.ui,'repositories',return_value={'packages':[{'dependency_inventory':{'artifacts':[evidence]}}]}):r=await self.prepare(self.graph('weights.bin'))
        self.assertFalse(r['can_install']);self.assertEqual(wrong.read_bytes(),b'wrong')
    async def test_reusable_mapping_does_not_become_official_hash(self):
        model=self.models/'chosen.bin';model.write_bytes(b'fixture')
        r=await self.prepare(self.graph('unknown.bin'),'a.json')
        await self.ui.resolve_requirement(r['session'],{'kind':'artifact','name':'unknown.bin','source':str(model)})
        other=await self.prepare(self.graph('unknown.bin'),'b.json')
        self.assertTrue(other['can_install'],other)
        self.assertNotIn('sha256',other['manifest']['artifacts'][0])

    async def test_stale_review_identity_cannot_approve_changed_source(self):
        r=await self.prepare(self.graph())
        old=r['review_id'];await self.ui.review(r['session'],{'artifact_dirs':[str(self.models)]})
        result=await self.ui.approve(r['session'],True,review_id=old)
        self.assertTrue(result['approval_required']);self.assertNotEqual(result['review_id'],old)
        self.assertFalse((self.root/'user').exists())

    async def test_selected_mirror_survives_interrupted_launch_restart_and_transfer(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        payload=b'tiny local binary fixture never loaded as a model';requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base='http://127.0.0.1:'+str(server.server_port)
        artifact={'filename':'mirror.bin','target':'models/checkpoints/mirror.bin','sha256':hashlib.sha256(payload).hexdigest(),'size_bytes':len(payload),'sources':[{'uri':base+'/upstream'},{'uri':base+'/mirror'}]}
        with patch.object(self.ui,'repositories',return_value={'packages':[{'dependency_inventory':{'artifacts':[artifact]}}]}):r=await self.prepare(self.graph('mirror.bin'))
        selected=await self.ui.review(r['session'],{'sources':['mirror.bin='+base+'/mirror']})
        self.assertEqual(selected['plan']['scope']['artifacts'][0]['sources'],[base+'/mirror'])
        create=asyncio.create_subprocess_exec
        async def interrupt(*args,**kwargs):
            if 'renderrepo.installation_worker' in args:raise OSError('simulated interrupted launch')
            return await create(*args,**kwargs)
        with patch('asyncio.create_subprocess_exec',side_effect=interrupt):
            with self.assertRaisesRegex(OSError,'simulated'):await self.ui.approve(r['session'],True,review_id=selected['review_id'])
        fresh=PackageUI(self.root,[self.models]);self.addCleanup(fresh.temporary.cleanup);fresh.target_url=self.ui.target_url
        self.assertEqual(fresh.status(r['session'])['options']['sources'],['mirror.bin='+base+'/mirror'])
        result=await fresh.resume(r['session']);self.assertEqual(result['state'],'running',result)
        await fresh.get(r['session'])['task']
        self.assertEqual(fresh.status(r['session'])['result']['exit_code'],0)
        self.assertEqual(requests,['/mirror']);self.assertEqual((self.root/'models/checkpoints/mirror.bin').read_bytes(),payload)

    async def test_conflicting_node_pins_never_choose_a_catalog_revision_arbitrarily(self):
        graph=json.loads(self.graph(node='MOSSFixture'));graph['nodes'][0]['properties']={'cnr_id':'fixture-nodes'}
        candidates=[{'dependency_inventory':{'runtime':{'custom_nodes':[{'name':'fixture-nodes','source':'https://example.invalid/fixture.git','revision':revision*40}]}}} for revision in ('a','b')]
        with patch.object(self.ui,'repositories',return_value={'packages':candidates}):r=await self.prepare(json.dumps(graph).encode())
        self.assertFalse(r['can_install']);self.assertEqual(r['target_check']['nodes'][0]['state'],'needs_source')
        self.assertEqual(r['manifest']['runtime']['custom_nodes'],[])

    async def test_ordinary_store_click_approves_same_prepared_scope(self):
        result = await self.prepare(self.graph())
        self.assertEqual(result['store_scope']['summary'], 'Add one workflow. No downloads.')
        self.assertEqual(result['store_scope']['decisions'], [])
        installed = await self.ui.approve(result['session'], True, review_id=result['review_id'], confirmations=[])
        self.assertEqual(installed['state'], 'running')
        await self.ui.get(result['session'])['task']
        self.assertEqual(self.ui.get(result['session'])['approved_scope'], result['plan']['scope'])

    async def test_import_review_recomputes_graph_facts_with_target_schema(self):
        fixtures=Path(__file__).parent/'fixtures'
        data=(fixtures/'image_z_image_turbo.json').read_bytes()
        self.observed['nodes']=json.loads((fixtures/'comfy_analysis_object_info.json').read_text())
        r=await self.prepare(data,'image_z_image_turbo.json')
        self.assertEqual(r['capabilities']['prompts'][0]['role'],'positive')
        self.assertIn('/object_info',r['capabilities']['prompts'][0]['role_evidence'])
        self.assertEqual(len(r['capabilities']['controls']),7)
        self.assertEqual(len(r['target_check']['nodes']),10)
        self.assertTrue(all(n['state']=='available' for n in r['target_check']['nodes']))
        self.assertEqual(self.ui.get(r['session'])['package'].joinpath('workflow.json').read_bytes(),data)
        self.assertIn('does not establish execution',r['capabilities']['limitations'][0])
        self.assertEqual(r['target_check']['installation_observations'],[])
        # A stale retained summary is replaced on review, not used as truth.
        self.ui.get(r['session'])['capabilities']={'purpose':'stale','analysis_version':1}
        new=await self.ui.review(r['session'],r['options'])
        self.assertEqual(new['capabilities']['analysis_version'],2)
        self.assertIn('Generate an image',new['capabilities']['purpose'])

    async def test_prior_install_observations_are_exact_byte_scoped_and_outside_claims(self):
        model=self.models/'local.bin';model.write_bytes(b'local fingerprint')
        item={'filename':'local.bin','target':'models/checkpoints/local.bin','sha256':hashlib.sha256(model.read_bytes()).hexdigest(),'sources':[]}
        data=self.graph('local.bin')
        with patch.object(self.ui,'repositories',return_value={'packages':[{'dependency_inventory':{'artifacts':[item]}}]}):
            first=await self.prepare(data,'first.json');await self.complete(first)
            new=await self.prepare(data,'second.json')
        observations=new['target_check']['installation_observations']
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['download_count'],0)
        self.assertTrue(observations[0]['artifacts'][0]['hash_matched'])
        self.assertEqual(observations[0]['artifacts'][0]['operation'],'reuse')
        self.assertNotIn('installation_observations',new['capabilities'])
        self.assertEqual(self.ui.installation_observations(data+b' '),[])

    async def test_store_code_and_python_require_separate_scoped_confirmation(self):
        result = await self.prepare(self.graph(node='MOSSFixture'))
        result = await self.ui.resolve_requirement(result['session'], {'kind':'node','name':'MOSSFixture',
            'repository':'fixture-nodes','source':'https://example.invalid/never-fetched.git','revision':'a'*40})
        self.assertEqual([d['id'] for d in result['store_scope']['decisions']], ['code'])
        self.assertFalse(result['options'].get('python_dependencies', False))
        rejected = await self.ui.approve(result['session'], True, review_id=result['review_id'], confirmations=[])
        self.assertTrue(rejected['confirmation_required'])
        self.assertFalse((self.root/'custom_nodes').exists())
        python = await self.ui.review(result['session'], {'python_dependencies':True})
        self.assertEqual([d['id'] for d in python['store_scope']['decisions']], ['code','python'])
        stale = await self.ui.approve(result['session'], True, review_id=result['review_id'], confirmations=['code'])
        self.assertTrue(stale['approval_required'])
        rejected = await self.ui.approve(result['session'], True, review_id=python['review_id'], confirmations=['code'])
        self.assertTrue(rejected['confirmation_required'])
        # Dispatch is simulated after the correct decisions, never fetching node code or running pip.
        fake = {'exit_code':0,'log':'RR_PLAN '+json.dumps(python['plan']['scope'])}
        with patch.object(self.ui,'command',return_value=fake), patch('asyncio.create_subprocess_exec',side_effect=OSError('simulated launch fence')):
            with self.assertRaisesRegex(OSError,'launch fence'):
                await self.ui.approve(result['session'],True,review_id=python['review_id'],confirmations=['code','python'])
        self.assertEqual(self.ui.get(result['session'])['approved_scope'],python['plan']['scope'])
        self.assertFalse((self.root/'custom_nodes').exists())

    async def test_concurrent_scope_change_finishes_before_old_authorization_is_checked(self):
        result=await self.prepare(self.graph())
        entered=asyncio.Event();release=asyncio.Event();original=self.ui.command
        async def delayed(argv,progress=None):
            entered.set();await release.wait();return await original(argv,progress)
        with patch.object(self.ui,'command',side_effect=delayed):
            change=asyncio.create_task(self.ui.review(result['session'],{'artifact_dirs':[str(self.models)]}))
            await entered.wait()
            approval=asyncio.create_task(self.ui.approve(result['session'],True,review_id=result['review_id'],confirmations=[]))
            await asyncio.sleep(0)
            self.assertFalse(approval.done())
            release.set();await change;rejected=await approval
        self.assertTrue(rejected['approval_required'])
        self.assertFalse((self.root/'user').exists())

    async def test_reusing_verified_node_checkout_does_not_ask_to_install_code(self):
        session={'manifest':{},'plan':{'log':'REUSE CODE: fixture @ '+ 'a'*40 +' from /owned/fixture',
            'scope':{'nodes':[{'name':'fixture','revision':'a'*40,'operation':'reuse'}], 'python':None}}}
        self.assertEqual(self.ui.store_scope(session)['store_scope']['decisions'],[])
        session['plan']['scope']['nodes'][0]['operation']='install'
        session['plan']['log']='INSTALL CODE: fixture @ '+ 'a'*40 +' from /owned/fixture'
        self.assertEqual([d['id'] for d in self.ui.store_scope(session)['store_scope']['decisions']],['code'])

    async def test_unknown_download_size_is_a_focused_decision(self):
        graph=json.loads(self.graph('tiny.bin'))
        graph['nodes'][1]['properties']={'models':[{'name':'tiny.bin','directory':'checkpoints','url':'http://127.0.0.1:9998/tiny'}]}
        result=await self.prepare(json.dumps(graph).encode())
        self.assertEqual([d['id'] for d in result['store_scope']['decisions']],['unknown-size'])
        rejected=await self.ui.approve(result['session'],True,review_id=result['review_id'],confirmations=[])
        self.assertTrue(rejected['confirmation_required'])
        self.assertFalse((self.root/'models').exists())

    async def test_capsule_target_checks_do_not_rewrite_portable_identity(self):
        from renderrepo.capsule import read_json
        r=await self.prepare(self.graph())
        session=self.ui.get(r['session']);folder=session['package']
        before={p.name:p.read_bytes() for p in folder.iterdir()}
        descriptor=read_json(folder/'capsule.json')
        self.observed['nodes']={}
        changed=await self.ui.review(r['session'],{})
        self.assertFalse(changed['can_install'])
        self.assertEqual(changed['capsule'],r['capsule'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})
        self.assertNotIn('target_check',descriptor['claims'])
        self.assertNotIn('node_availability',descriptor['claims'].get('runtime',{}))

    async def test_node_resolution_creates_new_linked_session_with_original_intact(self):
        from renderrepo.capsule import read_json
        r=await self.prepare(self.graph(node='MOSSFixture'))
        original=self.ui.get(r['session']);folder=original['package']
        original['direct_source']='https://example.invalid/original.rrpkg';self.ui.persist(original)
        before={p.name:p.read_bytes() for p in folder.iterdir()}
        changed=await self.ui.resolve_requirement(r['session'],{'kind':'node','name':'MOSSFixture',
            'repository':'fixture-nodes','source':'https://example.invalid/nodes.git','revision':'a'*40})
        self.assertNotEqual(changed['session'],r['session'])
        self.assertEqual(changed['capsule']['predecessor'],r['capsule']['sha256'])
        self.assertNotEqual(changed['review_id'],r['review_id'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})
        self.assertEqual(self.ui.get(r['session'])['manifest'],r['manifest'])
        child=self.ui.get(changed['session']);descriptor=read_json(child['package']/'capsule.json')
        self.assertEqual(descriptor['provenance']['source_records'][-1]['direct_source'],'https://example.invalid/original.rrpkg')
        self.assertNotIn('repository_info',child)  # A derivative is not the publisher archive.
        fresh=PackageUI(self.root,[self.models]);self.addCleanup(fresh.temporary.cleanup)
        self.assertEqual(fresh.status(changed['session'])['capsule'],changed['capsule'])
        self.assertEqual((child['package']/'workflow.json').read_bytes(),self.graph(node='MOSSFixture'))

    async def test_installed_receipt_observes_capsule_without_changing_it(self):
        from renderrepo.capsule import read_json
        r=await self.prepare(self.graph())
        folder=self.ui.get(r['session'])['package'];before=(folder/'capsule.json').read_bytes()
        status=await self.complete(r)
        self.assertEqual(read_receipts(self.root)[0]['capsule'],r['capsule'])
        self.assertEqual(status['capsule'],r['capsule'])
        self.assertEqual((folder/'capsule.json').read_bytes(),before)
        self.assertNotIn('status',read_json(folder/'capsule.json')['claims'])

class StorefrontHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_bridge_json_preview_exact_scope_approval_and_installed_workflow(self):
        from aiohttp import web,ClientSession
        from renderrepo.comfy_extension import register
        with tempfile.TemporaryDirectory() as t:
            routes=web.RouteTableDef();root=Path(t)
            folders=SimpleNamespace(base_path=t,folder_names_and_paths={},get_input_directory=lambda:str(root/'input'))
            ui=register(routes,folders);self.addCleanup(ui.temporary.cleanup)
            @routes.get('/object_info')
            async def nodes(request):return web.json_response({'SaveImage':{}})
            @routes.get('/system_stats')
            async def stats(request):return web.json_response({'system':{},'devices':[]})
            app=web.Application();app.add_routes(routes);runner=web.AppRunner(app);await runner.setup()
            site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession(headers={'X-RenderRepo-Request':'1'}) as client:
                    url='http://127.0.0.1:'+str(port)+'/renderrepo'
                    # Browser navigations cannot attach the API-only header. Static assets
                    # remain local/same-origin, while all data/mutation routes require it.
                    async with ClientSession() as browser:
                        for asset in ('index.html','app.js','adapter.js','style.css'):
                            asset_response=await browser.get(url+'/storefront/'+asset)
                            self.assertEqual(asset_response.status,200)
                        self.assertEqual((await browser.get(url+'/config')).status,403)
                        self.assertEqual((await browser.get(url+'/storefront/not-allowed')).status,400)
                        self.assertEqual((await browser.get(url+'/storefront/app.js',headers={'Origin':'https://other.invalid'})).status,403)
                    response=await client.post(url+'/preview',data=b'{"nodes":[{"id":1,"type":"SaveImage"}],"version":0.4}',headers={'X-RenderRepo-Filename':'ordinary.json'})
                    self.assertEqual(response.status,200);r=await response.json()
                    self.assertEqual(r['target_check']['state'],'available');self.assertTrue(r['can_install'])
                    rejected=await client.post(url+'/install/'+r['session'],json={'approved':True});self.assertEqual(rejected.status,400)
                    invalid=await client.post(url+'/install/'+r['session'],json={'approved':True,'review_id':r['review_id'],'confirmations':None});self.assertEqual(invalid.status,400)
                    accepted=await client.post(url+'/install/'+r['session'],json={'approved':True,'review_id':r['review_id']});self.assertEqual(accepted.status,200)
                    for _ in range(200):
                        response=await client.get(url+'/status/'+r['session']);state=await response.json()
                        if state['state']!='running':break
                        await asyncio.sleep(.02)
                    self.assertEqual(state['result']['exit_code'],0,state)
                    workflow=await client.get(url+'/workflow/'+r['session']);self.assertEqual(workflow.status,200)
                    self.assertEqual((await workflow.json())['nodes'][0]['type'],'SaveImage')
                    # Bundled previews are inspected without installation or arbitrary file access.
                    import io, zipfile
                    image=(Path(__file__).resolve().parents[1]/'examples/starter-catalog/rr-starter-base.png').read_bytes()
                    manifest={'schema_version':0,'name':'preview-only','workflow':'workflow.json',
                        'inputs':[{'filename':'example.png','kind':'image','mode':'bundled'}],
                        'artifacts':[{'filename':'example.png','target':'input/example.png',
                                      'sources':[{'uri':'package:assets/example.png'}]}]}
                    archive=io.BytesIO()
                    with zipfile.ZipFile(archive,'w') as z:
                        z.writestr('manifest.yaml',json.dumps(manifest));z.writestr('workflow.json','{"nodes":[{"id":1,"type":"SaveImage"}]}');z.writestr('assets/example.png',image)
                    response=await client.post(url+'/preview',data=archive.getvalue(),headers={'X-RenderRepo-Filename':'preview.rrpkg'})
                    self.assertEqual(response.status,200);preview=await response.json()
                    response=await client.get(url+'/example/'+preview['session']+'/0')
                    self.assertEqual(response.status,200);self.assertEqual(await response.read(),image)
                    self.assertFalse((root/'input/example.png').exists())
                    self.assertEqual((await client.get(url+'/example/'+preview['session']+'/-1')).status,400)
                    async with ClientSession() as anonymous:
                        self.assertEqual((await anonymous.get(url+'/example/'+preview['session']+'/0')).status,403)

            finally:await runner.cleanup()


class SelectedPythonTests(unittest.TestCase):
    def test_python_changes_are_bound_to_bridge_interpreter(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            ui=PackageUI(Path(d));self.addCleanup(ui.temporary.cleanup)
            self.assertEqual(ui.options({'python_dependencies':True})[-2:],['--comfy-python',sys.executable])
            ui.python='/some/other/environment/bin/python'
            with self.assertRaisesRegex(ValueError,'selected Comfy server interpreter'):
                ui.options({'python_dependencies':True})
            self.assertNotIn('--comfy-python',ui.options({}))
    def test_no_downloads_never_conceals_code_or_python_changes(self):
        with tempfile.TemporaryDirectory() as d:
            ui=PackageUI(Path(d));self.addCleanup(ui.temporary.cleanup)
            result=ui.store_scope({'plan':{'scope':{'nodes':[{'name':'owned-fixture','operation':'install'}],'python':{'requirements':['tiny']}}}})['store_scope']
            self.assertIn('custom-node code',result['summary']);self.assertIn('Change Python packages',result['summary'])
            self.assertEqual([d['id'] for d in result['decisions']],['code','python'])
