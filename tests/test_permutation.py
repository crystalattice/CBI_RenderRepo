"""Non-paid permutation qualification: complete fake wire path and crash fences."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from renderrepo.permutation import engine as e
from renderrepo.permutation.provider import Rejected
from renderrepo.permutation.review_server import server

GRAPH = {'1': {'class_type': 'Input', 'inputs': {'image': 'placeholder.png', 'text': 'original', 'negative': '',
          'seed': 0, 'cfg': 4.0, 'sampler': 'euler', 'scheduler': 'normal', 'lora': 'none',
          'strength': 0.0, 'width': 64, 'height': 64, 'untouched': 'keep me'}},
         '2': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0]}}}
BINDINGS = {name: [['1', field]] for name, field in {'prompt':'text', 'negative':'negative', 'seed':'seed',
            'cfg':'cfg', 'sampler':'sampler', 'scheduler':'scheduler', 'lora':'lora',
            'lora_strength':'strength', 'width':'width', 'height':'height'}.items()}


class Fake:
    def __init__(self, hook=None, states=None):
        self.posts, self.prepared, self.polls, self.checks = [], [], [], []
        self.hook, self.states = hook, states or {}
        self.error = None

    def check(self, config):
        self.checks.append(config)

    def prepare(self, graph, directory, bindings, settings):
        self.prepared.append(graph)
        for name, ref in settings.items():
            if name.startswith('reference:'):
                assert hashlib.sha256((directory/'references'/ref['file']).read_bytes()).hexdigest() == ref['sha256']
                for node, field in bindings[name]:
                    graph[node]['inputs'][field] = {'__type':'core/ASSET', 'info':{'id':'fake-asset'}}
        return graph

    def submit(self, graph, token):
        self.posts.append((graph, token))
        if self.error:
            raise self.error
        if self.hook:
            self.hook()
        return 'fake-' + str(len(self.posts))

    def status(self, job):
        self.polls.append(job)
        outcome = self.states.get(job, 'succeeded')
        if isinstance(outcome, Exception):
            raise outcome
        return {'state':outcome, 'provider_seconds':1.5, 'actual_charge':None, 'error':'fake_error'}

    def outputs(self, status, nodes):
        return [(b'fake image bytes', '.png')]


class PermutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        e.atomic_json(self.root/'workflow.json', GRAPH)
        self.spec = {'version':0, 'name':'test', 'workflow':'workflow.json', 'bindings':copy.deepcopy(BINDINGS),
            'fixed':{'prompt':'explicit instruction', 'negative':'avoid artifacts', 'cfg':4.0, 'sampler':'euler',
                     'scheduler':'normal', 'lora':'none', 'lora_strength':0.0, 'width':64, 'height':64},
            'vary':{}, 'seeds':[9000,9001], 'references':{},
            'provider':{'kind':'comfy-v2', 'adapter_repo':'/existing/adapter', 'base_url':'https://cloud.comfy.org', 'account_profile':'cbi'},
            'estimate':{'per_render':2.5,'unit':'credits','basis':'hypothetical operator estimate, not a price'},
            'limits':{'max_renders':120,'max_estimated_spend':300,'max_elapsed_seconds':3600,'failure_threshold':3},
            'output_nodes':['2']}

    def plan(self):
        e.atomic_json(self.root/'definition.json', self.spec)
        directory, value = e.plan(self.root/'definition.json', self.root/'store')
        return directory, value

    def ready(self):
        directory, value = self.plan()
        e.approve(directory, directory.name)
        e.control(directory, 'run')
        return directory, value

    def test_managed_requirement_blocker_is_durable_actionable_and_submits_nothing(self):
        directory, value = self.ready()
        provider = Fake()
        provider.target_evidence = {'state':'missing', 'missing_nodes':['Unavailable'],
                                    'action':'Required node Unavailable; explicitly select another target.'}
        def blocked(*args):
            raise ValueError('simulated missing managed node')
        provider.prepare = blocked
        with self.assertRaisesRegex(ValueError, 'explicitly select another target'):
            e.run(directory, provider)
        records = e.records(directory, value)
        self.assertEqual(records[0]['state'], 'preparation_failed')
        self.assertEqual(records[0]['target_requirements']['missing_nodes'], ['Unavailable'])
        self.assertEqual(provider.posts, [])

    def test_matrix_shared_seeds_fixed_preservation_stable_order(self):
        for name, values in {'cfg':[1,2,3,4,5], 'lora':['off','on'], 'sampler':['euler','heun'], 'scheduler':['normal','simple']}.items():
            self.spec['fixed'].pop(name); self.spec['vary'][name]=values
        self.spec['seeds']=[11,22,33]
        d,v=self.plan(); self.assertEqual(v['count'],120)
        for i in range(0,120,3):
            self.assertEqual([j['settings']['seed'] for j in v['jobs'][i:i+3]], [11,22,33])
        self.assertTrue(all(j['workflow']['1']['inputs']['untouched']=='keep me' for j in v['jobs']))
        self.assertEqual(self.plan()[0], d)
        self.assertFalse((d/'jobs').exists())

    def test_directory_reference_slots_snapshot_and_provenance(self):
        images=self.root/'images'; images.mkdir()
        (images/'b.png').write_bytes(b'b'); (images/'a.png').write_bytes(b'a')
        self.spec['references']={'person':{'directory':'images'}}
        self.spec['bindings']['reference:person']=[['1','image']]
        self.spec['fixed'].pop('prompt'); self.spec['vary']['prompt']=['one','two']
        d,v=self.ready(); self.assertEqual(v['count'],8)
        (images/'a.png').unlink() # execution uses retained bytes, not mutable source paths
        fake=Fake(); e.run(d,fake,sleep=lambda _:None)
        self.assertEqual(len(fake.posts),8)
        for r in e.records(d):
            ref=r['settings']['reference:person']
            self.assertIn(ref['sha256'], {'file':ref['file'], 'sha':ref['sha256']}.values())
            wire=e.read(d/'jobs'/r['id']/'submitted-workflow.json')
            self.assertEqual(wire['1']['inputs']['image']['__type'],'core/ASSET')
            self.assertEqual(e.digest(wire),r['submitted_workflow_hash'])

    def test_binding_validation_titles_connections_overlap(self):
        cases=[{'seed':[['Input','seed']]}, {'seed':[['2','images']]}, {'seed':[['1','seed']], 'cfg':[['1','seed']]}]
        for bindings in cases:
            with self.subTest(bindings=bindings):
                self.spec['bindings']=bindings
                with self.assertRaises(ValueError): self.plan()

    def test_scalar_binding_type_mismatch_is_rejected(self):
        self.spec['bindings']['seed']=[['1','image']]
        with self.assertRaisesRegex(ValueError,'scalar type'):self.plan()

    def test_download_hosts_are_explicit_approved_scope(self):
        default_directory, default_plan = self.plan()
        self.assertEqual(default_plan['provider']['download_hosts'], ['storage.googleapis.com'])
        self.spec['provider']['download_hosts']=['*.example.test']
        with self.assertRaises(ValueError):self.plan()
        self.spec['provider']['download_hosts']=['storage.example.test']
        d,v=self.plan();self.assertEqual(v['provider']['download_hosts'],['storage.example.test'])
        self.spec['provider']['download_hosts']=[]
        empty_directory, empty_plan = self.plan()
        self.assertEqual(empty_plan['provider']['download_hosts'], [])
        self.assertNotEqual(empty_directory,d)
        self.assertNotEqual(empty_directory,default_directory)

    def test_missing_or_invalid_state_never_resubmits(self):
        d,_=self.ready();e.run(d,Fake())
        row=e.records(d)[0];path=d/'jobs'/row['id']/'state.json'
        path.unlink()
        self.assertEqual(e.records(d)[0]['state'],'corrupt')
        with self.assertRaises(ValueError):e.run(d,Fake())
        e.atomic_json(path,{'state':'planned'})
        self.assertEqual(e.records(d)[0]['state'],'corrupt')
        with self.assertRaises(ValueError):e.run(d,Fake())

    def test_limits_no_submission_or_materialization(self):
        self.spec['limits']['max_renders']=1
        with self.assertRaises(ValueError):self.plan()
        self.assertFalse((self.root/'store').exists())
        self.spec['limits']['max_renders']=2;self.spec['limits']['max_estimated_spend']=4
        with self.assertRaises(ValueError):self.plan()

    def test_approval_and_expanded_plan_requires_new_approval(self):
        d,v=self.plan()
        with self.assertRaises(FileNotFoundError):e.run(d,Fake())
        with self.assertRaises(ValueError):e.approve(d,'wrong')
        e.approve(d,d.name)
        self.spec['seeds'].append(9002)
        new,_=self.plan();self.assertNotEqual(new,d)
        with self.assertRaises(FileNotFoundError):e.run(new,Fake())
        v['limits']['max_renders']=999
        e.atomic_json(d/'plan.json',v)
        with self.assertRaises(ValueError):e.load(d)

    def test_submission_intent_and_ack_are_durable_before_poll(self):
        d,v=self.ready();fake=Fake()
        original=fake.submit
        def submit(graph,token):
            record=next(r for r in e.records(d) if r.get('submission_token') == token)
            self.assertEqual(record['state'],'submitting');self.assertIsNone(record['provider_job_id'])
            self.assertEqual(e.digest(e.read(d/'jobs'/record['id']/'submitted-workflow.json')),record['submitted_workflow_hash'])
            return original(graph,token)
        fake.submit=submit
        original_status=fake.status
        def status(job):
            self.assertTrue(any(r.get('provider_job_id')==job for r in e.records(d)))
            return original_status(job)
        fake.status=status;e.run(d,fake,sleep=lambda _:None)
        self.assertEqual(len(fake.posts),2)
        self.assertTrue(all(r['state']=='completed' for r in e.records(d)))
        self.assertTrue(all(r['actual_charge'] is None for r in e.records(d)))

    def test_pause_after_current_then_resume_without_regenerating(self):
        d,_=self.ready();fake=Fake(hook=lambda:e.control(d,'pause'))
        e.run(d,fake,sleep=lambda _:None);self.assertEqual(len(fake.posts),1)
        self.assertEqual([r['state'] for r in e.records(d)],['completed','planned'])
        e.control(d,'run');fake.hook=None;e.run(d,fake,sleep=lambda _:None)
        self.assertEqual(len(fake.posts),2)
        e.run(d,fake,sleep=lambda _:None);self.assertEqual(len(fake.posts),2)

    def test_stop_finishes_accepted_job_and_submits_nothing_else(self):
        d,_=self.ready();fake=Fake(hook=lambda:e.control(d,'stop'))
        e.run(d,fake,sleep=lambda _:None);self.assertEqual(len(fake.posts),1)
        e.run(d,fake,sleep=lambda _:None);self.assertEqual(len(fake.posts),1)

    def test_pause_during_preparation_prevents_post(self):
        d,_=self.ready();fake=Fake();old=fake.prepare
        def prepare(*args):
            value=old(*args);e.control(d,'pause');return value
        fake.prepare=prepare;e.run(d,fake);self.assertEqual(fake.posts,[])

    def test_restart_known_job_reconciles_before_post(self):
        d,_=self.ready();first=Fake(states={'fake-1':OSError('offline')})
        with self.assertRaises(ValueError):e.run(d,first,sleep=lambda _:None)
        self.assertEqual(len(first.posts),1)
        restarted=Fake(); events=[]
        old=restarted.status;post=restarted.submit
        restarted.status=lambda job:(events.append(('GET',job)) or old(job))
        restarted.submit=lambda graph,token:(events.append(('POST',token)) or post(graph,token))
        e.run(d,restarted,sleep=lambda _:None)
        self.assertEqual(events[0],('GET','fake-1'));self.assertEqual(len(restarted.posts),1)
        self.assertTrue(all(r['state']=='completed' for r in e.records(d)))

    def test_process_death_after_intent_is_ambiguous(self):
        d,_=self.ready();fake=Fake()
        def die(*_):raise KeyboardInterrupt()
        fake.submit=die
        with self.assertRaises(KeyboardInterrupt):e.run(d,fake)
        with self.assertRaisesRegex(ValueError,'Unresolved'):e.run(d,Fake())
        self.assertEqual(e.records(d)[0]['state'],'ambiguous')

    def test_ambiguous_submission_halts_no_retry(self):
        d,_=self.ready();fake=Fake();fake.error=TimeoutError()
        with self.assertRaises(ValueError):e.run(d,fake)
        self.assertEqual(len(fake.posts),1)
        with self.assertRaises(ValueError):e.run(d,fake)
        self.assertEqual(len(fake.posts),1)

    def test_operator_association_reconciles_without_second_post(self):
        d,_=self.ready();fake=Fake();fake.error=TimeoutError()
        with self.assertRaises(ValueError):e.run(d,fake)
        e.attach(d,e.records(d)[0]['id'],'11111111-1111-4111-8111-111111111111')
        next_provider=Fake();e.run(d,next_provider,sleep=lambda _:None)
        self.assertEqual(next_provider.polls[0],'11111111-1111-4111-8111-111111111111')
        self.assertEqual(len(next_provider.posts),1)

    def test_failed_subset_does_not_discard_success_or_retry(self):
        self.spec['seeds']=[1,2,3,4]
        d,_=self.ready();fake=Fake(states={'fake-2':'failed','fake-4':'failed'})
        e.run(d,fake,sleep=lambda _:None)
        self.assertEqual(len(fake.posts),4)
        self.assertEqual([r['state'] for r in e.records(d)],['completed','failed','completed','failed'])
        e.run(d,fake);self.assertEqual(len(fake.posts),4)

    def test_repeated_failure_stop_threshold(self):
        self.spec['seeds']=[1,2,3,4];self.spec['limits']['failure_threshold']=2
        d,_=self.ready();fake=Fake(states={'fake-1':'failed','fake-2':'failed'})
        e.run(d,fake);self.assertEqual(len(fake.posts),2)
        e.run(d,fake);self.assertEqual(len(fake.posts),2)

    def test_definite_rejection_never_auto_retries(self):
        d,_=self.ready();fake=Fake();fake.error=Rejected('invalid')
        e.run(d,fake);self.assertEqual(len(fake.posts),2)
        self.assertTrue(all(r['state']=='failed' for r in e.records(d)))
        e.run(d,fake);self.assertEqual(len(fake.posts),2)

    def test_duplicate_runner_prevention(self):
        d,_=self.ready();fake=Fake()
        with e.locked(d.parent/'runner.lock'):
            with self.assertRaisesRegex(ValueError,'already active'):e.run(d,fake)
        self.assertEqual(fake.posts,[])

    def test_elapsed_limit_blocks_new_work_but_reconciles_accepted(self):
        d,_=self.ready();fake=Fake(states={'fake-1':OSError()})
        with self.assertRaises(ValueError):e.run(d,fake)
        e.atomic_json(d/'runtime.json',{'started_at':'2020-01-01T00:00:00+00:00'})
        restarted=Fake();e.run(d,restarted)
        self.assertEqual(restarted.posts,[]);self.assertEqual(restarted.polls,['fake-1'])

    def test_reviews_exports_coverage_unknown_cost(self):
        d,_=self.ready();e.run(d,Fake(),sleep=lambda _:None)
        rows=e.records(d);e.review(d,rows[0]['id'],'keeper','good')
        stats=e.summary(d)
        self.assertEqual(stats['keeper_rate'],1);self.assertEqual(stats['review_coverage'],.5)
        self.assertEqual(stats['cost_unknown_samples'],2);self.assertEqual(stats['known_costs'],{})
        self.assertIsNotNone(stats['seconds_per_keeper'])
        e.export(d,self.root/'export'); data=e.read(self.root/'export/results.json')
        self.assertEqual(data['results'][0]['review']['notes'],'good')
        self.assertEqual(data['results'][1]['review']['rating'],'unreviewed')
        self.assertIn('null',(self.root/'export/results.csv').read_text())
        with self.assertRaises(ValueError):e.review(d,rows[0]['id'],'keeper','x'*1001)

    def test_known_partial_cost_not_presented_as_complete(self):
        d,_=self.ready();e.run(d,Fake(),sleep=lambda _:None)
        row=e.records(d)[0];row['actual_charge']={'value':3,'unit':'credits','basis':'provider_accounting'}
        e.atomic_json(d/'jobs'/row['id']/'state.json',row);e.review(d,row['id'],'keeper')
        stats=e.summary(d);self.assertFalse(stats['known_costs']['credits']['complete'])
        self.assertEqual(stats['known_costs']['credits']['known_cost_per_keeper'],3)

    def test_corrupt_record_readable_others_execution_blocked(self):
        d,_=self.ready();e.run(d,Fake(),sleep=lambda _:None)
        row=e.records(d)[0];(d/'jobs'/row['id']/'state.json').write_text('{')
        self.assertEqual([r['state'] for r in e.records(d)],['corrupt','completed'])
        with self.assertRaises(ValueError):e.run(d,Fake())

    def test_real_worker_process_exit_releases_lease_and_reconciles(self):
        import subprocess
        import sys
        d,_=self.ready()
        worker = """
import os, sys
from renderrepo.permutation import engine
class Provider:
    def check(self, config): pass
    def prepare(self, graph, *args): return graph
    def submit(self, graph, token): return 'known-before-process-exit'
    def status(self, job): os._exit(73)
engine.run(sys.argv[1], Provider())
"""
        exited=subprocess.run([sys.executable,'-c',worker,str(d)],cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=10)
        self.assertEqual(exited.returncode,73,exited.stderr)
        self.assertEqual(e.records(d)[0]['provider_job_id'],'known-before-process-exit')
        self.assertEqual(e.execution_state(d),'interrupted')
        restarted=Fake();e.run(d,restarted)
        self.assertEqual(restarted.polls[0],'known-before-process-exit')
        self.assertEqual(len(restarted.posts),1)

    def test_actual_charge_and_time_require_trustworthy_evidence(self):
        d,_=self.ready();fake=Fake();old=fake.status
        def status(job):
            value=old(job);value['actual_charge']={'value':2.75,'unit':'credits','basis':'provider_accounting'}
            return value
        fake.status=status;e.run(d,fake,sleep=lambda _:None)
        self.assertEqual(e.records(d)[0]['provider_seconds'],1.5)
        self.assertEqual(e.records(d)[0]['actual_charge']['value'],2.75)
        self.assertTrue(e.summary(d)['known_costs']['credits']['complete'])

    def test_provider_time_unknown_and_unqualified_cost_rejected(self):
        d,_=self.ready();fake=Fake();old=fake.status
        def status(job):
            value=old(job);value['provider_seconds']=None
            return value
        fake.status=status;e.run(d,fake,sleep=lambda _:None)
        self.assertIsNone(e.records(d)[0]['provider_seconds'])
        self.spec['seeds']=[12];other,_=self.ready();bad=Fake()
        bad.status=lambda _:{'state':'succeeded','actual_charge':{'value':0,'unit':'USD','basis':'guess'}}
        with self.assertRaises(ValueError):e.run(other,bad)
        self.assertEqual(e.records(other)[0]['state'],'reconciliation_required')

    def test_other_experiment_cannot_bypass_unresolved_submission(self):
        d,_=self.ready();fake=Fake();fake.error=TimeoutError()
        with self.assertRaises(ValueError):e.run(d,fake)
        self.spec['seeds']=[100];other,_=self.ready();next_provider=Fake()
        with self.assertRaisesRegex(ValueError,'outstanding'):e.run(other,next_provider)
        self.assertEqual(next_provider.posts,[])

    def test_pause_acknowledgement_and_stop_state(self):
        d,_=self.ready();fake=Fake(hook=lambda:e.control(d,'pause'))
        e.run(d,fake);self.assertEqual(e.execution_state(d),'paused')
        e.control(d,'run');fake.hook=lambda:e.control(d,'stop')
        e.run(d,fake);self.assertEqual(e.execution_state(d),'finished')

    def test_submitted_graph_corruption_is_not_resubmitted(self):
        d,_=self.ready();fake=Fake(states={'fake-1':OSError()})
        with self.assertRaises(ValueError):e.run(d,fake)
        row=e.records(d)[0]
        e.atomic_json(d/'jobs'/row['id']/'submitted-workflow.json',{'changed':True})
        self.assertEqual(e.records(d)[0]['state'],'corrupt')
        with self.assertRaises(ValueError):e.run(d,Fake())

    def test_review_reconnects_while_worker_active_without_owning_it(self):
        d,_=self.ready();accepted=threading.Event();release=threading.Event();errors=[]
        fake=Fake();old=fake.status
        def status(job):
            accepted.set()
            if not release.wait(5): raise TimeoutError('test release')
            return old(job)
        fake.status=status
        def worker():
            try:e.run(d,fake,sleep=lambda _:None)
            except Exception as err:errors.append(err)
        thread=threading.Thread(target=worker);thread.start()
        self.assertTrue(accepted.wait(5))
        http=server(d,0);reader=threading.Thread(target=http.serve_forever,daemon=True);reader.start()
        try:
            base=f'http://127.0.0.1:{http.server_port}'
            first=json.load(urlopen(base+'/data'));second=json.load(urlopen(base+'/data'))
            self.assertEqual(first['execution_state'],'running')
            self.assertEqual(first['rows'][0]['provider_job_id'],second['rows'][0]['provider_job_id'])
            self.assertEqual(len(fake.posts),1)
        finally:
            http.shutdown();http.server_close();release.set();thread.join(5)
        self.assertEqual(errors,[])
        self.assertEqual(len(fake.posts),2) # closing review did not cancel execution

    def test_review_http_reconnection_and_security(self):
        d,_=self.ready();e.run(d,Fake(),sleep=lambda _:None)
        http=server(d,0);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        self.addCleanup(http.server_close);self.addCleanup(http.shutdown)
        base=f'http://127.0.0.1:{http.server_port}'
        first=json.load(urlopen(base+'/data'));second=json.load(urlopen(base+'/data'))
        self.assertEqual(first['rows'],second['rows'])
        body=json.dumps({'job':first['rows'][0]['id'],'rating':'modify','notes':'adjust'}).encode()
        with self.assertRaises(HTTPError):urlopen(Request(base+'/review',data=body))
        request=Request(base+'/review',data=body,headers={'Origin':base,'X-RR-Review':first['token'],'Content-Type':'application/json'})
        urlopen(request).close();self.assertEqual(e.results(d)[0]['review']['rating'],'modify')
        with self.assertRaises(HTTPError):urlopen(Request(base+'/data',headers={'Host':'evil.test'}))
        self.assertEqual(urlopen(base+first['rows'][0]['outputs'][0]['url']).read(),b'fake image bytes')


if __name__ == '__main__':unittest.main()
