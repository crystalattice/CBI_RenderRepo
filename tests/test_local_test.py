"""Mocked transport/state cases. No Comfy execution or GPU/OOM qualification."""
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from renderrepo import local_test as lt
from renderrepo.installation_history import atomic_json, lease
from renderrepo.receipts import save_receipt


class LocalTestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'input').mkdir();(self.root/'output').mkdir()
        self.name='rr-starter-resize';self.calls=[];self.jobs={};self.fail=None;self.cached=[]
        for file in ('main.py','nodes.py','execution.py','server.py','comfy_extras/nodes_mask.py'):
            p=self.root/file;p.parent.mkdir(exist_ok=True);p.write_text('client_prompt_id = json_data.get("prompt_id")')
        self.graph=json.loads((lt.assets()/(self.name+'.json')).read_text())
        self.workflow=self.root/'workflow.json';self.workflow.write_text(json.dumps(self.graph))
        for name in ('rr-starter-base.png','rr-starter-overlay.png'): shutil.copyfile(lt.assets()/name,self.root/'input'/name)
        self.receipt={'receipt_version':1,'name':self.name,'version':'0.1.0','status':'installed','comfy_root':str(self.root),
                      'workflow_path':str(self.workflow),'workflow_sha256':lt.digest(self.workflow.read_bytes()),'artifacts':[],'managed':[]}
        save_receipt(self.root,self.receipt)
        self.nodes={n:{'python_module':next(iter(modules))} for n,modules in lt.MODULES.items()}
        self.stats={'system':{'argv':[str(self.root/'main.py'),'--cpu'],'comfyui_version':'test','python_version':'3.13','pytorch_version':'test'},'devices':[{'type':'cpu','name':'fixture','vram_total':123}]}
        self.capabilities={'root':str(self.root),'input_dir':str(self.root/'input'),'output_dir':str(self.root/'output'),'modules':{n:next(iter(modules)) for n,modules in lt.MODULES.items()},'targeted_cancel':False}
        owner=self
        class Wire:
            def __init__(self,url): self.url=url
            def request(self,route,data=None): return owner.request(route,data)
        self.wire=Wire('http://127.0.0.1:1')
        self.native=patch.object(lt,'Native',Wire);self.native.start();self.addCleanup(self.native.stop)
        self.verify=patch.object(lt,'verify_outputs',return_value={'sha256':'fixture','dimensions':[160,100],'verification_seconds':.001});self.verify.start();self.addCleanup(self.verify.stop)
        self.sleep=patch.object(lt.time,'sleep',return_value=None);self.sleep.start();self.addCleanup(self.sleep.stop)

    def request(self,route,data=None):
        self.calls.append((route,data))
        if route=='/object_info':return self.nodes
        if route=='/system_stats':return self.stats
        if route.endswith('/capabilities'):return self.capabilities
        if route=='/prompt':
            if self.fail=='rejected':
                from urllib.error import HTTPError
                raise HTTPError('/prompt',400,'Invalid prompt',None,io.BytesIO(b'{"error":"invalid test profile"}'))
            id=data['prompt_id'];self.jobs[id]=data
            if self.fail=='ambiguous':raise OSError('ack lost after acceptance')
            return {'prompt_id':id}
        if route.startswith('/history/'):
            id=route.split('/')[-1]
            if id not in self.jobs or self.fail=='pending':return {}
            now=int(time.time()*1000)
            failure=self.fail in {'oom','ordinary','interrupted'}
            end='execution_interrupted' if self.fail=='interrupted' else 'execution_error' if failure else 'execution_success'
            return {id:{'prompt':[0,id,self.jobs[id]['prompt'],{'create_time':now-30}],
                        'status':{'status_str':'error' if failure else 'success','messages':[
                            ['execution_start',{'timestamp':now-20}],['execution_cached',{'nodes':self.cached}],
                            [end,{'timestamp':now,'exception_message':'simulated OOM' if self.fail=='oom' else 'simulated failure'}]]},'outputs':{}}}
        if route.endswith('/cancel-owned'):return {'state':'not_interrupted','execution_may_continue':True}
        raise AssertionError(route)

    def plan(self,runs=2,deadline=60):return lt.prepare(self.root,self.name,self.wire.url,runs=runs,deadline=deadline)
    def approved(self,runs=2):
        p=self.plan(runs);lt.approve(self.root,p['plan']['id'],p['approval_sha256']);return p
    def seed_run(self,plan,age=0):
        token=plan['plan']['id'];folder,p,state=lt.load(self.root,token)
        run={'index':0,'label':'First observed run','state':'submitted','prompt_id':'owned','submitted_epoch':time.time()-age,'execution_may_continue':True}
        state.update(state='running',runs=[run]);atomic_json(folder/'state.json',state);return folder

    def test_prepare_is_review_only_exact_inputs_limits_overrides(self):
        plan=self.plan();p=plan['plan']
        self.assertFalse(any(route=='/prompt' for route,_ in self.calls))
        self.assertEqual(p['context']['inputs'][0]['dimensions'],[320,200]);self.assertEqual(p['runs'],2)
        a,b=p['attempts'];self.assertNotEqual(a['inputs'][0]['sha256'],b['inputs'][0]['sha256'])
        self.assertIsNotNone(b['inputs'][0]['change']);self.assertEqual(a['label'],'First observed run')
        for runs,deadline in [(0,60),(3,60),(1,61),(1,0)]:
            with self.assertRaises(ValueError):lt.prepare(self.root,self.name,self.wire.url,runs=runs,deadline=deadline)

    def test_approval_hash_changed_settings_and_new_attempt_required(self):
        p=self.plan();token=p['plan']['id']
        with self.assertRaises(ValueError):lt.approve(self.root,token,'wrong')
        self.workflow.write_text('{}')
        with self.assertRaises(ValueError):lt.approve(self.root,token,p['approval_sha256'])
        self.workflow.write_text(json.dumps(self.graph));lt.approve(self.root,token,p['approval_sha256'])
        with self.assertRaises(ValueError):lt.approve(self.root,token,p['approval_sha256'])

    def test_two_runs_duplicate_worker_does_not_resubmit(self):
        p=self.approved();token=p['plan']['id'];lt.worker(self.root,token)
        state=lt.status(self.root,token)
        self.assertEqual(state['state'],'completed');self.assertEqual(len(state['runs']),2)
        lt.worker(self.root,token)
        self.assertEqual(sum(r=='/prompt' for r,_ in self.calls),2)
        for run in state['runs']:self.assertTrue(run['evidence']['included_in_compute_summary'])
        self.assertFalse(any(r in {'/interrupt','/free'} for r,_ in self.calls))

    def test_duplicate_completed_runner_cannot_erase_results_when_other_test_active(self):
        p=self.approved(1);token=p['plan']['id'];lt.worker(self.root,token)
        with lease(lt.store(self.root)/'submission.lock'):
            result=lt.worker(self.root,token)
        self.assertEqual(result['state'],'completed');self.assertEqual(sum(r=='/prompt' for r,_ in self.calls),1)

    def test_duplicate_live_worker_is_rejected(self):
        p=self.approved();folder=lt.directory(self.root,p['plan']['id'])
        with lease(folder/'worker.lock'):
            with self.assertRaises(ValueError):lt.run_worker(self.root,p['plan']['id'])
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_cached_replay_excluded_and_absent_cache_evidence_unknown(self):
        p=self.approved(1);self.cached=['2'];state=lt.worker(self.root,p['plan']['id'])
        e=state['runs'][0]['evidence'];self.assertEqual(e['computation'],'Cached');self.assertIsNone(e['execution_seconds']);self.assertFalse(e['included_in_compute_summary'])
        job=self.request('/history/'+state['runs'][0]['prompt_id'])[state['runs'][0]['prompt_id']]
        job['status']['messages']=[v for v in job['status']['messages'] if v[0]!='execution_cached']
        self.assertEqual(lt.evidence(job,p['plan']['attempts'][0])['computation'],'Computation unverified')

    def test_absent_native_timestamps_never_become_zero_compute_time(self):
        p=self.approved(1);state=lt.worker(self.root,p['plan']['id']);id=state['runs'][0]['prompt_id']
        job=self.request('/history/'+id)[id]
        for _,data in job['status']['messages']:data.pop('timestamp',None)
        result=lt.evidence(job,p['plan']['attempts'][0])
        self.assertIsNone(result['execution_seconds']);self.assertIsNone(result['queued_seconds']);self.assertFalse(result['included_in_compute_summary'])

    def test_mismatched_server_prompt_is_not_a_configuration_pass(self):
        p=self.plan(1);self.seed_run(p);self.jobs['owned']={'prompt':{'different':{}}}
        state=lt.reconcile(self.root,p['plan']['id'])
        self.assertEqual(state['state'],'failed');self.assertIn('differs',state['runs'][0]['verification_error'])
        self.assertFalse(state['runs'][0]['evidence']['included_in_compute_summary'])

    def test_simulated_oom_and_ordinary_failure_retained(self):
        for failure in ('oom','ordinary','interrupted'):
            p=self.approved(1);self.fail=failure;state=lt.worker(self.root,p['plan']['id'])
            self.assertEqual(state['state'],'cancelled' if failure=='interrupted' else 'failed')
            self.assertIn('error',state['runs'][0]);self.assertIsNone(state['runs'][0]['evidence']['execution_seconds'])

    def test_definitive_submission_rejection_is_failed_not_ambiguous(self):
        p=self.approved();self.fail='rejected';state=lt.worker(self.root,p['plan']['id'])
        self.assertEqual(state['state'],'failed');self.assertEqual(len(state['runs']),1)
        self.assertEqual(state['runs'][0]['terminal_outcome'],'submission_rejected')
        self.assertFalse(state['runs'][0]['execution_may_continue']);self.assertEqual(len(self.jobs),0)

    def test_ambiguous_ack_is_reconciled_without_repeat_or_resubmission(self):
        p=self.approved();self.fail='ambiguous';state=lt.worker(self.root,p['plan']['id'])
        self.assertEqual(state['state'],'unresolved');self.assertEqual(len(state['runs']),1)
        self.assertTrue(state['runs'][0]['terminal_observed'])
        self.assertEqual(sum(r=='/prompt' for r,_ in self.calls),1)

    def test_timeout_current_may_continue_and_later_completion_preserved(self):
        p=self.plan(1);self.seed_run(p,age=65);self.fail='pending'
        state=lt.reconcile(self.root,p['plan']['id'])
        run=state['runs'][0];self.assertEqual(state['state'],'timed_out');self.assertTrue(run['execution_may_continue'])
        self.assertIn('not_interrupted',run['cancellation']['state'])
        self.jobs['owned']={'prompt':p['plan']['attempts'][0]['prompt']};self.fail=None
        state=lt.reconcile(self.root,p['plan']['id'])
        self.assertEqual(state['state'],'timed_out');self.assertEqual(state['runs'][0]['terminal_outcome'],'completed');self.assertFalse(state['runs'][0]['execution_may_continue'])
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_stop_queued_deletes_only_owned_and_running_fallback_keeps_monitoring(self):
        for queued in (True,False):
            p=self.plan(1);self.seed_run(p);self.fail='pending';lt.stop(self.root,p['plan']['id'])
            original=self.request
            def request(route,data=None):
                if route.endswith('/cancel-owned'):
                    self.assertEqual(data,{'prompt_id':'owned'})
                    return {'state':'queued_removed' if queued else 'not_interrupted','execution_may_continue':not queued}
                return original(route,data)
            with patch.object(self,'request',side_effect=request):state=lt.reconcile(self.root,p['plan']['id'])
            self.assertEqual(state['state'],'cancelled');self.assertEqual(state['runs'][0]['execution_may_continue'],not queued)
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_targeted_queue_primitives_cannot_cancel_unrelated_jobs(self):
        p=self.plan(1);self.seed_run(p);token=p['plan']['id']
        class Queue:
            def __init__(self):self.pending=[(0,'other-queued'),(1,'owned')];self.running='other-running';self.interrupts=[]
            def delete_queue_item(self,predicate):
                for item in self.pending:
                    if predicate(item):self.pending.remove(item);return True
                return False
            def interrupt_if_running(self,id):
                if self.running==id:self.interrupts.append(id);return True
                return False
        queue=Queue()
        self.assertEqual(lt.cancel_owned(self.root,token,'owned',queue)['state'],'queued_removed')
        self.assertEqual(queue.pending,[(0,'other-queued')]);self.assertEqual(queue.interrupts,[])
        self.assertEqual(lt.cancel_owned(self.root,token,'owned',queue)['state'],'not_interrupted')
        queue.running='owned'
        self.assertEqual(lt.cancel_owned(self.root,token,'owned',queue)['state'],'targeted_interrupt_requested')
        self.assertEqual(queue.interrupts,['owned'])
        with self.assertRaises(ValueError):lt.cancel_owned(self.root,token,'other-running',queue)
        queue.interrupt_if_running=None
        self.assertTrue(lt.cancel_owned(self.root,token,'owned',queue)['execution_may_continue'])

    def test_restart_reconnect_and_do_not_submit_remaining_runs(self):
        p=self.plan();self.seed_run(p);self.jobs['owned']={'prompt':p['plan']['attempts'][0]['prompt']}
        state=lt.status(self.root,p['plan']['id'])
        self.assertEqual(state['state'],'unresolved');self.assertTrue(state['runs'][0]['terminal_observed'])
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_approved_launch_failure_is_unresolved_and_never_retried(self):
        p=self.approved(1);token=p['plan']['id'];folder,_,state=lt.load(self.root,token)
        state['approved_epoch']=time.time()-10;atomic_json(folder/'state.json',state)
        self.assertEqual(lt.status(self.root,token)['state'],'unresolved')
        lt.worker(self.root,token)
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_symlink_output_escape_cannot_be_approved(self):
        p=self.plan(1);other=self.root/'elsewhere';other.mkdir()
        (self.root/'output/rr-local-tests').symlink_to(other,target_is_directory=True)
        # Still inside output's parent but outside the configured output directory.
        with self.assertRaises(ValueError):lt.approve(self.root,p['plan']['id'],p['approval_sha256'])
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_prepared_input_tamper_after_review_is_rejected(self):
        p=self.approved(1);token=p['plan']['id'];folder=lt.directory(self.root,token)
        (folder/p['plan']['attempts'][0]['inputs'][0]['prepared']).write_bytes(b'changed')
        state=lt.run_worker(self.root,token)
        self.assertEqual(state['state'],'unresolved');self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_historical_changes_mark_different_preserve_original(self):
        p=self.approved(1);token=p['plan']['id'];lt.worker(self.root,token)
        before=lt.read(lt.directory(self.root,token)/'plan.json')
        self.stats['devices'][0]['name']='different';self.assertTrue(lt.status(self.root,token,True)['historical_differences'])
        self.stats['devices'][0]['name']='fixture';self.workflow.write_text('{}')
        self.assertTrue(lt.status(self.root,token,True)['historical_differences'])
        self.assertEqual(before,lt.read(lt.directory(self.root,token)/'plan.json'))

    def test_input_shape_launch_runtime_and_model_evidence_changes_are_stale(self):
        p=self.approved(1);token=p['plan']['id'];lt.worker(self.root,token)
        original=copy.deepcopy(self.stats)
        self.stats['system']['argv'].append('--lowvram')
        self.assertTrue(lt.status(self.root,token,True)['historical_differences'])
        self.stats=copy.deepcopy(original);self.stats['system']['pytorch_version']='changed'
        self.assertTrue(lt.status(self.root,token,True)['historical_differences'])
        self.stats=copy.deepcopy(original)
        self.receipt['dependency_inventory']={'artifacts':[{'filename':'model-evidence-only','sha256':'a'*64}]}
        save_receipt(self.root,self.receipt)
        self.assertTrue(lt.status(self.root,token,True)['historical_differences'])
        self.receipt.pop('dependency_inventory');save_receipt(self.root,self.receipt)
        from PIL import Image
        Image.new('RGB',(64,64),'green').save(self.root/'input/rr-starter-base.png')
        self.assertTrue(lt.status(self.root,token,True)['historical_differences'])
        self.assertEqual(lt.read(lt.directory(self.root,token)/'state.json')['state'],'completed')

    def test_stop_before_any_submission_and_declined_plan_do_not_execute(self):
        p=self.plan();lt.worker(self.root,p['plan']['id'])
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))
        lt.approve(self.root,p['plan']['id'],p['approval_sha256']);lt.stop(self.root,p['plan']['id'])
        state=lt.worker(self.root,p['plan']['id']);self.assertEqual(state['state'],'cancelled');self.assertEqual(state['runs'],[])

    def test_offline_historical_comparison_is_unknown_not_a_claim_of_change(self):
        p=self.approved(1);token=p['plan']['id'];lt.worker(self.root,token)
        with patch.object(lt,'context',side_effect=OSError('offline')):
            result=lt.status(self.root,token,True)
        self.assertEqual(result['state'],'completed');self.assertEqual(result['historical_comparison'],'unknown')

    def test_provider_or_custom_override_or_unresolved_destination_rejected(self):
        self.nodes['ImageScale']['api_node']=True
        with self.assertRaises(ValueError):self.plan()
        self.nodes['ImageScale'].pop('api_node');self.capabilities['modules']['ImageScale']='custom_nodes.paid'
        with self.assertRaises(ValueError):self.plan()
        self.capabilities['modules']['ImageScale']='nodes';self.stats['system']['argv']=['/other/main.py']
        with self.assertRaises(ValueError):self.plan()
        self.assertFalse(any(r=='/prompt' for r,_ in self.calls))

    def test_native_loader_exact_builtin_module_path_is_supported(self):
        self.name='rr-starter-overlay'
        self.graph=json.loads((lt.assets()/(self.name+'.json')).read_text());self.workflow.write_text(json.dumps(self.graph))
        self.receipt.update(name=self.name,workflow_sha256=lt.digest(self.workflow.read_bytes()));save_receipt(self.root,self.receipt)
        self.capabilities['modules']['ImageCompositeMasked']=str(self.root/'comfy_extras/nodes_mask')
        self.assertEqual(self.plan(1)['plan']['context']['package']['name'],self.name)

    def test_graph_workload_not_lowered_or_pruned(self):
        self.graph['nodes'][1]['widgets_values'][1]=64;self.workflow.write_text(json.dumps(self.graph))
        with self.assertRaises(ValueError):self.plan()
        self.graph['nodes'].append({'id':4,'type':'PaidAPI','widgets_values':[]});self.workflow.write_text(json.dumps(self.graph))
        with self.assertRaises(ValueError):self.plan()

    def test_shipped_profiles_match_catalog_sources(self):
        source=Path(__file__).resolve().parents[1]/'examples/starter-catalog'
        for path in lt.assets().iterdir():self.assertEqual(path.read_bytes(),(source/path.name).read_bytes())
