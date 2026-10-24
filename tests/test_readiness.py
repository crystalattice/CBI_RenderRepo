import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from renderrepo.readiness import inventory, evaluate, version_matches, snapshot, validate_inventory
from renderrepo.cli import main
from renderrepo.comfy_extension import PackageUI
from renderrepo.receipts import save_receipt


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'input').mkdir()
        self.graph = {'1': {'class_type':'LoadImage', 'inputs':{'image':'test.png'}}}
        self.manifest = {'schema_version':0,'name':'ready-test','workflow':'workflow.json',
                         'artifacts':[], 'inputs':[{'filename':'test.png','kind':'image','mode':'user'}]}
        self.observed = {'observed_at':'2026-10-17T00:00:00+00:00','server':'http://127.0.0.1:1',
                         'nodes':{'LoadImage':{'input':{'required':{'image':[['test.png'],{}]}}}},
                         'system':{'system':{'python_version':'3.13.1 (build)', 'comfyui_version':'0.39.0',
                                             'pytorch_version':'2.13.0+cu130'}, 'devices':[{'type':'cpu'}]}}

    def report(self):
        return evaluate(inventory(self.manifest,self.graph), self.observed,self.root)

    def test_ready_and_provenance_is_not_requirement(self):
        self.manifest['runtime'] = {'node_packages':[{'id':'comfy-core','recorded_version':'ancient'}],
                                    'local_installation':{'comfy':{'revision':'old'},'custom_nodes':[]}}
        self.assertEqual(self.report()['state'],'ready')
        self.assertIn('not execution', self.report()['limitation'])

    def test_missing_nodes_and_inputs(self):
        self.observed['nodes'] = {'LoadImage':{'input':{'required':{'image':[[],{}]}}}}
        self.assertEqual(self.report()['state'],'missing')
        self.observed['nodes'] = {}
        self.assertEqual(self.report()['state'],'missing')

    def test_model_visibility_dynamic_and_opaque(self):
        self.graph = {'1':{'class_type':'UNETLoader','inputs':{'unet_name':'test.safetensors'}}}
        self.manifest['inputs']=[]
        self.observed['nodes']={'UNETLoader':{'input':{'required':{'unet_name':[['other'],{}]}}}}
        self.assertEqual(self.report()['state'],'missing')
        self.graph['1']['inputs']['unet_name']=['2',0]
        self.assertEqual(self.report()['state'],'unknown')
        self.graph['1']['inputs']['unet_name']='test.safetensors'
        self.observed['nodes']['UNETLoader']['input']['required']['unet_name']=['COMBO',{}]
        self.assertEqual(self.report()['state'],'unknown')

    def test_hash_mismatch_no_hash_missing_and_match(self):
        data=b'original'; file=self.root/'input/test.png'; file.write_bytes(data)
        self.manifest['artifacts']=[{'filename':'test.png','target':'input/test.png','sha256':hashlib.sha256(data).hexdigest()}]
        self.assertEqual(self.report()['state'],'ready')
        file.write_bytes(b'changed')
        self.assertEqual(self.report()['state'],'incompatible')
        del self.manifest['artifacts'][0]['sha256']
        self.assertEqual(self.report()['state'],'unknown')
        file.unlink()
        self.assertEqual(self.report()['state'],'missing')

    def test_visibility_does_not_prove_artifact_identity(self):
        self.manifest['artifacts']=[{'filename':'test.png','sha256':'0'*64}]
        report=evaluate(inventory(self.manifest,self.graph),self.observed)
        self.assertEqual(report['state'],'unknown')

    def test_explicit_versions_devices_unknown_keys_and_prerelease(self):
        self.manifest['runtime']={'requirements':{'python_version':'>=3.13,<4','comfyui_version':'>=0.39','device_types':['cpu']}}
        self.assertEqual(self.report()['state'],'ready')
        self.manifest['runtime']['requirements']['device_types']=['cuda']
        self.assertEqual(self.report()['state'],'incompatible')
        self.manifest['runtime']['requirements']={'python_version':'>=99'}
        self.assertEqual(self.report()['state'],'incompatible')
        self.manifest['runtime']['requirements']={'cuda_version':'>=12'}
        self.assertEqual(self.report()['state'],'unknown')
        self.assertIsNone(version_matches('0.39.0rc1','>=0.39'))
        self.assertIsNone(version_matches('0.39.0','~=0.39'))
        self.assertTrue(version_matches('2.13.0+cu130','>=2.13,<3'))

    def test_unavailable_endpoints_and_no_mutations(self):
        with patch('renderrepo.readiness.capability_json',side_effect=OSError('offline')) as get:
            observed=snapshot('http://127.0.0.1:1')
        self.assertEqual([c.args[1] for c in get.call_args_list],['/object_info','/system_stats'])
        self.assertEqual(evaluate(inventory(self.manifest,self.graph),observed)['state'],'unknown')
        self.assertEqual(evaluate(None,self.observed)['state'],'unknown')
        self.assertEqual(list(self.root.iterdir()),[self.root/'input'])

    def test_custom_node_revision_missing_unknown_mismatch_match(self):
        self.manifest['runtime']={'custom_nodes':[{'name':'tiny','source':'https://example.org/tiny.git','revision':'a'*40}]}
        dep=inventory(self.manifest,self.graph)
        self.assertEqual(evaluate(dep,self.observed)['state'],'unknown')
        self.assertEqual(self.report()['state'],'missing')
        path=self.root/'custom_nodes/tiny';path.mkdir(parents=True)
        for revision,dirty,state in [('b'*40,False,'incompatible'),('a'*40,True,'unknown'),('a'*40,False,'ready')]:
            with patch('renderrepo.readiness.git_provenance',return_value={'revision':revision,'dirty':dirty}):
                self.assertEqual(self.report()['state'],state)

    def test_cli_json_and_exit_states(self):
        import yaml
        package=self.root/'package';package.mkdir()
        (package/'workflow.json').write_text(json.dumps(self.graph))
        for spec,code,state in [({},0,'ready'),({'python_version':'>=99'},4,'incompatible'),({'cuda_version':'>=12'},3,'unknown')]:
            self.manifest['runtime']={'requirements':spec}
            (package/'manifest.yaml').write_text(yaml.safe_dump(self.manifest))
            out=io.StringIO()
            with patch('renderrepo.readiness.snapshot',return_value=self.observed),contextlib.redirect_stdout(out):
                result=main(['readiness',str(package),'--comfy-url',self.observed['server'],'--json'])
            self.assertEqual(result,code);self.assertEqual(json.loads(out.getvalue())['state'],state)

    def test_installed_receipt_evidence_and_modified_and_legacy(self):
        graph=self.root/'workflow.json'; graph.write_text(json.dumps(self.graph))
        receipt={'receipt_version':1,'name':'test','version':'1','status':'installed','comfy_root':str(self.root),
                 'workflow_path':str(graph),'workflow_sha256':hashlib.sha256(graph.read_bytes()).hexdigest(),
                 'artifacts':[],'managed':[],'dependency_inventory':inventory(self.manifest,self.graph)}
        save_receipt(self.root,receipt)
        ui=PackageUI(self.root);self.addCleanup(ui.temporary.cleanup)
        before=ui.readiness({'installed':'test'},self.observed)
        self.assertEqual(before['state'],'ready')
        self.assertIn('capabilities',before)
        graph.write_text('{}')
        changed=ui.readiness({'installed':'test'},self.observed)
        self.assertEqual(changed['state'],'unknown')
        self.assertNotEqual(changed['capabilities']['workflow_sha256'],before['capabilities']['workflow_sha256'])
        self.assertTrue(changed['capabilities']['uncertainties'])
        del receipt['dependency_inventory']; save_receipt(self.root,receipt)
        self.assertEqual(ui.readiness({'installed':'test'},self.observed)['state'],'unknown')
        graph.unlink()
        self.assertEqual(ui.readiness({'installed':'test'},self.observed)['state'],'missing')

    def test_catalog_identity_and_legacy_without_retrieval(self):
        ui=PackageUI(self.root);self.addCleanup(ui.temporary.cleanup)
        item={'name':'test','version':'1','sha256':'a'*64,'repository':'local', 'dependency_inventory':inventory(self.manifest,self.graph)}
        values={'name':'test','version':'1','sha256':'a'*64,'source':'local'}
        with patch.object(ui,'repositories',return_value={'packages':[item]}):
            self.assertEqual(ui.readiness(values,self.observed)['state'],'ready')
            del item['dependency_inventory']
            self.assertEqual(ui.readiness(values,self.observed)['state'],'unknown')
            with self.assertRaises(ValueError): ui.readiness(dict(values,version='2'),self.observed)

    def test_graph_inputs_are_checked_even_if_manifest_omits_them(self):
        del self.manifest['inputs']
        self.observed['nodes']['LoadImage']['input']['required']['image']=[[],{}]
        self.assertEqual(self.report()['state'],'missing')
        self.graph['1']['inputs']['image']=['2',0]
        self.assertEqual(self.report()['state'],'unknown')

    def test_unreadable_files_and_malformed_server_evidence_are_unknown(self):
        file=self.root/'input/test.png';file.write_bytes(b'input')
        self.manifest['artifacts']=[{'filename':'test.png','target':'input/test.png','sha256':'0'*64}]
        with patch('renderrepo.readiness.hashlib.file_digest',side_effect=PermissionError('unreadable')):
            self.assertEqual(self.report()['state'],'unknown')
        with patch('renderrepo.readiness.capability_json',return_value={'bad':'schema'}):
            data=snapshot('http://127.0.0.1:1')
        self.assertIsNone(data['nodes']);self.assertIsNone(data['system'])

    def test_pack_requirements_and_index_inventory_roundtrip(self):
        import zipfile
        import yaml
        from renderrepo.repository import validate_index
        workflow=self.root/'graph.json';workflow.write_text(json.dumps(self.graph))
        requirements=self.root/'requirements.json';requirements.write_text('{"python_version":">=3.13"}')
        publisher=self.root/'publisher';publisher.mkdir()
        archive=publisher/'test.rrpkg'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['pack',str(workflow),'--output',str(archive),'--runtime-requirements',str(requirements)]),0)
            self.assertEqual(main(['index',str(publisher)]),0)
        with zipfile.ZipFile(archive) as package:
            manifest=yaml.safe_load(package.read('manifest.yaml'))
        self.assertEqual(manifest['runtime']['requirements'],{'python_version':'>=3.13'})
        index=json.loads((publisher/'index.json').read_text())
        items=validate_index(index,'http://127.0.0.1:1/index.json')
        self.assertEqual(items[0]['dependency_inventory']['runtime']['requirements'],{'python_version':'>=3.13'})
        self.assertEqual(items[0]['dependency_inventory']['inputs'][0]['filename'],'test.png')

    def test_invalid_metadata(self):
        dep=inventory(self.manifest,self.graph)
        dep['artifacts']=[{'filename':'escape','target':'input/../escape'}]
        with self.assertRaises(ValueError):validate_inventory(dep)
