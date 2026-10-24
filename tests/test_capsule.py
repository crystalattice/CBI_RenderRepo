"""Portable identity/revision rules; target observations stay installation-local."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from renderrepo.archive import package_directory, write_archive
from renderrepo.capsule import (canonical, copy_capsule, describe, identity, parent_descriptor,
                                read_json, seal, verify, PAYLOAD_DOMAIN, CAPSULE_DOMAIN)
from renderrepo.cli import load_package, main


class CapsuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.package=self.root/'legacy';self.package.mkdir()
        self.manifest={'schema_version':0,'name':'sample','version':'1','workflow':'workflow.json','artifacts':[]}
        self.graph=b'{"nodes":[{"id":1,"type":"SaveImage"}]}\n'
        (self.package/'manifest.yaml').write_text(json.dumps(self.manifest))
        (self.package/'workflow.json').write_bytes(self.graph)
        (self.package/'supplement.txt').write_bytes(b'preserve supplementary bytes\n')

    def cli(self,*args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(map(str,args)))

    def test_legacy_import_and_explicit_migration_preserve_all_data(self):
        before={p.name:p.read_bytes() for p in self.package.iterdir()}
        self.assertEqual(load_package(self.package),self.manifest)
        self.assertFalse((self.package/'capsule.json').exists())
        archive=self.root/'migrated.rrpkg'
        self.assertEqual(self.cli('capsule',self.package,'--output',archive),0)
        with package_directory(archive) as pkg:
            self.assertEqual(load_package(pkg),self.manifest)
            for name,data in before.items():self.assertEqual((pkg/name).read_bytes(),data)
            capsule=read_json(pkg/'capsule.json')
            self.assertEqual((capsule['format'],capsule['profile'],capsule['schema_version']),('s4-capsule','renderrepo',1))
            self.assertIsNone(capsule['predecessor']);self.assertIsNone(capsule['s4'])
            self.assertEqual(capsule,parent_descriptor(self.package))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.package.iterdir()})

    def test_hash_bytes_fields_and_non_self_reference(self):
        self.assertEqual(canonical({'b':[1,None],'a':'é'}),b'{"a":"\\u00e9","b":[1,null]}')
        descriptor=seal(self.package,self.manifest)
        self.assertEqual(descriptor['payload_sha256'],hashlib.sha256(PAYLOAD_DOMAIN+canonical(descriptor['files'])).hexdigest())
        body={k:v for k,v in descriptor.items() if k!='sha256'}
        self.assertEqual(descriptor['sha256'],hashlib.sha256(CAPSULE_DOMAIN+canonical(body)).hexdigest())
        self.assertNotIn('capsule.json',[f['path'] for f in descriptor['files']])
        self.assertEqual([f['path'] for f in descriptor['files']],['manifest.yaml','supplement.txt','workflow.json'])
        for entry in descriptor['files']:
            data=(self.package/entry['path']).read_bytes()
            self.assertEqual((entry['size_bytes'],entry['sha256']),(len(data),hashlib.sha256(data).hexdigest()))
        # Object formatting/order is irrelevant; exact payload formatting is significant.
        (self.package/'capsule.json').write_text(json.dumps(dict(reversed(list(descriptor.items()))),indent=4))
        self.assertEqual(verify(self.package,self.manifest),descriptor)
        with self.assertRaises(FileExistsError):seal(self.package,self.manifest)
        (self.package/'workflow.json').write_bytes(self.graph+b' ')
        with self.assertRaisesRegex(ValueError,'mismatch'):load_package(self.package)

    def test_zip_container_order_timestamps_and_compression_do_not_change_identity(self):
        expected=seal(self.package,self.manifest)
        archives=[]
        for number,compression in enumerate((zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)):
            archive=self.root/f'{number}.rrpkg';archives.append(archive)
            with zipfile.ZipFile(archive,'w',compression=compression) as z:
                for p in sorted(self.package.iterdir(),reverse=bool(number)):
                    item=zipfile.ZipInfo(p.name,date_time=(2020+number,1,1,0,0,0));item.compress_type=compression
                    z.writestr(item,p.read_bytes())
            with package_directory(archive) as pkg:
                self.assertEqual(verify(pkg,load_package(pkg))['sha256'],expected['sha256'])
        self.assertNotEqual(archives[0].read_bytes(),archives[1].read_bytes())

    def test_unknown_identity_schema_and_reserved_s4_values_rejected(self):
        descriptor=seal(self.package,self.manifest)
        for key,value in [('format','other'),('profile','other'),('schema_version',2),('schema_version',True),('s4',{}),('extra',None)]:
            with self.subTest(key=key,value=value):
                (self.package/'capsule.json').write_text(json.dumps({**descriptor,key:value}))
                with self.assertRaises(ValueError):load_package(self.package)
        (self.package/'capsule.json').write_text('{"profile":"renderrepo","profile":"other"}')
        with self.assertRaisesRegex(ValueError,'Duplicate'):load_package(self.package)
        (self.package/'capsule.json').write_text('{"value":NaN}')
        with self.assertRaisesRegex(ValueError,'Non-finite'):load_package(self.package)
        with self.assertRaises(ValueError):canonical({'bad':float('inf')})

    def test_linked_revision_preserves_parent_and_source_provenance(self):
        parent=seal(self.package,self.manifest,provenance={'repository':{'name':'source','index_uri':'file:///catalog/index.json'}})
        before={p.name:p.read_bytes() for p in self.package.iterdir()}
        def edit(folder):
            manifest=load_package(folder)
            manifest['description']='a new declaration'
            (folder/'manifest.yaml').write_text(json.dumps(manifest))
        output=copy_capsule(self.package,self.root/'revision',edit=edit)
        child=verify(output,load_package(output))
        self.assertEqual(child['predecessor'],parent['sha256']);self.assertNotEqual(child['sha256'],parent['sha256'])
        self.assertEqual(child['provenance'],parent['provenance'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.package.iterdir()})
        self.assertEqual((output/'supplement.txt').read_bytes(),before['supplement.txt'])
        with self.assertRaisesRegex(ValueError,'already exists'):copy_capsule(self.package,output)
        with self.assertRaisesRegex(ValueError,'outside'):copy_capsule(self.package,self.package/'nested')

    def test_payload_membership_is_closed_and_symlinks_refused(self):
        seal(self.package,self.manifest)
        extra=self.package/'extra';extra.write_bytes(b'not sealed')
        with self.assertRaises(ValueError):load_package(self.package)
        extra.unlink();(self.package/'supplement.txt').unlink()
        with self.assertRaises(ValueError):load_package(self.package)
        (self.package/'supplement.txt').symlink_to(self.package/'workflow.json')
        with self.assertRaisesRegex(ValueError,'symlink'):load_package(self.package)

    def test_authoring_observations_are_not_claims_or_current_target_findings(self):
        self.manifest['runtime']={'compatibility':'unverified',
          'requirements':{'device_types':['cpu']},'local_installation':{'comfy':{'revision':'a'*40,'dirty':False,'source':'https://example.test/comfy.git'},'custom_nodes':[]}}
        (self.package/'manifest.yaml').write_text(json.dumps(self.manifest))
        descriptor=seal(self.package,self.manifest)
        self.assertNotIn('compatibility',descriptor['claims']['runtime'])
        self.assertNotIn('local_installation',descriptor['claims']['runtime'])
        self.assertEqual(descriptor['observations']['authoring_runtime'],self.manifest['runtime']['local_installation'])
        self.assertEqual(load_package(self.package),self.manifest)
        # Authoring observation cannot be replaced with a receiving-target result.
        changed=json.loads(json.dumps(descriptor));changed['claims']['runtime']['installed']=True
        (self.package/'capsule.json').write_text(json.dumps(changed))
        with self.assertRaises(ValueError):load_package(self.package)

    def test_new_pack_json_export_revision_link_and_legacy_import(self):
        graph=self.root/'source.json';graph.write_bytes(self.graph)
        first=self.root/'first.rrpkg';second=self.root/'second.rrpkg'
        self.assertEqual(self.cli('pack',graph,'--output',first),0)
        with package_directory(first) as pkg:
            manifest=json.loads((pkg/'manifest.yaml').read_text())  # New manifest bytes are JSON.
            parent=verify(pkg,manifest)
            legacy=self.root/'legacy.rrpkg'
            with zipfile.ZipFile(legacy,'w') as z:
                for entry in parent['files']:z.writestr(entry['path'],(pkg/entry['path']).read_bytes())
        with package_directory(legacy) as pkg:self.assertEqual(load_package(pkg),manifest)
        original=first.read_bytes();graph.write_bytes(self.graph+b' ')
        self.assertEqual(self.cli('pack',graph,'--output',second,'--predecessor',first),0)
        with package_directory(second) as pkg:
            child=verify(pkg,load_package(pkg));self.assertEqual(child['predecessor'],parent['sha256'])
        self.assertEqual(first.read_bytes(),original)
        self.assertEqual(self.cli('pack',graph,'--output',first,'--predecessor',second),2)

    def test_legacy_qualification_fields_not_promoted_by_migration(self):
        self.manifest['runtime']={'compatibility':'tested-good'}
        (self.package/'manifest.yaml').write_text(json.dumps(self.manifest))
        self.assertEqual(load_package(self.package),self.manifest)
        with self.assertRaisesRegex(ValueError,'compatibility'):copy_capsule(self.package,self.root/'migrated')
        self.assertFalse((self.root/'migrated').exists())

    def test_install_metadata_only_revision_requires_receipt_aware_removal(self):
        from renderrepo.receipts import read_receipts
        comfy=self.root/'Comfy';comfy.mkdir()
        first=copy_capsule(self.package,self.root/'first')
        self.assertEqual(self.cli('install',first,'--comfy-dir',comfy,'--yes'),0)
        before=read_receipts(comfy)
        def edit(folder):
            manifest=load_package(folder);manifest['description']='Changed declaration, same graph/version'
            (folder/'manifest.yaml').write_text(json.dumps(manifest))
        second=copy_capsule(first,self.root/'second',edit=edit)
        self.assertEqual(self.cli('install',second,'--comfy-dir',comfy,'--yes'),2)
        self.assertEqual(before,read_receipts(comfy))
        archive=self.root/'one.rrpkg';write_archive(first,archive)
        with self.assertRaises(FileExistsError):write_archive(second,archive)

    def test_published_json_schema(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('Optional schema conformance check requires jsonschema.')
        schema=read_json(Path(__file__).resolve().parents[1]/'renderrepo/schemas/s4-capsule-v1.json')
        Draft202012Validator.check_schema(schema)
        validator=Draft202012Validator(schema)
        descriptor=seal(self.package,self.manifest)
        validator.validate(descriptor)
        for change in ({'schema_version':True},{'schema_version':2},{'s4':{}},{'unknown':None}):
            self.assertTrue(list(validator.iter_errors({**descriptor,**change})))

    def test_starter_catalog_supplementary_examples_are_bound_revisions(self):
        from scripts.build_starter_catalog import build
        output=self.root/'catalog'
        with contextlib.redirect_stdout(io.StringIO()):build(output)
        for archive in output.glob('*.rrpkg'):
            with package_directory(archive) as folder:
                descriptor=verify(folder,load_package(folder))
                self.assertIsNotNone(descriptor['predecessor'])
                self.assertTrue(any(f['path'].startswith('examples/') for f in descriptor['files']))

    def test_unknown_target_state_in_declarations_refused(self):
        self.manifest['runtime']={'node_availability':{'SaveImage':'installed'}}
        with self.assertRaisesRegex(ValueError,'target state'):describe(self.package,self.manifest)
        self.manifest['runtime']={'node_packages':{}}
        with self.assertRaisesRegex(ValueError,'node_packages'):describe(self.package,self.manifest)
