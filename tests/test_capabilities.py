"""Static fixtures only. None of these generative-looking graphs are executed."""
import copy
import json
from pathlib import Path
import unittest
from renderrepo.capabilities import analyze, resolve_summary, validate_summary


def api_node(typ, **inputs):
    return {'class_type': typ, 'inputs': inputs}


def edit_graph(references=1, negative=False):
    g={'1':api_node('LoadImage',image='owned.png'),
       '2':api_node('TextEncodeQwenImageEdit' if references==1 else 'TextEncodeQwenImageEditPlus',
                    prompt='Make the procedural square blue', **({'image':['1',0]} if references==1 else {'image1':['1',0],'image2':['7',0]})),
       '3':api_node('EmptyLatentImage',width=256,height=256,batch_size=1),
       '4':api_node('KSampler',positive=['2',0],latent_image=['3',0],seed=1,steps=4,cfg=1,denoise=1),
       '5':api_node('VAEDecode',samples=['4',0]),'6':api_node('SaveImage',images=['5',0],filename_prefix='NOT A PROMPT')}
    if references>1:g['7']=api_node('LoadImage',image='reference.png')
    if negative:g['8']=api_node('CLIPTextEncode',text='blur');g['4']['inputs']['negative']=['8',0]
    return g


def editor(g):
    nodes,links=[],[]
    for i,n in g.items():
        ports=[];widgets={}
        for name,v in n['inputs'].items():
            if isinstance(v,list) and len(v)==2 and str(v[0]) in g:
                lid=len(links)+1;links.append([lid,int(v[0]),v[1],int(i),len(ports),'*']);ports.append({'name':name,'type':'*','link':lid})
            else: widgets[name]=v
        nodes.append({'id':int(i),'type':n['class_type'],'inputs':ports,'outputs':[{'name':'out','type':'*','links':[]}], 'widgets_values':widgets,'mode':0})
    return {'nodes':nodes,'links':links,'version':0.4}


class CapabilityTests(unittest.TestCase):
    def test_single_image_instruction_and_optional_socket(self):
        c=analyze(edit_graph());self.assertEqual(c['input_counts']['image'],{'min':1,'max':1,'connected':1})
        self.assertEqual(c['prompts'][0]['label'],'Editing instructions');self.assertIn('edit-image',c['actions']);self.assertTrue(any(x['label']=='Output width' for x in c['controls']))
        self.assertTrue(any('Automatic reference resizing' in p['label'] and not p['exposed'] for p in c['processing']))
    def test_multiple_images_and_traceable_negative(self):
        c=analyze(edit_graph(2,True));self.assertEqual(c['input_counts']['image']['min'],2)
        self.assertEqual([p['role'] for p in c['prompts']],['positive','negative'])
        self.assertEqual([i['slots'] for i in c['inputs'] if i['requirement']=='optional'],[1])
    def test_roles_not_inferred_from_text_order(self):
        g={'1':api_node('CLIPTextEncode',text='first'),'2':api_node('CLIPTextEncode',text='second'),
           '3':api_node('ConditioningCombine',conditioning_1=['1',0],conditioning_2=['2',0]),
           '4':api_node('MysteryPainter',conditioning=['3',0]),'5':api_node('SaveImage',images=['4',0])}
        c=analyze(g);self.assertEqual([p['role'] for p in c['prompts']],['unknown','unknown'])
        self.assertEqual(c['actions'],[]);self.assertTrue(any('MysteryPainter' in u for u in c['uncertainties']))
    def test_disconnected_copy_is_not_editing(self):
        g={'1':api_node('LoadImage',image='one.png'),'2':api_node('SaveImage',images=['1',0]),'3':api_node('LoadImage',image='unused.png')}
        c=analyze(g);self.assertEqual(c['input_counts']['image']['min'],1);self.assertEqual(c['actions'],[])
    def test_optional_static_and_dynamic_branches(self):
        g={'1':api_node('LoadImage',image='one.png'),'2':api_node('LoadImage',image='two.png'),
           '3':api_node('ComfySwitchNode',switch=True,on_true=['1',0],on_false=['2',0]),'4':api_node('SaveImage',images=['3',0])}
        c=analyze(g);self.assertEqual([i['node'] for i in c['inputs']],['1'])
        g['3']['inputs']['switch']=['5',0];g['5']=api_node('UnmappedBoolean',value=True)
        c=analyze(g);self.assertEqual(c['input_counts']['image']['max'],None)
        self.assertTrue(any('Mode-dependent switch' in u for u in c['uncertainties']))
    def test_internal_and_exposed_resize(self):
        g=edit_graph();g['7']=api_node('ImageScale',image=['1',0],width=256,height=256,crop='center',upscale_method='area');g['2']['inputs']['image']=['7',0]
        c=analyze(g);self.assertFalse(any(x['node']=='7' for x in c['controls']));self.assertTrue(any(p['node']=='7' and not p['exposed'] for p in c['processing']))
        g['8']=api_node('ImageScaleBy',image=['5',0],scale_by=2,upscale_method='nearest-exact');g['6']['inputs']['images']=['8',0]
        c=analyze(g);self.assertTrue(any(x['node']=='8' and x['field']=='scale_by' for x in c['controls']))
    def test_editor_equivalent_and_muted_bypass(self):
        g={'1':api_node('LoadImage',image='one.png'),'2':api_node('ImageScale',image=['1',0],width=30,height=40), '3':api_node('SaveImage',images=['2',0])}
        e=editor(g);self.assertEqual(analyze(g)['actions'],analyze(e)['actions'])
        e['nodes'][1]['mode']=4;c=analyze(e);self.assertEqual(c['actions'],[]);self.assertEqual(c['input_counts']['image']['min'],1)
        e['nodes'][1]['mode']=2;c=analyze(e);self.assertEqual(c['inputs'],[]);self.assertTrue(c['uncertainties'])
    def test_nested_subgraphs_resolve_only_used_boundary(self):
        inner={'id':'inner','nodes':[{'id':1,'type':'ImageScale','inputs':[{'name':'image','link':1}], 'widgets_values':['area',64,64,'center']}],
               'links':[[1,-10,0,1,0,'IMAGE'],[2,1,0,-20,0,'IMAGE']]}
        middle={'id':'middle','nodes':[{'id':1,'type':'inner','inputs':[{'name':'image','link':1}]}],
                'links':[[1,-10,0,1,0,'IMAGE'],[2,1,0,-20,0,'IMAGE']]}
        g=editor({'1':api_node('LoadImage',image='owned.png'),'2':api_node('middle',image=['1',0]),'3':api_node('SaveImage',images=['2',0]),'4':api_node('LoadImage',image='unused.png')})
        g['definitions']={'subgraphs':[inner,middle]};c=analyze(g)
        self.assertEqual(c['input_counts']['image']['min'],1);self.assertIn('resize-crop',c['actions']);self.assertEqual(c['uncertainties'],[])
    def test_unknown_custom_node_and_no_supported_output(self):
        g={'1':api_node('LoadImage',image='owned.png'),'2':api_node('OpaqueNode',image=['1',0]),'3':api_node('SaveImage',images=['2',0])}
        c=analyze(g);self.assertIsNone(c['input_counts']['image']['max']);self.assertEqual(c['inputs'][0]['requirement'],'unknown')
        self.assertTrue(analyze({'1':g['1']})['uncertainties'])
    def test_model_free_starters_and_stale_digest(self):
        for filename,count,action in [('resize',1,'resize-crop'),('portrait-crop',1,'resize-crop'),('overlay',2,'combine-images')]:
            g=json.loads((Path(__file__).parents[1]/f'examples/starter-catalog/rr-starter-{filename}.json').read_text());c=analyze(g)
            validate_summary(c);self.assertIn(action,c['actions']);self.assertEqual(c['input_counts']['image']['min'],count)
            changed=copy.deepcopy(g);changed['nodes'][0]['widgets_values'][0]='another.png';new=resolve_summary(changed,c)
            self.assertNotEqual(new['workflow_sha256'],c['workflow_sha256']);self.assertIn('stale',new['uncertainties'][-1])
    def test_legacy_missing_summary_still_loads(self):
        from renderrepo.cli import load_package
        import tempfile,yaml
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'workflow.json').write_text(json.dumps(edit_graph()))
            (root/'manifest.yaml').write_text(yaml.safe_dump({'schema_version':0,'name':'legacy','workflow':'workflow.json','artifacts':[]}))
            self.assertNotIn('capabilities',load_package(root))


class CapabilityMetadataTests(unittest.TestCase):
    def test_catalog_generation_recomputes_even_same_digest_publisher_facts(self):
        import tempfile,yaml
        from types import SimpleNamespace
        from renderrepo.archive import write_archive
        from renderrepo.repository import build_index,validate_index
        g=edit_graph();old=analyze(g);old['purpose']='Unsupported publisher claim';old['actions']=['create-video']
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);pkg=root/'package';pkg.mkdir();(pkg/'workflow.json').write_text(json.dumps(g))
            (pkg/'manifest.yaml').write_text(yaml.safe_dump({'schema_version':0,'name':'demo','workflow':'workflow.json','artifacts':[],'capabilities':old}))
            write_archive(pkg,root/'demo.rrpkg');build_index(SimpleNamespace(directory=root,output=None))
            index=json.loads((root/'index.json').read_text());item=index['packages'][0]
            self.assertEqual(item['capabilities']['purpose'],'Reference-guided image editing');self.assertNotIn('create-video',item['capabilities']['actions'])
            validate_index(index,(root/'index.json').as_uri())
    def test_author_actions_are_explicit_stable_metadata(self):
        from renderrepo.discovery import validate_discovery
        self.assertEqual(validate_discovery({'actions':['edit-image','resize-crop']})['actions'],['edit-image','resize-crop'])
        with self.assertRaises(ValueError):validate_discovery({'actions':['face-swap']})
    def test_instantiated_but_unused_subgraph_does_not_add_inputs(self):
        g=editor({'1':api_node('LoadImage',image='visible.png'),'2':api_node('SaveImage',images=['1',0]),'3':api_node('unused')})
        g['definitions']={'subgraphs':[{'id':'unused','nodes':[{'id':10,'type':'LoadImage','widgets_values':['hidden.png']}], 'links':[[1,10,0,-20,0,'IMAGE']]}]}
        self.assertEqual(analyze(g)['input_counts']['image']['min'],1)
    def test_unknown_subgraph_overrides_do_not_reuse_inner_crop_setting(self):
        sub={'id':'sub','nodes':[{'id':1,'type':'ImageScale','inputs':[{'name':'image','link':1}], 'widgets_values':['area',64,64,'center']}], 'links':[[1,-10,0,1,0,'IMAGE'],[2,1,0,-20,0,'IMAGE']]}
        g=editor({'1':api_node('LoadImage',image='owned.png'),'2':api_node('sub',image=['1',0]),'3':api_node('SaveImage',images=['2',0])})
        g['definitions']={'subgraphs':[sub]};g['nodes'][1]['widgets_values']=['disabled'];c=analyze(g)
        self.assertTrue(any('overrides' in u for u in c['uncertainties']));self.assertFalse(any('Center crop' in p['label'] for p in c['processing']))

class ImportedSubgraphRegressionTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(__file__).parent/'fixtures/image_z_image_turbo.json'
        self.graph = json.loads(self.source.read_bytes())
        self.schema = json.loads((self.source.parent/'comfy_analysis_object_info.json').read_text())

    def analyze(self):
        return analyze(self.graph, node_schema=self.schema)

    def test_real_json_purpose_prompt_output_and_processing(self):
        original = self.source.read_bytes()
        c = self.analyze()
        self.assertEqual(c['purpose'], 'Generate an image from a text prompt using Z-Image-Turbo')
        self.assertEqual(c['input_counts']['image'], {'min':0,'max':0,'connected':0})
        self.assertEqual(c['inputs'], [])
        self.assertEqual([p['role'] for p in c['prompts']], ['positive'])
        self.assertIn('/object_info', c['prompts'][0]['role_evidence'])
        self.assertIn('no separate negative-prompt', c['conditioning'][0]['label'])
        self.assertEqual(c['outputs'], ['image'])
        self.assertEqual(c['output_details'][0]['filename_prefix'], 'z-image-turbo')
        self.assertEqual([p['label'] for p in c['processing']],
                         ['Text encoding','Empty latent','Sampling','VAE decoding','Image saving'])
        self.assertEqual(c['actions'], ['create-image'])
        self.assertEqual(c['uncertainties'], [])
        self.assertEqual(c['limitations'], ['Graph analysis does not establish execution, hardware suitability or performance.'])
        self.assertEqual(self.source.read_bytes(), original)
        validate_summary(c)

    def test_outer_controls_and_selected_models_override_inner_defaults_and_notes(self):
        from renderrepo.pack import discover
        g = self.graph
        inner = g['definitions']['subgraphs'][0]['nodes']
        for n in inner:
            if n['type']=='EmptySD3LatentImage':
                n['widgets_values']=[64,96,1];n['widgets_values_named']={'width':64,'height':96,'batch_size':1}
            if n['type']=='KSampler':
                n['widgets_values'][0]=0;n['widgets_values'][2]=99
                n['widgets_values_named'].update(seed=0,steps=99)
        c = self.analyze()
        controls = {x['field']:x for x in c['controls']}
        self.assertEqual(set(controls), {'width','height','seed','steps','unet_name','clip_name','vae_name'})
        self.assertEqual(controls['width']['value'],1024)
        self.assertEqual(controls['height']['value'],1024)
        self.assertEqual(controls['steps']['value'],8)
        self.assertEqual(controls['seed']['value'],196112838694424)
        self.assertTrue(all(x['exposed_at']['node']=='57' for x in controls.values()))
        filenames = {'z_image_turbo_int8_convrot.safetensors','qwen_3_4b_fp4_flux2.safetensors','ae.safetensors'}
        self.assertEqual({m['filename'] for m in c['models']},filenames)
        artifacts, _, _, _ = discover(g)
        self.assertEqual({a['filename'] for a in artifacts},filenames)
        self.assertTrue(all(not a['sources'] for a in artifacts if a['filename']!='ae.safetensors'))
        instance = g['nodes'][-1]
        instance['widgets_values_named']['width']=768
        instance['widgets_values'][1]=2048
        self.assertEqual(next(x['value'] for x in self.analyze()['controls'] if x['field']=='width'),768)

    def test_effective_prompt_is_verbatim_outer_value_not_inner_default(self):
        original = self.source.read_bytes()
        outer = self.graph['nodes'][-1]['widgets_values_named']['text']
        prompt = self.analyze()['prompts'][0]
        self.assertEqual(prompt['value'],outer)
        self.assertEqual(prompt['value_source'],{'node':'57','field':'text'})
        inner = next(n for n in self.graph['definitions']['subgraphs'][0]['nodes'] if n['type']=='CLIPTextEncode')
        inner['widgets_values']=['Conflicting inner default; never display this.']
        inner['widgets_values_named']['text']='Conflicting inner default; never display this.'
        self.assertEqual(self.analyze()['prompts'][0]['value'],outer)
        self.assertEqual(self.source.read_bytes(),original)

    def test_missing_or_dynamic_prompt_does_not_expose_default(self):
        self.graph['nodes'][-1]['widgets_values_named']['text']=''
        self.assertEqual(self.analyze()['prompts'][0]['value'],'')
        g={'1':api_node('ExternalText'),
           '2':api_node('CLIPTextEncode',text=['1',0]),
           '3':api_node('KSampler',positive=['2',0]),
           '4':api_node('VAEDecode',samples=['3',0]),
           '5':api_node('SaveImage',images=['4',0])}
        e=editor(g);e['nodes'][1]['widgets_values']={'text':'stale inner text'}
        self.assertNotIn('value',analyze(e)['prompts'][0])

    def test_expanded_nodes_include_model_patch_but_exclude_annotation(self):
        c = self.analyze()
        self.assertEqual(set(c['nodes']), {'CLIPLoader','VAELoader','ConditioningZeroOut','VAEDecode','UNETLoader',
             'CLIPTextEncode','EmptySD3LatentImage','ModelSamplingAuraFlow','KSampler','SaveImage'})
        self.assertNotIn('MarkdownNote', {e['type'] for e in c['evidence']})
        self.assertTrue(all(m['expected_sha256'] is None for m in c['models']))
        # An unrelated opaque node on the active path remains opaque even if
        # it exposes plausible socket names and is registered on the target.
        patch_node = next(n for n in self.graph['definitions']['subgraphs'][0]['nodes'] if n['type']=='ModelSamplingAuraFlow')
        patch_node['type']='UnspecifiedModelPatch'
        self.schema['UnspecifiedModelPatch']=self.schema['ModelSamplingAuraFlow']
        c = self.analyze()
        self.assertTrue(any('Unknown behavior: UnspecifiedModelPatch' in u for u in c['uncertainties']))
        self.assertIsNone(c['input_counts']['image']['max'])
        self.assertIn('does not establish execution',c['limitations'][0])

    def test_target_schema_conflict_does_not_assert_prompt_polarity(self):
        self.schema['KSampler']['input']['required']['positive']=['IMAGE']
        c = self.analyze()
        self.assertEqual(c['prompts'][0]['role'],'unknown')
        self.assertTrue(any('schema does not establish positive' in u for u in c['uncertainties']))

    def test_zeroed_negative_is_not_a_negative_text_field_but_direct_negative_is(self):
        # Change only the negative connection; polarity follows connections,
        # never prompt text, ID order, title, or model filename.
        sub = self.graph['definitions']['subgraphs'][0]
        link = next(link for link in sub['links'] if link['id']==33)
        link['origin_id']=27
        c = self.analyze()
        self.assertEqual(c['prompts'][0]['role'],'unknown')  # one encoder now feeds both roles
        self.assertEqual(c['conditioning'],[])

    def test_nested_literal_binding_is_not_workflow_specific(self):
        leaf={'id':'leaf','inputs':[{'name':'size'}], 'nodes':[{'id':7,'type':'EmptyLatentImage',
              'inputs':[{'name':'width','link':1,'widget':{'name':'width'}}],
              'widgets_values':[32,64,1]}],
              'links':[[1,-10,0,7,0,'INT'],[2,7,0,-20,0,'LATENT']]}
        middle={'id':'middle','inputs':[{'name':'canvas_size'}], 'nodes':[{'id':9,'type':'leaf',
                'inputs':[{'name':'size','link':1}], 'widgets_values_named':{'size':99}}],
                'links':[[1,-10,0,9,0,'INT'],[2,9,0,-20,0,'LATENT']]}
        g=editor({'1':api_node('middle',canvas_size=777),'2':api_node('KSampler',latent_image=['1',0]),
                  '3':api_node('VAEDecode',samples=['2',0]),'4':api_node('SaveImage',images=['3',0])})
        g['definitions']={'subgraphs':[leaf,middle]}
        c=analyze(g)
        width=next(x for x in c['controls'] if x['field']=='width')
        self.assertEqual(width['value'],777)
        self.assertEqual(width['exposed_at'],{'node':'1','field':'canvas_size'})
        self.assertIn('EmptyLatentImage',c['nodes'])

if __name__=='__main__':unittest.main()
