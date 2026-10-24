"""Explicit, bounded native-Comfy tests for the three built-in starter profiles.

No cloud adapters, arbitrary node execution, install scripts or cache flushes.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request

from .installation_history import atomic_json, timestamp, lease as file_lease, active
from .receipts import read_receipts

PROFILES = {'rr-starter-resize':'resize', 'rr-starter-portrait-crop':'portrait-crop', 'rr-starter-overlay':'overlay'}
MODULES = {'LoadImage':{'nodes'}, 'SaveImage':{'nodes'}, 'ImageScale':{'nodes'},
           'ImageCompositeMasked':{'comfy_extras.nodes_mask'}}
STATES = {'completed','failed','cancelled','timed_out','unresolved'}
WARNING = 'Execution consumes local CPU/GPU/RAM and produces files. Only this recorded configuration is tested; no quality, minimum hardware or cold-start claim.'


def lease(path):
    try: return file_lease(path)
    except ValueError:
        raise ValueError('A local test worker is active; wait or reconcile known jobs instead of submitting again.') from None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read(path):
    return json.loads(Path(path).read_text())


class Native:
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'http' or parts.hostname not in {'127.0.0.1','::1','localhost'} or parts.username or parts.password or parts.path not in {'','/'} or parts.query or parts.fragment:
            raise ValueError('Test locally supports a native local HTTP Comfy server with a matching local installation, not a proxy or provider URL.')
        self.url = url.rstrip('/')

    def request(self, route, data=None):
        # No redirects: an execution destination must never change after review.
        from urllib.request import build_opener, HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args): return None
        body = encoded(data) if data is not None else None
        request = Request(self.url+route, data=body, headers={'Content-Type':'application/json','X-RenderRepo-Request':'1'})
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            raw = response.read(32*1024*1024+1)
            if len(raw)>32*1024*1024: raise ValueError('Comfy response is too large')
            return json.loads(raw or b'{}')


def store(root):
    return Path(root)/'.renderrepo/local-tests'


def directory(root, token):
    if not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid local test identity')
    path=store(root)/token
    if any(p.is_symlink() for p in (Path(root)/'.renderrepo',store(root),path)):
        raise ValueError('Local test state must not be symlinked')
    return path


def assets():
    # These profiles ship with RR, not with untrusted package executable code.
    return Path(__file__).resolve().parent/'starter_profiles'


def semantic(graph):
    if graph.get('definitions') or not isinstance(graph.get('nodes'),list):
        raise ValueError('Unsupported test graph; only the three flat starter profiles are supported')
    return {'nodes':sorted([{'id':n['id'],'type':n['type'],'mode':n.get('mode',0),'widgets':n.get('widgets_values',[])} for n in graph['nodes']],key=lambda n:n['id']),
            'links':graph.get('links')}


def runtime_identity(observed, root):
    system=observed.get('system') or {}
    stats=system.get('system',{})
    argv=stats.get('argv',[])
    if not argv or Path(argv[0]).resolve()!=Path(root).resolve()/'main.py':
        raise ValueError('Server execution destination is unresolved: system_stats must identify this local Comfy main.py. No submission is allowed.')
    files={}
    for name in ('server.py','execution.py','nodes.py','comfy_extras/nodes_mask.py'):
        path=Path(root)/name
        if not path.is_file(): raise ValueError('Local native Comfy source is unavailable: '+name)
        files[name]=digest(path.read_bytes())
    server=(Path(root)/'server.py').read_text(); execution=(Path(root)/'execution.py').read_text()
    if 'client_prompt_id = json_data.get("prompt_id")' not in server:
        raise ValueError('This native server cannot establish client-assigned prompt identities; tests are unsupported.')
    targeted = '@routes.post("/api/jobs/{job_id}/cancel")' in server and 'self.prompt_queue.interrupt_if_running(prompt_id)' in server and 'def interrupt_if_running(self, prompt_id)' in execution
    return {'system':{k:stats.get(k) for k in ('os','comfyui_version','python_version','pytorch_version','argv')},
            'devices':[{k:d.get(k) for k in ('name','type','index','vram_total')} for d in system.get('devices',[])],
            'core_source_sha256':files,'targeted_cancel':targeted,
            'trust':'Native local runtime and the named built-in modules; arbitrary custom-node code is not audited.'}


def context(root, name, url, input_dir=None, output_dir=None):
    root=Path(root).resolve(); input_dir=Path(input_dir or root/'input').resolve(); output_dir=Path(output_dir or root/'output').resolve()
    if name not in PROFILES: raise ValueError('Unsupported test configuration. Test locally currently supports only the three original model-free starter packages.')
    receipt=next((r for r in read_receipts(root) if r['name']==name),None)
    if not receipt or receipt['status']!='installed': raise ValueError('Install the starter package completely before preparing a test.')
    workflow=Path(receipt['workflow_path']); data=workflow.read_bytes(); graph=json.loads(data)
    canonical=json.loads((assets()/(name+'.json')).read_text())
    if semantic(graph)!=semantic(canonical):
        raise ValueError('Unsupported or changed test bindings. Restore the original starter settings and graph; Test locally does not guess bindings or alter the workload.')
    wire=Native(url)
    observed={'nodes':wire.request('/object_info'),'system':wire.request('/system_stats')}
    capabilities=wire.request('/renderrepo/local-tests/capabilities')
    if capabilities.get('root')!=str(root) or capabilities.get('input_dir')!=str(input_dir) or capabilities.get('output_dir')!=str(output_dir):
        raise ValueError('Selected server and local configured directories do not match; execution destination is unsupported.')
    identity=runtime_identity(observed,root)
    identity['targeted_cancel']=capabilities.get('targeted_cancel') is True
    identity['loaded_modules']=capabilities.get('modules')
    for typ in {n['type'] for n in graph['nodes']}:
        info=(observed.get('nodes') or {}).get(typ,{})
        loaded=capabilities.get('modules',{}).get(typ)
        allowed=MODULES.get(typ,set())
        if typ=='ImageCompositeMasked': allowed=allowed | {str(root/'comfy_extras/nodes_mask')}
        if typ not in MODULES or info.get('api_node') or info.get('python_module') not in MODULES[typ] or loaded not in allowed:
            raise ValueError('Unsupported execution node/destination: '+typ+'. Only the named built-in, non-API modules are allowed.')
    try:
        from PIL import Image
    except ImportError:
        raise ValueError('Use the existing Comfy Python with Pillow for Test locally; no dependencies are installed automatically.') from None
    inputs=[]
    for node in graph['nodes']:
        if node['type']!='LoadImage': continue
        filename=node['widgets_values'][0]; expected=(assets()/filename).read_bytes(); path=input_dir/filename
        if not path.is_file() or digest(path.read_bytes())!=digest(expected):
            raise ValueError('Restore the bundled starter test input: '+filename+'. No inputs are downloaded or repaired by planning.')
        with Image.open(io.BytesIO(expected)) as image:
            if image.format!='PNG' or image.width>1024 or image.height>1024 or getattr(image,'n_frames',1)!=1:
                raise ValueError('Test inputs must be single PNG images at most 1024×1024')
            inputs.append({'node':str(node['id']),'filename':filename,'sha256':digest(expected),'dimensions':list(image.size),'batch':1})
    return {'package':{'name':name,'version':receipt['version'],'archive_sha256':receipt.get('repository',{}).get('archive_sha256'),
                       'receipt_sha256':digest(encoded(receipt))},
            'workflow_sha256':digest(data),'workflow':graph,'inputs':inputs,'runtime':identity,
            'input_dir':str(input_dir),'output_dir':str(output_dir),'server':wire.url,
            'artifact_verification':'Installed input bytes match RR starter profile SHA-256; package archive identity is recorded when available.'}


def prompt_for(graph):
    prompt={}
    for n in graph['nodes']:
        typ=n['type']; w=n['widgets_values']; fields={}
        if typ=='LoadImage': fields={'image':w[0]}
        elif typ=='ImageScale': fields=dict(zip(('upscale_method','width','height','crop'),w))
        elif typ=='ImageCompositeMasked': fields=dict(zip(('x','y','resize_source'),w))
        elif typ=='SaveImage': fields={'filename_prefix':w[0]}
        else: raise ValueError('Unsupported execution node: '+typ)
        for link in graph['links']:
            if link[3]==n['id']:
                fields[n['inputs'][link[4]]['name']]=[str(link[1]),link[2]]
        prompt[str(n['id'])]={'class_type':typ,'inputs':fields}
    return prompt


def prepare(root,name,url,*,runs=1,deadline=60,input_dir=None,output_dir=None):
    if type(runs) is not int or runs not in (1,2) or type(deadline) is not int or not 1<=deadline<=60:
        raise ValueError('Tests permit one first observed run and one optional recomputed repeat, with a 1–60 second submission deadline each.')
    current=context(root,name,url,input_dir,output_dir); token=uuid.uuid4().hex; folder=directory(root,token); folder.mkdir(parents=True)
    from PIL import Image
    attempts=[]
    for index in range(runs):
        prompt=prompt_for(current['workflow']); overrides=[]; test_inputs=[]
        for item in current['inputs']:
            source=(assets()/item['filename']).read_bytes()
            with Image.open(io.BytesIO(source)) as opened: image=opened.convert('RGB')
            change=None
            if index and item['filename']=='rr-starter-base.png':
                point=(image.width//2,image.height//2); before=image.getpixel(point); after=(before[0]^1,*before[1:]); image.putpixel(point,after)
                change={'pixel':list(point),'before':list(before),'after':list(after)}
            stream=io.BytesIO();image.save(stream,format='PNG');payload=stream.getvalue()
            basename=f'{index+1}-{item["filename"]}'; (folder/basename).write_bytes(payload)
            selected=f'rr-local-tests/{token}/{basename}'
            prompt[item['node']]['inputs']['image']=selected
            test_inputs.append(dict(item,selected=selected,sha256=digest(payload),prepared=basename,change=change))
            overrides.append({'node':item['node'],'field':'image','original':item['filename'],'submitted':selected,'controlled_pixel_change':change})
        prefix=f'rr-local-tests/{token}/run-{index+1}/result'
        save=next(k for k,v in prompt.items() if v['class_type']=='SaveImage')
        original=prompt[save]['inputs']['filename_prefix'];prompt[save]['inputs']['filename_prefix']=prefix
        overrides.append({'node':save,'field':'filename_prefix','original':original,'submitted':prefix})
        attempts.append({'label':'First observed run' if index==0 else 'Recomputed repeat','prompt':prompt,'prompt_sha256':digest(encoded(prompt)),
                         'inputs':test_inputs,'overrides':overrides,'output_node':save,'output_prefix':prefix,
                         'compute_nodes':[k for k,n in prompt.items() if n['class_type'] in {'ImageScale','ImageCompositeMasked'}]})
    operation=next(n for n in attempts[0]['prompt'].values() if n['class_type'] in {'ImageScale','ImageCompositeMasked'})
    controls={k:v for k,v in operation['inputs'].items() if not isinstance(v,list)}
    dimensions={'resize':[160,100],'portrait-crop':[100,200],'overlay':[320,200]}[PROFILES[name]]
    plan={'id':token,'created_at':timestamp(),'context':current,'attempts':attempts,'runs':runs,'deadline_seconds':deadline,
          'configuration':{'profile':PROFILES[name], 'controls':controls,'expected_output_dimensions':dimensions,'batch':1},
          'max_execution_elapsed_seconds':runs*deadline,'max_output_verification_seconds_per_run':15,
          'max_elapsed_before_deadline_reporting_seconds':runs*(deadline+15+5),
          'deadline_limit_note':'Deadline bounds the execution request, not proof of stopping. Network observation can lag by up to a 5-second request timeout; monitoring may continue after the deadline.',
          'recomputation':'Unique private input selections; repeat changes one center pixel of the base image. No shared cache flush, model unload or server restart.',
          'expected_output_directory':str(Path(current['output_dir'])/'rr-local-tests'/token),'warning':WARNING}
    approval=digest(encoded(plan));atomic_json(folder/'plan.json',plan)
    atomic_json(folder/'state.json',{'id':token,'state':'planned','plan_sha256':approval,'created_at':timestamp(),'runs':[]})
    atomic_json(folder/'control.json',{'stop':False})
    return dict(plan=plan,approval_sha256=approval)


def load(root,token):
    folder=directory(root,token); plan=read(folder/'plan.json'); state=read(folder/'state.json')
    if digest(encoded(plan))!=state['plan_sha256']: raise ValueError('Test plan changed; prepare and review a new plan.')
    return folder,plan,state


def changed(plan):
    c=plan['context']
    try:
        now=context(plan['comfy_root'],c['package']['name'],c['server'],c['input_dir'],c['output_dir'])
        return [] if digest(encoded(now))==digest(encoded(c)) else ['Workflow, inputs, package evidence, launch settings, runtime or hardware differs from this recorded test.']
    except (OSError,KeyError) as error: return ['Current configuration unverified: '+str(error)]
    except ValueError as error: return [str(error)]


def status(root,token,check_current=False):
    folder,plan,state=load(root,token)
    busy=active(folder/'worker.lock')
    if not busy and state['state']=='approved' and time.time()-state.get('approved_epoch',time.time())>5:
        state.update(state='unresolved',error='Worker launch was not confirmed; no automatic submission. Prepare a new reviewed attempt.')
        atomic_json(folder/'state.json',state)
    if not busy and state['state'] not in {'planned','approved'} and any(not r.get('terminal_observed') for r in state['runs']):
        try:
            with lease(folder/'worker.lock'):
                state=reconcile_run(root,token)
        except (ValueError,OSError) as error:
            state=dict(state,reconciliation_error=str(error))
    if not busy and state['state']=='running' and state['runs'] and all(r.get('terminal_observed') for r in state['runs']) and len(state['runs'])<plan['runs']:
        state.update(state='unresolved',error='Worker ended before all reviewed runs were submitted. No automatic resubmission; prepare a new attempt.')
        atomic_json(folder/'state.json',state)
    result=dict(state,plan=plan,worker_active=busy)
    if check_current:
        result['historical_differences']=changed(dict(plan,comfy_root=str(Path(root).resolve())))
        result['historical_comparison']='unknown' if any(v.startswith('Current configuration unverified:') for v in result['historical_differences']) else 'different' if result['historical_differences'] else 'same'
    return result


def approve(root,token,approval):
    folder,plan,state=load(root,token)
    with lease(folder/'worker.lock'):
        folder,plan,state=load(root,token)
        if state['state']!='planned' or approval!=state['plan_sha256']:
            raise ValueError('Explicit approval must match the reviewed plan; reruns require a new plan.')
        differences=changed(dict(plan,comfy_root=str(Path(root).resolve())))
        if differences: raise ValueError('Test configuration changed; prepare and approve a new plan. '+ '; '.join(differences))
        output_root=Path(plan['context']['output_dir']).resolve()
        output_namespace=output_root/'rr-local-tests'/token
        if not output_namespace.resolve().is_relative_to(output_root) or output_namespace.exists():
            raise ValueError('Private output destination already exists or escapes the reviewed output directory.')
        for attempt in plan['attempts']:
            for item in attempt['inputs']:
                if digest((folder/item['prepared']).read_bytes())!=item['sha256']: raise ValueError('Prepared test input changed')
                target=Path(plan['context']['input_dir'])/item['selected']
                if target.exists(): raise ValueError('Test input destination already exists')
        state.update(state='approved',approved_at=timestamp(),approved_epoch=time.time());atomic_json(folder/'state.json',state)
    return state


def launch(root,token,python=None):
    folder,plan,state=load(root,token)
    if state['state']!='approved': raise ValueError('Only a newly approved plan can launch a submission worker.')
    with (folder/'worker.log').open('ab') as log:
        return subprocess.Popen([str(python or sys.executable),'-u','-m','renderrepo.local_test', 'worker','--comfy-dir',str(root),'--id',token],
                                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)


def evidence(history,attempt):
    messages=history.get('status',{}).get('messages',[])
    def event(kind): return next((d for t,d in messages if t==kind),None)
    start=event('execution_start'); end=event('execution_success') or event('execution_error') or event('execution_interrupted')
    cached=event('execution_cached'); cache_nodes=[str(n) for n in cached.get('nodes',[])] if cached else None
    success=history.get('status',{}).get('status_str')=='success'
    computed=success and cache_nodes is not None and all(n not in cache_nodes for n in attempt['compute_nodes'])
    begin=start.get('timestamp') if start else None;finish=end.get('timestamp') if end else None
    valid=lambda value:type(value) in (int,float) and value>0
    milliseconds=finish-begin if valid(begin) and valid(finish) and finish>=begin else None
    reliable=milliseconds is not None and computed
    queued=history.get('prompt',[None]*4)[3].get('create_time') if len(history.get('prompt',[]))>3 and isinstance(history['prompt'][3],dict) else None
    return {'cache_nodes':cache_nodes,'compute_nodes':attempt['compute_nodes'],
            'computation':'Recomputed' if computed else 'Cached' if cache_nodes is not None and any(n in cache_nodes for n in attempt['compute_nodes']) else 'Computation unverified',
            'execution_seconds':milliseconds/1000 if reliable else None,
            'server_execution_wall_seconds':milliseconds/1000 if milliseconds is not None and milliseconds>=0 else None,
            'queued_seconds':(begin-queued)/1000 if valid(begin) and valid(queued) and begin>=queued else None,
            'timing_source':'Comfy history event timestamps (millisecond resolution); includes execution overhead, not isolated kernel time' if milliseconds is not None else 'Unknown: reliable native execution timestamps unavailable',
            'included_in_compute_summary':reliable,'messages':messages}


def verify_outputs(folder,plan,attempt,history):
    from PIL import Image, ImageChops
    started=time.monotonic(); expected_inputs=[]
    for item in attempt['inputs']:
        with Image.open(folder/item['prepared']) as image: expected_inputs.append(image.convert('RGB'))
    name=plan['context']['package']['name']; base=expected_inputs[0]
    if name.endswith('resize'):
        expected=Image.new('RGB',(160,100));expected.putdata([base.getpixel((2*x+1,2*y+1)) for y in range(100) for x in range(160)])
    elif name.endswith('portrait-crop'): expected=base.crop((110,0,210,200))
    else:
        expected=base.copy();expected.paste(expected_inputs[1],(224,120))
    outputs=history.get('outputs',{}).get(attempt['output_node'],{}).get('images',[])
    if len(outputs)!=1: raise ValueError('Expected exactly one batch-1 PNG output')
    image=outputs[0]; relative=Path(image.get('subfolder',''))/image.get('filename','')
    prefix=Path(attempt['output_prefix'])
    if image.get('type')!='output' or relative.parent!=prefix.parent or not relative.name.startswith(prefix.name+'_') or relative.suffix!='.png':
        raise ValueError('Output destination differs from the reviewed private prefix')
    root=Path(plan['context']['output_dir']).resolve(); path=(root/relative).resolve()
    if not path.is_relative_to(root) or path.stat().st_size>10*1024*1024: raise ValueError('Unsafe or oversized output')
    payload=path.read_bytes()
    with Image.open(io.BytesIO(payload)) as opened:
        actual=opened.convert('RGB')
        if actual.size!=expected.size or ImageChops.difference(actual,expected).getbbox() is not None:
            raise ValueError('Output pixels do not match the advertised starter operation')
    retained=folder/(attempt['label'].replace(' ','-')+'.png');retained.write_bytes(payload)
    elapsed=time.monotonic()-started
    if elapsed>15: raise ValueError('Output verification exceeded the reviewed 15-second budget')
    return {'path':str(path),'retained':str(retained),'sha256':digest(payload),'dimensions':list(actual.size),'batch':1,
            'verification':'All decoded RGB pixels match independent expected operation','verification_seconds':elapsed}


def sample(wire):
    try:
        stats=wire.request('/system_stats')
        return {'at':timestamp(),'whole_device_observations':stats.get('devices'),
                'whole_host_ram_free':stats.get('system',{}).get('ram_free'),
                'process_peak_bytes':None,'note':'Whole-device/host samples, not process memory or exact peaks. No fit claim.'}
    except (OSError,ValueError): return {'at':timestamp(),'memory':'Unknown','process_peak_bytes':None}


def reconcile_run(root,token,wire=None):
    folder,plan,state=load(root,token); wire=wire or Native(plan['context']['server'])
    for run in state['runs']:
        if run.get('terminal_observed'): continue
        job=wire.request('/history/'+run['prompt_id']).get(run['prompt_id'])
        if job:
            index=run['index']; attempt=plan['attempts'][index]; run['history']=job;run['evidence']=evidence(job,attempt)
            run['finished_observed_at']=timestamp();run['terminal_observed']=True;run['execution_may_continue']=False
            run['total_elapsed_seconds']=max(0,time.time()-run['submitted_epoch'])
            success=job.get('status',{}).get('status_str')=='success'
            native_prompt=job.get('prompt',[])
            if len(native_prompt)<3 or native_prompt[1]!=run['prompt_id'] or native_prompt[2]!=attempt['prompt']:
                success=False;run['verification_error']='Native history differs from the exact reviewed prompt.'
                run['evidence'].update(computation='Computation unverified',execution_seconds=None,included_in_compute_summary=False)
            if success:
                try: run['output']=verify_outputs(folder,plan,attempt,job)
                except (OSError,ValueError) as error: run['verification_error']=str(error);success=False
            interrupted=any(t=='execution_interrupted' for t,_ in job.get('status',{}).get('messages',[]))
            observed='completed' if success else 'cancelled' if interrupted else 'failed'
            run['terminal_outcome']=observed
            messages=run['evidence']['messages']
            end=next((d.get('timestamp') for t,d in messages if t in {'execution_success','execution_error','execution_interrupted'}),None)
            if end is not None and end/1000-run['submitted_epoch']>plan['deadline_seconds'] and not run.get('stop_at'):
                run['deadline_at']=timestamp()
            if not run.get('deadline_at') and not run.get('stop_at'): run['state']=observed
            elif run.get('deadline_at'): run['state']='timed_out'
            else: run['state']='cancelled'
            if run['state']!='completed':
                run['evidence']['included_in_compute_summary']=False
                run['evidence']['exclusion_reason']='Run was cancelled, failed or exceeded its deadline.'
            if not success: run['error']=job.get('status',{}).get('messages',[])
            run['total_elapsed_seconds']=max(0,time.time()-run['submitted_epoch'])
        else:
            control=read(folder/'control.json')
            expired=time.time()-run['submitted_epoch']>=plan['deadline_seconds']
            if (control['stop'] or expired) and not run.get('cancellation_requested_at'):
                run['cancellation_requested_at']=timestamp()
                if control['stop']: run.update(stop_at=timestamp(),state='cancelled')
                else: run.update(deadline_at=timestamp(),state='timed_out')
                state['state']=run['state'];run['execution_may_continue']=True
                atomic_json(folder/'state.json',state)
                try: run['cancellation']=cancel_current(wire,plan,run)
                except (OSError,ValueError) as error: run['cancellation']={'error':str(error),'warning':'Current operation may continue.'}
                if run['cancellation'].get('state')=='queued_removed':
                    run.update(terminal_observed=True,execution_may_continue=False,terminal_outcome='removed_from_queue')
    if state['runs'] and all(r.get('terminal_observed') for r in state['runs']):
        last=state['runs'][-1]
        if last['state']!='completed' or len(state['runs'])==plan['runs']: state['state']=last['state']
        elif state.get('stop_further_submissions'): state['state']='unresolved'
    atomic_json(folder/'state.json',state)
    return state


def cancel_current(wire,plan,run):
    # Never use /interrupt, /free, clear=true, process termination, or broad job lists.
    return wire.request('/renderrepo/local-tests/'+plan['id']+'/cancel-owned', {'prompt_id':run['prompt_id']})


def cancel_owned(root,token,prompt_id,queue):
    _,_,state=load(root,token)
    if not any(r.get('prompt_id')==prompt_id for r in state['runs']):
        raise ValueError('Job does not belong to this local test.')
    if queue.delete_queue_item(lambda item:item[1]==prompt_id):
        return {'state':'queued_removed','execution_may_continue':False}
    target=getattr(queue,'interrupt_if_running',None)
    if callable(target) and target(prompt_id):
        return {'state':'targeted_interrupt_requested','execution_may_continue':True}
    return {'state':'not_interrupted','execution_may_continue':True,
            'message':'Safe targeted interruption was unavailable or not dispatched. Continue monitoring/reconciliation.'}


def stop(root,token):
    folder,plan,state=load(root,token);atomic_json(folder/'control.json',{'stop':True,'requested_at':timestamp()})
    return {'id':token,'stop_requested':True,'message':'Further submissions stopped. Only this test’s job may be cancelled. A running operation may continue until server confirmation.'}


def worker(root,token):
    folder,plan,state=load(root,token)
    with lease(folder/'worker.lock'):
        # Reload under the lease: a second worker must never repeat a submission.
        folder,plan,state=load(root,token)
        wire=Native(plan['context']['server'])
        if state['state']!='approved':
            return reconcile_run(root,token,wire)  # reconciliation NEVER submits, including an approved but partially used plan
        with lease(store(root)/'submission.lock'):
            for index,attempt in enumerate(plan['attempts']):
                state=read(folder/'state.json')
                if read(folder/'control.json')['stop']:
                    state['state']='cancelled';atomic_json(folder/'state.json',state);break
                if changed(dict(plan,comfy_root=str(Path(root).resolve()))):
                    state.update(state='unresolved',error='Configuration changed after approval; no further submissions.');atomic_json(folder/'state.json',state);break
                output_root=Path(plan['context']['output_dir']).resolve()
                if not (output_root/attempt['output_prefix']).resolve().is_relative_to(output_root):
                    raise ValueError('Private output destination changed after approval; no submission.')
                for item in attempt['inputs']:
                    payload=(folder/item['prepared']).read_bytes()
                    if digest(payload)!=item['sha256']:raise ValueError('Prepared input changed after approval; no submission.')
                    input_root=Path(plan['context']['input_dir']).resolve();target=input_root/item['selected']
                    if not target.resolve().is_relative_to(input_root):raise ValueError('Private test input destination escapes the reviewed input directory')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('xb') as output: output.write(payload)
                before_memory=sample(wire)
                run={'index':index,'label':attempt['label'],'state':'submitting','prompt_id':str(uuid.uuid4()),
                     'submitted_at':timestamp(),'submitted_epoch':time.time(),'execution_may_continue':True,'memory_samples':[before_memory]}
                state['runs'].append(run);state['state']='running';atomic_json(folder/'state.json',state)
                try:
                    response=wire.request('/prompt',{'prompt':attempt['prompt'],'prompt_id':run['prompt_id'],'extra_data':{'rr_local_test':token}})
                    if response.get('prompt_id')!=run['prompt_id']: raise ValueError('Ambiguous submission acknowledgement; do not resubmit.')
                    run['acknowledgement']=response;run['state']='submitted';atomic_json(folder/'state.json',state)
                except (OSError,ValueError) as error:
                    if isinstance(error,HTTPError) and error.code in {400,401,403,404,405,413,422}:
                        run.update(state='failed',terminal_observed=True,execution_may_continue=False,
                                   terminal_outcome='submission_rejected',error=str(error),response_body=error.read(65536).decode(errors='replace'))
                        state['state']='failed';atomic_json(folder/'state.json',state);return state
                    run.update(state='unresolved',error=str(error));state['state']='unresolved';atomic_json(folder/'state.json',state)
                    run['ambiguous_acknowledgement']=True
                    state['stop_further_submissions']=True
                    atomic_json(folder/'state.json',state)  # monitor the known intended id; never resubmit
                while True:
                    state=reconcile_run(root,token,wire);run=state['runs'][-1]
                    if run.get('terminal_observed'): break
                    # After the deadline continue observing this known job, never submit another.
                    time.sleep(.2)
                run['memory_samples'].append(sample(wire));run['total_elapsed_seconds']=max(0,time.time()-run['submitted_epoch']);run['finished_at']=timestamp();atomic_json(folder/'state.json',state)
                if run['state']!='completed' or state.get('stop_further_submissions'):
                    state['state']='unresolved' if state.get('stop_further_submissions') else run['state']
                    atomic_json(folder/'state.json',state);break
            return read(folder/'state.json')


def run_worker(root,token):
    try: return worker(root,token)
    except (OSError,ValueError) as error:
        # A live worker owns state. Never overwrite it if duplicate lease acquisition failed.
        folder=directory(root,token)
        if active(folder/'worker.lock'): raise
        with lease(folder/'worker.lock'):
            state=read(folder/'state.json');state.update(state='unresolved',error=str(error));atomic_json(folder/'state.json',state)
        return state


def reconcile(root,token):
    folder,_,_=load(root,token)
    with lease(folder/'worker.lock'):
        return reconcile_run(root,token)


def main(argv=None):
    parser=argparse.ArgumentParser(prog='renderrepo test')
    parser.add_argument('action',choices=['plan','start','status','stop','reconcile','worker'])
    parser.add_argument('--comfy-dir',required=True,type=Path);parser.add_argument('--name');parser.add_argument('--comfy-url')
    parser.add_argument('--input-dir',type=Path);parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--id');parser.add_argument('--approve');parser.add_argument('--runs',type=int,default=1)
    parser.add_argument('--deadline',type=int,default=60);parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='plan': result=prepare(args.comfy_dir,args.name,args.comfy_url,runs=args.runs,deadline=args.deadline,input_dir=args.input_dir,output_dir=args.output_dir)
        elif args.action=='start': approve(args.comfy_dir,args.id,args.approve);launch(args.comfy_dir,args.id);result=status(args.comfy_dir,args.id)
        elif args.action=='status': result=status(args.comfy_dir,args.id,True)
        elif args.action=='stop': result=stop(args.comfy_dir,args.id)
        elif args.action=='reconcile': result=reconcile(args.comfy_dir,args.id)
        else: result=run_worker(args.comfy_dir,args.id)
        if args.json: print(json.dumps(result,indent=2))
        elif args.action=='plan':
            p=result['plan'];print(f"Test plan {p['id']} — {args.name}\nServer: {p['context']['server']}\nRuns: {p['runs']}; deadline: {p['deadline_seconds']}s per submission; review limit: {p['max_elapsed_before_deadline_reporting_seconds']}s\nConfiguration: {p['configuration']}\nInputs: {p['context']['inputs']}\nRecomputation: {p['recomputation']}\n{p['deadline_limit_note']}\nOutputs: {p['expected_output_directory']}\n{WARNING}\nApproval SHA-256: {result['approval_sha256']}\nExact prompts and overrides: {directory(args.comfy_dir,p['id'])/'plan.json'}")
        else:
            print(result.get('state',result.get('message','')))
            for run in result.get('runs',[]): print(run['label'],run['state'],json.dumps(run.get('evidence',{})),run.get('output',{}))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        print('Test locally: '+str(error),file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
