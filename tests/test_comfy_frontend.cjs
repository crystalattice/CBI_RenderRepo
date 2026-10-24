// SPDX-License-Identifier: GPL-3.0-only
// Dependency-free DOM harness. Real browser/editor acceptance is a separate check.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tagName=tag.toUpperCase();this.children=[];this.value='';this.disabled=false;this.checked=false;this.listeners={};this.selectedOptions=[];this.style={}; }
  setAttribute(name,value) { this[name]=value; }
  removeAttribute(name) { delete this[name]; }
  append(...items) { for (const item of items) { if(typeof item==='object'){if(item.parent)item.parent.children=item.parent.children.filter(x=>x!==item);item.parent=this;}this.children.push(item); } }
  replaceChildren(...items) { this.children=[];this.append(...items); }
  get previousElementSibling() { const items=this.parent?.children||[];return items[items.indexOf(this)-1]; }
  addEventListener(name, action) { this.listeners[name]=action; }
  click() { return this.onclick?.(); }
  getAttribute(name){return this[name];}
  focus(){this.focused=true;}
  showModal() { this.open=true; }
  close() { this.open=false; }
  remove() { this.parent.children=this.parent.children.filter(x=>x!==this); }
  querySelectorAll(selector) { const tags=selector.split(',').map(x=>x.trim().toUpperCase());return all(this).filter(x=>tags.includes(x.tagName)); }
}
function all(root) { return [root,...root.children.filter(x=>x instanceof Element).flatMap(all)]; }
function harness(customNodes = [], installationStatus = null, installations = [], resumeResult = {state:'running'}, gates = {}) {
  const body=new Element('body'),requests=[],timers=[];let extension,loaded,planValid=true;const editorCalls=[];let inputChoices=['pre-existing.png'];
  let transportError=null,httpFailure=null,bodyError=null;
  let repositoryData=gates.repositoryData || {sources:[],packages:[],updates:[],operations:[],warnings:[]}, repositoryOperation={token:'repo-op',state:'done',progress:'Refresh finished',sources:[]}, repositoryError=null;
  const document={body,createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),getElementById:id=>all(body).find(x=>x.id===id)};
  const workflow=gates.workflow || {nodes:[{id:1,type:'SaveImage'}],definitions:{subgraphs:[{id:'sub',nodes:[{id:2,type:'SaveImage'}]}]}};
  const app={registerExtension:value=>{extension=value;},graph:{serialize:()=>workflow},refreshComboInNodes:async()=>{editorCalls.push('refresh');if(gates.refreshError)throw new Error(gates.refreshError);inputChoices.push('rr-core-demo-example.png');},loadGraphData:async graph=>{editorCalls.push('load');if(graph.nodes.some(n=>n.type==='LoadImage'&&!inputChoices.includes(n.widgets_values[0])))throw new Error('Bundled input absent from stale choices');loaded=graph;}};
  const api={apiURL:route=>'/api'+route,fetchApi:async(url,options)=>{
    requests.push({url,options});
    if(gates[url])await gates[url];
    if(transportError&&url.endsWith('/preview'))throw new TypeError(transportError);
    if(httpFailure&&url.endsWith('/preview'))return {ok:false,status:httpFailure.status,statusText:'Rejected',url:'http://127.0.0.1:8188/api/renderrepo/preview',json:async()=>({error:httpFailure.error})};
    let data={};
    if(url.endsWith('/repositories'))data=repositoryData;
    else if(url.includes('/repositories/operation/'))data=repositoryOperation;
    else if(url.endsWith('/repositories/refresh')||url.endsWith('/repositories/retrieve')) {
      if(repositoryError)return {ok:false,status:400,json:async()=>({error:repositoryError})};
      data={token:'repo-op',state:'running'};
    }
    else if(url.endsWith('/repositories/source'))data=repositoryData;
    else if(url.endsWith('/local-tests/plan'))data=gates.localTestPlan;
    else if(url.endsWith('/local-tests'))data={tests:[]};
    else if(url.includes('/local-tests/'))data=gates.localTestStatus;
    else if(url.endsWith('/readiness'))data=gates.readinessReport || {state:'ready',checked_at:'2026-10-17T00:00:00Z',runtime:{},dependency_source:'catalog',server:'http://127.0.0.1:1',checks:[],limitation:'Declared checks only; not execution'};
    else if(url.endsWith('/config'))data={python:'/comfy/python',custom_nodes:customNodes,installations};
    else if(url.endsWith('/local-files'))data={files:[{name:'local.safetensors',path:'/models/my LoRA/local.safetensors'}],truncated:false};
    else if(url.endsWith('/history'))data={installations};
    else if(url.includes('/resume/'))data=resumeResult;
    else if(url.includes('/resolve/'))data=gates.resolutionResult || gates.reviewResult;
    else if(url.endsWith('/preview')||url.includes('/review/'))data=gates.reviewResult || {review_id:'review-hash',session:'token',name:'demo',version:'1.0',archive_sha256:'hash',plan:{log:'REUSE: weights\nWORKFLOW: demo.json',scope:planValid?{workflow:{path:'/comfy/workflows/demo.json'}}:null},can_install:planValid};
    else if(url.includes('/status/export-token'))data={state:'exported',result:{exit_code:0,log:'Packed'}};
    else if(url.includes('/status/'))data=installationStatus || {state:'done',result:{exit_code:0,log:'INSTALLED'},workflow_available:true,restart_required:false};
    else if(url.includes('/workflow/')||url.includes('/installed-workflow?'))data=workflow;
    else if(url.endsWith('/export'))data={session:'export-token',log:'Packed'};
    if(!gates.legacyBackend){const scope={summary:'Add one workflow. No downloads.',downloads:0,reused:0,decisions:[]};if(data.plan&&!data.store_scope)data={...data,store_scope:scope};if(data.result?.plan&&!data.result.store_scope)data={...data,result:{...data.result,store_scope:scope}};}
    return {ok:true,status:200,json:async()=>{if(bodyError&&url.endsWith('/preview'))throw new TypeError(bodyError);return data;},blob:async()=>Buffer.from('archive')};
  }};
  const code=fs.readFileSync(path.join(__dirname,'../extensions/comfyui_renderrepo/web/renderrepo.js'),'utf8').replace(/^import .*;\n/gm,'').replace('button.onclick = openStorefront;', 'button.onclick = openPanel;'); // Exercise retained legacy panel explicitly.
  vm.runInNewContext(code,{document,app,api,location:{href:'http://127.0.0.1:8188/'},URL:class extends URL{static createObjectURL(){return 'blob:test';}static revokeObjectURL(){}},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}});
  extension.setup();document.getElementById('renderrepo-launcher').click();
  return {editorCalls,get:id=>document.getElementById(id),requests,workflow,get loaded(){return loaded;},set planValid(value){planValid=value;},
    set bodyError(value){bodyError=value;},set transportError(value){transportError=value;},set httpFailure(value){httpFailure=value;},
    set repositoryData(value){repositoryData=value;},set repositoryOperation(value){repositoryOperation=value;},set repositoryError(value){repositoryError=value;},
    set installationStatus(value){installationStatus=value;},async poll(){timers.shift()?.();await tick();}};
}
async function tick(){for(let i=0;i<80;i++)await Promise.resolve();}

test('local picker builds a source mapping and invalidates approval',async()=>{
  const h=harness();await tick();h.get('rr-import').files=[Buffer.from('rrpkg')];await h.get('rr-import').onchange();
  h.get('rr-local-dependency').value='cloud-name.safetensors';h.get('rr-file-query').value='LoRA';
  await h.get('rr-file-search').onclick();
  h.get('rr-file-results').value='/models/my LoRA/local.safetensors';

  h.get('rr-use-file').onclick();
  assert.equal(h.get('rr-sources').value,'cloud-name.safetensors=/models/my LoRA/local.safetensors');
  assert.equal(h.get('rr-consent'),undefined);
  assert.equal(h.get('rr-install').disabled,true);
});

test('opening a new panel reconnects to active install without reapproving or discarding it',async()=>{
  const h=harness([], {state:'running',progress:'Paused',elapsed_seconds:10,
    control:{action:'pause',rate_mbps:2},transfer:{filename:'model',bytes:1,total_bytes:4,state:'paused'}},
    [{session:'existing-token',state:'running'}]);
  await tick();
  assert.ok(h.requests.some(x=>x.url.endsWith('/status/existing-token')));
  assert.equal(h.get('rr-pause').textContent,'Resume downloads');
  assert.equal(h.get('rr-rate').value,2);
  assert.equal(h.requests.some(x=>x.url.includes('/install/')),false);
  await h.get('rr-close-top').onclick();
  assert.equal(h.requests.some(x=>x.url.includes('/discard/')),false);
});

test('running install shows current file percentage, size, and streamed output',async()=>{
  const h=harness([], {state:'running',progress:'Downloading model',elapsed_seconds:12,
    transfer:{filename:'model.safetensors',bytes:1e9,total_bytes:4e9,complete:false}});
  await tick();h.get('rr-import').files=[Buffer.from('rrpkg')];await h.get('rr-import').onchange();

  await h.get('rr-install').onclick();await tick();
  assert.equal(h.get('rr-install-progress').hidden,false);
  assert.equal(h.get('rr-install-progress').value,25);
  assert.equal(h.get('rr-plan').textContent,'Downloading model');
  await h.get('rr-pause').onclick();
  assert.equal(JSON.parse(h.requests.filter(r=>r.url.includes('/download-control/')).at(-1).options.body).action,'pause');
  h.get('rr-rate').value=2;await h.get('rr-rate').onchange();await tick();
  assert.equal(JSON.parse(h.requests.filter(r=>r.url.includes('/download-control/')).at(-1).options.body).rate_mbps,2);
  await h.get('rr-pause').onclick();
  assert.equal(JSON.parse(h.requests.filter(r=>r.url.includes('/download-control/')).at(-1).options.body).action,'run');
  await h.get('rr-cancel').onclick();
  assert.equal(JSON.parse(h.requests.filter(r=>r.url.includes('/download-control/')).at(-1).options.body).action,'cancel');
});

test('review, explicit approval, install, and unchanged editor graph handoff',async()=>{
  const h=harness();await tick();const file=h.get('rr-import');file.files=[Buffer.from('rrpkg')];await file.onchange();
  assert.equal(h.get('rr-install').disabled,false);
  assert.equal(h.requests.some(x=>x.url.includes('/install/')),false);
  assert.equal(h.get('rr-install').textContent,'Install');
  await h.get('rr-install').onclick();await tick();assert.equal(h.get('rr-open').disabled,false);
  assert.deepEqual(JSON.parse(h.requests.find(x=>x.url.includes('/install/')).options.body),{approved:true,review_id:'review-hash'});
  await h.get('rr-open').onclick();assert.equal(h.loaded,h.workflow);
  assert.ok(h.loaded.definitions.subgraphs.length);
  assert.ok(h.requests.every(x=>x.options.headers['X-RenderRepo-Request']==='1'));
});

test('editing options invalidates prior approval; close retains the imported package',async()=>{
  const h=harness();await tick();h.get('rr-import').files=[Buffer.from('rrpkg')];await h.get('rr-import').onchange();

  h.get('rr-sources').value='model.bin=https://example.org/model';h.get('rr-sources').listeners.input();
  assert.equal(h.get('rr-consent'),undefined);assert.equal(h.get('rr-install').disabled,true);
  await h.get('rr-close-top').onclick();assert.equal(h.requests.some(x=>x.url.endsWith('/discard/token')),false);
  assert.equal(h.requests.some(x=>x.url.includes('/install/')),false);
});

test('export sends the current editor graph and explicit node/input choices',async()=>{
  const h=harness();await tick();h.get('rr-nodes').selectedOptions=[{value:'demo-node'}];h.get('rr-inputs').value='input.ppm';
  await h.get('rr-export').onclick();
  const sent=JSON.parse(h.requests.find(x=>x.url.endsWith('/export')).options.body);
  assert.deepEqual(sent.workflow,h.workflow);assert.deepEqual(sent.custom_nodes,['demo-node']);assert.deepEqual(sent.bundle_inputs,['input.ppm']);
  assert.ok(h.requests.some(x=>x.url.endsWith('/archive/export-token')));
  assert.ok(h.requests.some(x=>x.url.endsWith('/discard/export-token')));
});


test('unavailable repositories remain visible with reasons and cannot be exported',async()=>{
  const h=harness([{name:'clean',revision:'a'.repeat(40),exportable:true},
    {name:'GGUF',revision:'b'.repeat(40),exportable:false,unavailable_reason:'local changes (including untracked files)'},
    {name:'zip-node',exportable:false,unavailable_reason:'missing Git history; missing origin URL'}]);
  await tick();const options=h.get('rr-nodes').children;
  assert.equal(options.length,3);assert.equal(options[0].disabled,false);assert.equal(options[1].disabled,true);
  assert.match(options[1].textContent,/local changes/);
  assert.match(h.get('rr-node-notice').textContent,/1 of 3/);
  assert.equal(h.get('rr-unavailable-nodes').children.length,2);
  h.get('rr-nodes').selectedOptions=options;
  await h.get('rr-export').onclick();
  const sent=JSON.parse(h.requests.find(x=>x.url.endsWith('/export')).options.body);
  assert.deepEqual(sent.custom_nodes,['clean']);
});

test('empty custom-node inventory explains the empty list',async()=>{
  const h=harness();await tick();assert.match(h.get('rr-node-notice').textContent,/No installed/);
});

test('interrupted history reopens and resumes without another upload or approval',async()=>{
  const h=harness([], {state:'interrupted',result:{exit_code:2,log:'Interrupted'},
    options:{artifact_dirs:['/models'],sources:['tiny=http://localhost/tiny'],python_dependencies:false}},
    [{session:'saved',name:'tiny package',version:'2.0',state:'interrupted',last_activity:'2026-10-09T12:00:00Z',
      transfer:{filename:'tiny',bytes:2,total_bytes:4}}]);
  await tick();
  assert.match(h.get('rr-history').children[0].children[0].textContent,/tiny package 2.0.*interrupted.*50.0%/);
  await all(h.get('rr-history')).find(x=>x.textContent==='View status / report').onclick();await tick();
  assert.equal(h.get('rr-sources').value,'tiny=http://localhost/tiny');
  const resume=all(h.get('rr-history')).find(x=>/^(Retry|Resume) installation$/.test(x.textContent));
  await resume.onclick();await tick();
  assert.ok(h.requests.some(x=>x.url.endsWith('/resume/saved')));
  assert.equal(h.requests.some(x=>x.url.endsWith('/preview')||x.url.includes('/install/')),false);
  await h.get('rr-close-top').onclick();
  h.get('renderrepo-launcher').click();await tick();
  assert.ok(h.get('rr-history'));
  assert.equal(h.requests.some(x=>x.url.includes('/discard/')),false);
});

test('expanded recovery plan requires displaying and explicitly approving revised scope',async()=>{
  const h=harness([], {state:'interrupted',result:{exit_code:2,log:'Interrupted'}},
    [{session:'saved',name:'tiny',state:'interrupted'}],
    {session:'saved',name:'tiny',version:'1',archive_sha256:'hash',approval_required:true,
      can_install:true,plan:{log:'DOWNLOAD: revised destination /new-models'}});
  await tick();
  await all(h.get('rr-history')).find(x=>/^(Retry|Resume) installation$/.test(x.textContent)).onclick();await tick();
  assert.match(h.get('rr-plan').textContent,/revised destination/);
  assert.equal(h.get('rr-consent'),undefined);
  assert.equal(h.get('rr-install').disabled,false);
  assert.equal(h.requests.some(x=>x.url.includes('/install/')),false);
});

test('failed/completed history exposes report, workflow location and installed graph',async()=>{
  const h=harness([], {state:'done',result:{exit_code:3,log:'Readable result report'},workflow_available:true,
    workflow_path:'/comfy/user/default/workflows/tiny.json'},
    [{session:'saved',name:'tiny',state:'done',outcome:'failed',workflow_available:true}]);
  await tick();
  await all(h.get('rr-history')).find(x=>x.textContent==='View status / report').onclick();await tick();
  assert.match(h.get('rr-plan').textContent,/Readable result report/);
  assert.match(h.get('rr-plan').textContent,/Workflow location:.*tiny.json/);
  await all(h.get('rr-history')).find(x=>x.textContent==='Open installed workflow').onclick();
  assert.equal(h.loaded,h.workflow);
});

test('corrupt history stays visible and can be explicitly removed without hiding other entries',async()=>{
  const h=harness([], null, [{session:'bad',name:'Unreadable installation',state:'corrupt',error:'Missing job.json'},
    {session:'good',name:'good',state:'done'}]);await tick();
  assert.equal(h.get('rr-history').children.length,2);
  assert.match(h.get('rr-history').children[0].children[0].textContent,/Missing job.json/);
  await all(h.get('rr-history').children[0]).find(x=>x.tagName==='BUTTON').onclick();
  assert.ok(h.requests.some(x=>x.url.endsWith('/discard/bad')));
  assert.equal(h.requests.some(x=>x.url.includes('/install/')||x.url.includes('/download-control/')),false);
});

test('pause requested remains distinct from acknowledged paused state beside progress',async()=>{
  const h=harness([], {state:'running',control:{action:'pause',rate_mbps:1},
    transfer:{filename:'tiny',bytes:1,total_bytes:4,state:'transferring'}}, [{session:'active',state:'running'}]);
  await tick();h.get('rr-tab-activity').onclick();assert.match(h.get('rr-install-status').textContent,/Pausing…/);
  h.installationStatus={state:'running',control:{action:'pause',rate_mbps:1},
    transfer:{filename:'tiny',bytes:1,total_bytes:4,state:'paused'}};
  await h.poll();assert.match(h.get('rr-install-status').textContent,/Paused/);
  assert.equal(h.get('rr-install-progress').hidden,false);
  assert.equal(h.get('rr-pause').disabled,false);
});

test('restart shows interrupted prior worker and disables duplicate execution while monitoring controls',async()=>{
  const h=harness([], {state:'interrupted',worker_active:true,control:{action:'pause',rate_mbps:1},
    transfer:{filename:'tiny',bytes:1,total_bytes:4,state:'paused'}},
    [{session:'old-worker',name:'tiny',state:'interrupted',worker_active:true}]);await tick();h.get('rr-tab-activity').onclick();
  assert.match(h.get('rr-install-status').textContent,/previous worker is still active/);
  assert.equal(all(h.get('rr-history')).find(x=>/^(Retry|Resume) installation$/.test(x.textContent)).disabled,true);
  assert.equal(h.get('rr-pause').disabled,false);
});


const indexed = {name:'demo',version:'1.0',description:'Clothing reference workflow',repository:'personal',uri:'file:///pkg/demo.rrpkg',sha256:'a'.repeat(64),size_bytes:100,cache_stale:false,installed_version:null};
async function selectRepo(h, version='1.0', source='personal') {
  const row=all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row'&&e['data-catalog']===source);
  await row.click();await tick();
}

test('repository identities stay separate and Install inspects without dependency approval',async()=>{
  const h=harness();await tick();
  h.repositoryData={sources:[],packages:[indexed,{...indexed,repository:'other'}],updates:[],operations:[],warnings:[]};
  await h.get('rr-repository-search').onclick();
  assert.equal(all(h.get('rr-repository-results')).filter(e=>e.className==='rr-store-row').length,2);
  assert.equal(h.get('rr-repository-consent'),undefined);
  h.repositoryOperation={state:'done',kind:'archive',progress:'Archive inspected',sources:[],result:{session:'repository-job',name:'demo',version:'1.0',archive_sha256:indexed.sha256,manifest:{artifacts:[]},plan:{log:'WORKFLOW: demo.json'},can_install:true}};
  await selectRepo(h);
  const sent=JSON.parse(h.requests.find(r=>r.url.endsWith('/repositories/retrieve')).options.body);
  assert.deepEqual(sent,{name:'demo',version:'1.0',source:'personal',sha256:indexed.sha256});
  assert.equal(h.get('rr-install').disabled,false);assert.equal(h.get('rr-consent'),undefined);
  assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);

  await selectRepo(h,'1.0','other');
  assert.equal(h.get('rr-install').disabled,false,'New review offers its own explicit approval action');
  assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
});
test('source refresh preserves search and does not acquire dependencies',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[indexed],installed:[],updates:[],operations:[],warnings:[]}});await tick();
 h.get('rr-repository-query').value='clothing';h.get('rr-repository-query').oninput();
 await h.get('rr-repository-refresh').onclick();await tick();assert.equal(h.get('rr-close-top').disabled,false);
 assert.equal(h.get('rr-repository-query').value,'clothing');assert.ok(!h.requests.some(r=>r.url.includes('/install/')));
});

test('repository retrieval errors are actionable beside archive controls and do not retain approval',async()=>{
  const h=harness();await tick();h.repositoryData={sources:[],packages:[indexed],updates:[],operations:[],warnings:[]};
  await h.get('rr-repository-search').onclick();
  h.repositoryError='Catalog digest changed; search and select the package again.';
  await selectRepo(h);
  assert.match(h.get('rr-package-error').textContent,/search and select/);
  assert.equal(h.get('rr-consent'),undefined);assert.equal(h.get('rr-install').disabled,true);
  assert.equal(h.get('rr-close-top').disabled,false);
});


test('import transport failure identifies real API URL and operation without retry or history deletion',async()=>{
  const h=harness();await tick();h.transportError='Failed to fetch';
  h.get('rr-import').files=[Buffer.from('package')];await h.get('rr-import').onchange();
  const error=h.get('rr-plan').textContent;
  assert.match(error,/Import and review package: POST http:\/\/127\.0\.0\.1:8188\/api\/renderrepo\/preview/);
  assert.match(error,/no HTTP response/);assert.match(error,/startup log/);assert.match(error,/history before retrying/);
  assert.equal(h.requests.filter(r=>r.url.endsWith('/preview')).length,1);
  assert.equal(h.requests.some(r=>r.url.includes('/discard/')||r.url.includes('/install/')),false);
});
test('HTTP rejection is distinct from a disconnected backend and reports route guidance',async()=>{
  const h=harness();await tick();h.get('rr-import').files=[Buffer.from('package')];
  for(const status of [400,403,404,500]){
    h.httpFailure={status,error:'Reported backend reason'};await h.get('rr-import').onchange();
    const text=h.get('rr-plan').textContent;
    assert.match(text,new RegExp(`HTTP ${status}: Reported backend reason`));
    assert.match(text,/api\/renderrepo\/preview/);assert.doesNotMatch(text,/no HTTP response/);
    if(status===404)assert.match(text,/route is missing/);
    if(status===500)assert.match(text,/backend traceback/);
  }
});
test('delayed initial history response cannot replace a new in-flight import selection',async()=>{
  let configReady,previewReady;
  const gates={'/renderrepo/config':new Promise(resolve=>configReady=resolve),'/renderrepo/preview':new Promise(resolve=>previewReady=resolve)};
  const h=harness([],null,[{session:'old-job',state:'done',outcome:'completed'}],undefined,gates);await tick();
  h.get('rr-import').files=[Buffer.from('new-package')];const pending=h.get('rr-import').onchange();
  configReady();await tick();
  assert.equal(h.requests.some(r=>r.url.endsWith('/status/old-job')),false);
  previewReady();await pending;await tick();
  assert.match(h.get('rr-plan').textContent,/WORKFLOW: demo.json/);
  assert.equal(h.get('rr-install').disabled,false);
});


test('a disconnected JSON response body retains operation, URL and received status',async()=>{
  const h=harness();await tick();h.bodyError='Failed to fetch';
  h.get('rr-import').files=[Buffer.from('package')];await h.get('rr-import').onchange();
  assert.match(h.get('rr-plan').textContent,/Import and review package: POST http:\/\/127\.0\.0\.1:8188\/api\/renderrepo\/preview/);
  assert.match(h.get('rr-plan').textContent,/HTTP 200, but its response body could not be read/);
  assert.equal(h.requests.filter(r=>r.url.endsWith('/preview')).length,1);
});

test('Explore has compact results and metadata categories; query survives navigation',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[{...indexed,discovery:{title:'Demo task',tags:['crop'],actions:['resize-crop']}}],installed:[],updates:[],operations:[],warnings:[]}});await tick();
 assert.equal(h.get('rr-view-browse').hidden,false);const row=all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row');assert.ok(row);
 assert.equal(all(row).filter(e=>['DETAILS','SELECT'].includes(e.tagName)).length,0);
 assert.equal(all(h.get('rr-categories')).find(e=>e.textContent==='Resize & Crop').tagName,'BUTTON');
 h.get('rr-repository-query').value='clothing';h.get('rr-tab-installed').click();h.get('rr-tab-browse').click();assert.equal(h.get('rr-repository-query').value,'clothing');
});

test('ordinary page Install is approval of displayed scope without a second screen',async()=>{
 const h=harness([],null,[],undefined,{reviewResult:{review_id:'exact',session:'token',name:'tiny',version:'1',manifest:{artifacts:[]},plan:{log:'ok',scope:{workflow:{}}},store_scope:{summary:'Add one workflow. No downloads.',downloads:0,decisions:[]},can_install:true}});await tick();
 h.get('rr-import').files=[Buffer.from('json')];await h.get('rr-import').onchange();assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
 assert.ok(all(h.get('rr-review-summary')).some(e=>e.textContent==='Add one workflow. No downloads.'));
 await h.get('rr-install').click();await tick();assert.deepEqual(JSON.parse(h.requests.find(r=>r.url.includes('/install/')).options.body),{approved:true,review_id:'exact'});
 assert.equal(h.get('rr-open').hidden,false);
});

test('installed workflows without a catalog open from their page without inspection',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[],installed:[{name:'ordinary',version:'1',workflow_available:true,workflow_modified:true}],updates:[],operations:[],warnings:[]}});await tick();
 await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();
 assert.match(h.get('rr-page-state').textContent,/edits are kept/);assert.equal(h.get('rr-direct-open').hidden,false);assert.equal(h.requests.some(r=>r.url.includes('/retrieve')),false);
 await h.get('rr-direct-open').click();assert.ok(h.requests.some(r=>r.url.includes('/installed-workflow?name=ordinary')));
});

test('active archive status stays visible across navigation and late review does not move views',async()=>{
  const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[indexed],installed:[],updates:[],operations:[],warnings:[]}});await tick();
  h.repositoryOperation={token:'repo-op',kind:'archive',state:'running',sources:[],progress:'Retrieving'};
  await selectRepo(h);
  h.get('rr-tab-installed').onclick();assert.match(h.get('rr-install-status').textContent,/Retrieving/);
  h.repositoryOperation={kind:'archive',state:'done',sources:[],result:{session:'review',name:'demo',version:'1.0',archive_sha256:'hash',plan:{log:'Reviewed'},can_install:true}};
  await h.poll();assert.equal(h.get('rr-view-installed').hidden,false);
  assert.doesNotMatch(h.get('rr-install-status').textContent,/Retrieving|approve|verified/i);
  assert.equal(h.get('rr-consent'),undefined);
});
test('refresh activity shows actual counts and readable time with exact details',async()=>{
  const now=new Date().toISOString();
  const operations=[{kind:'refresh',state:'done',started_at:now,finished_at:now,progress:'Refresh finished. Failed sources retain stale cached results.',sources:[{state:'success'},{state:'success'}]},
    {kind:'refresh',state:'done',started_at:now,finished_at:now,sources:[{state:'success'},{state:'failed'}]}];
  const h=harness([],null,[{session:'past',name:'demo',state:'done',outcome:'completed',last_activity:now}],undefined,{repositoryData:{sources:[],packages:[],updates:[],operations,warnings:[]}});await tick();
  const rows=h.get('rr-repository-activity').children;
  assert.ok(all(rows[0]).some(e=>e.textContent==='2 succeeded, 0 failed.'));
  assert.ok(!all(rows[0]).some(e=>e.textContent?.includes('stale cached results')));
  assert.ok(all(rows[1]).some(e=>e.textContent==='1 succeeded, 1 failed.'));
  assert.ok(all(rows[0]).some(e=>e.textContent==='Just now'));
  assert.ok(all(rows[0]).some(e=>e.textContent?.includes('Finished: '+now)));
  const history=h.get('rr-history').children[0];
  assert.match(history.children[0].textContent,/Last activity: Just now/);
  assert.ok(all(history).some(e=>e.textContent==='Exact last activity: '+now));
});
test('source add form defaults collapsed with sources but respects user expansion',async()=>{
  const data={sources:[{name:'personal',uri:'/packages',enabled:true}],packages:[],updates:[],operations:[],warnings:[]};
  const h=harness([],null,[],undefined,{repositoryData:data});await tick();assert.equal(h.get('rr-source-add-form').open,false);
  h.get('rr-source-add-form').open=true;await h.get('rr-repository-search').onclick();assert.equal(h.get('rr-source-add-form').open,true);
  const empty=harness();await tick();assert.equal(empty.get('rr-source-add-form').open,true);
});

test('direct Open refreshes bundled input choices before loading the graph',async()=>{
 const workflow={nodes:[{type:'LoadImage',widgets_values:['rr-core-demo-example.png']}]};
 const h=harness([],null,[],undefined,{workflow,repositoryData:{sources:[],packages:[],installed:[{name:'tiny',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();
 await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();await h.get('rr-direct-open').click();assert.deepEqual(h.editorCalls,['refresh','load']);assert.equal(h.loaded,workflow);
});

test('failed direct Open preserves the existing graph and exposes recovery',async()=>{
 const h=harness([],null,[],undefined,{refreshError:'offline',repositoryData:{sources:[],packages:[],installed:[{name:'tiny',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();
 await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();await h.get('rr-direct-open').click();assert.equal(h.loaded,undefined);assert.match(h.get('rr-plan').textContent,/offline/);assert.ok(h.get('rr-operation-error'));
});

test('new local import hides the previous review and invalidates its approval until inspection finishes',async()=>{
  let release;const gate=new Promise(resolve=>release=resolve);
  const h=harness([],null,[],undefined,{'/renderrepo/preview':gate});await tick();
  h.get('rr-review-phase').hidden=false;
  h.get('rr-import').files=[Buffer.from('new archive')];const pending=h.get('rr-import').onchange();await tick();
  assert.equal(h.get('rr-review-phase').hidden,true);assert.equal(h.get('rr-install').disabled,true);assert.equal(h.get('rr-consent'),undefined);
  release();await pending;
  assert.equal(h.get('rr-review-phase').hidden,false);
});

test('a package page exposes other source versions without mutating on selection',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[indexed,{...indexed,version:'2.0'}],installed:[],updates:[],operations:[],warnings:[]}});await tick();await selectRepo(h);
 const choices=all(h.get('rr-page-about')).find(e=>e.tagName==='SELECT');assert.ok(choices);assert.equal(choices.children.length,2);choices.value='0';await choices.onchange();await tick();assert.equal(JSON.parse(h.requests.filter(r=>r.url.endsWith('/repositories/retrieve')).at(-1).options.body).version,'1.0');assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
});

test('failed download identifies artifact and HTTP cause without expanding technical details',async()=>{
  const h=harness([], {state:'done',result:{exit_code:1,log:'FAILED SOURCE: example.bin: http://localhost/example.bin: HTTP Error 404: Not Found'},transfer:{filename:'example.bin',bytes:65536,total_bytes:262144}},[{session:'saved',state:'done',outcome:'failed'}]);
  await tick();await all(h.get('rr-history')).find(e=>e.textContent==='View status / report').onclick();await tick();
  assert.match(h.get('rr-activity-error').textContent,/Could not download example.bin.*HTTP Error 404/);
  assert.doesNotMatch(h.get('rr-activity-error').textContent,/FAILED SOURCE:|http:\/\//);
  assert.match(h.get('rr-activity-error').textContent,/then retry/);
  assert.equal(h.get('rr-technical-details').open,undefined);
  assert.ok(all(h.get('rr-history')).some(e=>/^(Retry|Resume) installation$/.test(e.textContent)));
});

test('paused transfer and history agree without stale speed/ETA; resume restores live measurements',async()=>{
  const job={session:'pause-test',name:'Tiny',state:'running',control:{action:'pause',rate_mbps:1},transfer:{filename:'tiny.bin',bytes:65536,total_bytes:262144,state:'paused',bytes_per_second:50000,eta_seconds:4}};
  const h=harness([], {...job}, [job]); await tick(); h.get('rr-tab-activity').onclick();
  assert.match(h.get('rr-install-status').textContent,/Paused/);
  assert.match(h.get('rr-transfer-info').textContent,/65.5 KB/);
  assert.doesNotMatch(h.get('rr-transfer-info').textContent,/ETA|\/s|Speed/);
  assert.match(h.get('rr-history').children[0].children[0].textContent,/Paused/);
  assert.equal(h.get('rr-install-progress').value,25);
  job.control={action:'run',rate_mbps:1}; h.installationStatus={...job}; await h.poll();
  assert.match(h.get('rr-install').textContent,/Resuming…/);
  assert.doesNotMatch(h.get('rr-transfer-info').textContent,/ETA|\/s/);
  job.transfer={...job.transfer,state:'transferring',bytes:131072,bytes_per_second:100000,eta_seconds:2}; h.installationStatus={...job}; await h.poll();
  assert.match(h.get('rr-transfer-info').textContent,/100.0 KB\/s.*ETA 2 sec/);
  assert.doesNotMatch(h.get('rr-history').children[0].children[0].textContent,/Paused/);
});
test('a blocked plan keeps Install disabled and the cause inline',async()=>{
 const h=harness([],null,[],undefined,{reviewResult:{session:'token',name:'blocked',manifest:{artifacts:[]},review_id:'blocked',can_install:false,plan:{exit_code:2,log:'ERROR: workflow already exists',scope:null}}});await tick();
 h.get('rr-import').files=[Buffer.from('json')];await h.get('rr-import').onchange();assert.equal(h.get('rr-install').disabled,true);assert.match(h.get('rr-review-recovery').textContent,/already exists/);assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
});

test('failure explanation appears once beside primary retry while verbose logs stay in report',async()=>{
  const failure='FAILED SOURCE: tiny.bin: http://localhost/file: HTTP Error 404: Not Found';
  const h=harness([], {state:'done',outcome:'failed',result:{exit_code:1,log:failure+'\nverbose retry diagnostics'}}, [{session:'saved',name:'Tiny',state:'done',outcome:'failed',failure}]); await tick();
  await all(h.get('rr-history')).find(e=>e.textContent==='View status / report').onclick(); await tick();
  assert.match(h.get('rr-activity-error').textContent,/tiny.bin.*404/);
  assert.doesNotMatch(h.get('rr-install-status').textContent,/404|tiny.bin/);
  assert.ok(all(h.get('rr-history')).find(e=>e.textContent==='Retry installation').className.includes('rr-primary'));
  assert.match(h.get('rr-plan').textContent,/verbose retry diagnostics/);
});

test('page presents backend-derived download and reuse counts without empty metadata',async()=>{
 const h=harness([],null,[],undefined,{reviewResult:{session:'token',name:'tiny',review_id:'r',manifest:{artifacts:[]},can_install:true,plan:{log:'ok',scope:{}},store_scope:{summary:'Add one workflow. Reuse 3 existing files.',downloads:2,download_bytes:6300000000,decisions:[]}}});await tick();h.get('rr-import').files=[Buffer.from('pkg')];await h.get('rr-import').onchange();
 const text=all(h.get('rr-review-summary')).map(e=>e.textContent||'').join(' ');assert.match(text,/Reuse 3/);assert.match(text,/6.30 GB/);assert.equal(text.includes('not specified'),false);
});

test('a status response requested before Pause cannot overwrite the newer pending label',async()=>{
  let release;const delayed=new Promise(resolve=>release=resolve);
  const h=harness([], {state:'running',control:{action:'run',rate_mbps:1},transfer:{filename:'tiny',bytes:1,total_bytes:4,state:'transferring'}},[],undefined,{'/renderrepo/status/token':delayed});
  await tick();h.get('rr-import').files=[Buffer.from('package')];await h.get('rr-import').onchange();
  await h.get('rr-install').onclick();await tick();await h.get('rr-pause').onclick();
  release();await tick();h.get('rr-tab-activity').onclick();
  assert.match(h.get('rr-install-status').textContent,/Pausing…/);
  assert.equal(h.get('rr-pause').textContent,'Resume downloads');
  h.installationStatus={state:'running',control:{action:'pause',rate_mbps:1},transfer:{filename:'tiny',bytes:1,total_bytes:4,state:'paused'}};
  await h.poll();assert.match(h.get('rr-install-status').textContent,/Paused/);
});


test('readiness stays under installed workflow Manage and is identity-bound',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[],installed:[{name:'tiny',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();
 assert.equal(all(h.get('rr-installed-results')).some(e=>e.textContent==='Check local readiness'),false);await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();
 await all(h.get('rr-page-about')).find(e=>e.textContent==='Check local readiness').click();assert.deepEqual(JSON.parse(h.requests.find(r=>r.url.endsWith('/readiness')).options.body),{installed:'tiny'});
});

test('source edits invalidate the prepared authorization immediately',async()=>{
 const h=harness();await tick();h.get('rr-import').files=[Buffer.from('pkg')];await h.get('rr-import').onchange();h.get('rr-sources').value='a=http://localhost/new';h.get('rr-sources').listeners.input();assert.equal(h.get('rr-install').disabled,true);assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
});

test('browsing an installed starter never starts local execution',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[],installed:[{name:'rr-starter-resize',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();
 assert.ok(all(h.get('rr-page-about')).some(e=>e.textContent==='Test locally'));assert.equal(h.requests.some(r=>r.url.endsWith('/local-tests/plan')||r.url.endsWith('/start')||r.url.includes('/prompt')),false);
});

test('storefront has two normal views, Manage retains admin, file accepts editor JSON',async()=>{
 const h=harness();await tick();
 assert.equal(h.get('rr-tab-browse').textContent,'Explore');assert.equal(h.get('rr-tab-installed').textContent,'My Workflows');
 assert.equal(h.get('rr-tab-sources').parent.id,'rr-manage');assert.equal(h.get('rr-tab-activity').parent.id,'rr-manage');
 assert.ok(h.get('rr-import').accept.includes('.json'));assert.equal(h.get('rr-import-action').textContent,'Import workflow');
});
test('interrupted operation reconnects beside normal task without visiting history',async()=>{
 const h=harness([], {state:'interrupted',result:{exit_code:2,log:'Interrupted'},manifest:{name:'demo',artifacts:[]},options:{}},[{session:'retained',state:'interrupted'}]);await tick();
 assert.equal(h.get('rr-view-activity').hidden,true);assert.equal(h.get('rr-active-operation').hidden,false);
 assert.equal(h.get('rr-install').textContent,'Resume installation');assert.equal(h.get('rr-install').disabled,false);
});
test('source choice invalidates and re-reviews exact override; one missing model gets one control',async()=>{
 const result={session:'token',name:'plain',version:'0.1',manifest:{artifacts:[{filename:'missing.bin',sources:[]},{filename:'multi.bin',sources:[{uri:'http://127.0.0.1/upstream'},{uri:'http://127.0.0.1/mirror'}]}]},target_check:{state:'available',nodes:[]},plan:{log:'NO SOURCE: missing.bin',exit_code:2,scope:null},can_install:false};
 const h=harness([],null,[],undefined,{reviewResult:result});await tick();h.get('rr-import').files=[Buffer.from('json')];await h.get('rr-import').onchange();
 assert.ok(h.get('rr-resolve-missing.bin'));assert.ok(h.get('rr-source-multi.bin'));assert.equal(h.get('rr-install').disabled,true);
 h.get('rr-source-multi.bin').value='http://127.0.0.1/mirror';await h.get('rr-source-multi.bin').onchange();
 const request=h.requests.filter(r=>r.url.includes('/review/')).at(-1);assert.ok(JSON.parse(request.options.body).sources.includes('multi.bin=http://127.0.0.1/mirror'));
});

test('installed workflow page offers direct Open even with newer catalog version',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[{...indexed,version:'2.0',installed_identity:true}],installed:[{name:'demo',source:'personal',version:'1.0',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();await all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row').click();assert.equal(h.get('rr-direct-open').hidden,false);assert.equal(h.requests.some(r=>r.url.includes('/retrieve')),false);await h.get('rr-direct-open').click();assert.ok(h.loaded);
});

test('listing stays compact; supplied previews appear only on workflow page',async()=>{
 const item={...indexed,discovery:{examples:['resize photo','http://localhost/owned.png']}};
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[item],installed:[{name:'demo',source:'personal',version:'1',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();assert.equal(all(h.get('rr-repository-results')).some(e=>e.tagName==='IMG'),false);
 // Receipt-backed page avoids preparation just for Open.
 item.installed_identity=true;await h.get('rr-repository-search').click();await all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row').click();assert.equal(all(h.get('rr-page-about')).filter(e=>e.tagName==='IMG').length,1);
});


test('code decisions need a focused confirmation bound to the prepared identity',async()=>{
 const h=harness([],null,[],undefined,{reviewResult:{session:'token',name:'code',review_id:'code-plan',manifest:{artifacts:[]},can_install:true,plan:{log:'ok',scope:{}},store_scope:{summary:'Add one workflow.',decisions:[{id:'code',message:'Install custom-node code.'}]}}});await tick();h.get('rr-import').files=[Buffer.from('fixture')];await h.get('rr-import').onchange();
 await h.get('rr-install').click();assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);assert.equal(h.get('rr-install').disabled,true);
 const checkbox=all(h.get('rr-approval-actions')).find(e=>e.type==='checkbox');checkbox.checked=true;checkbox.onchange();await h.get('rr-install').click();
 assert.deepEqual(JSON.parse(h.requests.find(r=>r.url.includes('/install/')).options.body),{approved:true,review_id:'code-plan',confirmations:['code']});
});
test('editing Python scope invalidates an open code confirmation before dispatch',async()=>{
 const h=harness([],null,[],undefined,{reviewResult:{session:'token',name:'code',review_id:'code-plan',manifest:{artifacts:[]},can_install:true,plan:{log:'ok',scope:{}},store_scope:{summary:'Add one workflow.',decisions:[{id:'code',message:'Install code.'}]}}});await tick();h.get('rr-import').files=[Buffer.from('fixture')];await h.get('rr-import').onchange();await h.get('rr-install').click();
 h.get('rr-python').checked=true;h.get('rr-python').listeners.input();await h.get('rr-install').click();assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);assert.equal(h.get('rr-install').disabled,true);
});


test('Test locally is review-only until one explicit approval action; no automatic execution',async()=>{
  const plan={id:'test-token',context:{server:'http://127.0.0.1:1',package:{name:'rr-starter-resize',version:'0.1.0'},inputs:[{filename:'example.png',dimensions:[320,200],batch:1}],runtime:{system:{comfyui_version:'test'},devices:[{name:'CPU',type:'cpu'}]}},runs:2,deadline_seconds:60,max_elapsed_before_deadline_reporting_seconds:150,expected_output_directory:'/private/output',recomputation:'One pixel change',warning:'Consumes CPU and produces files'};
  const gates={repositoryData:{sources:[],packages:[],updates:[],operations:[],warnings:[],installed:[{name:'rr-starter-resize',version:'0.1.0',status:'installed',workflow_available:true}]},localTestPlan:{plan,approval_sha256:'reviewed-hash'},localTestStatus:{id:'test-token',plan,state:'completed',runs:[]}};
  const h=harness([],null,[],undefined,gates);await tick();
  assert.equal(h.requests.some(r=>r.url.includes('/local-tests/plan')||r.url.endsWith('/start')),false);
  await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();
  const area=all(h.get('rr-page-about')).find(e=>e.className==='rr-local-test');
  await all(area).find(e=>e.textContent==='Test locally').onclick();
  assert.equal(h.requests.some(r=>r.url.endsWith('/start')),false);
  assert.ok(all(area).some(e=>e.textContent?.includes('Server: http://127.0.0.1:1')));
  await all(area).find(e=>e.textContent==='Start reviewed test').onclick();
  const starts=h.requests.filter(r=>r.url.endsWith('/start'));assert.equal(starts.length,1);
  assert.equal(JSON.parse(starts[0].options.body).approval_sha256,'reviewed-hash');
  assert.equal(h.requests.some(r=>r.url.endsWith('/prompt')),false);
});

test('Back preserves the visible category selection and task query',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[{...indexed,discovery:{tags:['crop'],actions:['resize-crop']}}],installed:[],updates:[],operations:[],warnings:[]}});await tick();
 h.get('rr-repository-query').value='clothing';await all(h.get('rr-categories')).find(e=>e.textContent==='Resize & Crop').click();await selectRepo(h);h.get('rr-back').click();
 assert.equal(h.get('rr-repository-query').value,'clothing');assert.equal(all(h.get('rr-categories')).find(e=>e.textContent==='Resize & Crop')['aria-pressed'],'true');
});

test('partial Installed entry reconnects to its retained operation instead of retrieving a new recipe',async()=>{
 const h=harness([], {state:'done',outcome:'failed',result:{exit_code:2,log:'ERROR: fixture stopped'},manifest:{name:'partial',artifacts:[]},plan:{exit_code:0,log:'prepared',scope:{}},options:{}},
 [{session:'saved',name:'partial',state:'done',outcome:'failed'}],undefined,{repositoryData:{sources:[],packages:[],installed:[{name:'partial',status:'partial',workflow_available:false}],updates:[],operations:[],warnings:[]}});await tick();
 await all(h.get('rr-installed-results')).find(e=>e.className==='rr-store-row').click();await tick();assert.ok(h.requests.some(r=>r.url.endsWith('/status/saved')));assert.equal(h.requests.some(r=>r.url.endsWith('/repositories/retrieve')),false);assert.ok(h.get('rr-inline-recovery'));
});

test('one newer catalog version remains inspectable while the installed version opens directly',async()=>{
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[{...indexed,version:'2.0',installed_identity:true}],installed:[{name:'demo',source:'personal',version:'1.0',workflow_available:true}],updates:[],operations:[],warnings:[]}});await tick();await all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row').click();assert.equal(h.get('rr-direct-open').hidden,false);
 await all(h.get('rr-page-about')).find(e=>e.textContent==='Inspect selected version').click();await tick();assert.equal(JSON.parse(h.requests.filter(r=>r.url.endsWith('/repositories/retrieve')).at(-1).options.body).version,'2.0');assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
});

test('a new store page cannot authorize installation against an older backend without scoped decisions',async()=>{
 const h=harness([],null,[],undefined,{legacyBackend:true});await tick();h.get('rr-import').files=[Buffer.from('fixture')];await h.get('rr-import').onchange();assert.equal(h.get('rr-install').disabled,true);assert.match(h.get('rr-page-state').textContent,/older/);await h.get('rr-install').click();assert.equal(h.requests.some(r=>r.url.includes('/install/')),false);
});

test('action navigation is stable, supports multiple actions, and never promotes raw tags',async()=>{
 const cap={purpose:'Crop image',actions:['edit-image','resize-crop'],inputs:[{label:'Image input',kind:'image',requirement:'required'}],prompts:[],controls:[],processing:[{label:'Crop image',exposed:true}],outputs:['image'],uncertainties:[],evidence:[],workflow_sha256:'a'.repeat(64)};
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[{...indexed,capabilities:cap,discovery:{tags:['aspect','smaller']},target_status:'Required nodes available'}],installed:[],updates:[],operations:[],warnings:[]}});await tick();
 const labels=all(h.get('rr-categories')).filter(e=>e.tagName==='BUTTON').map(e=>e.textContent);assert.deepEqual(labels,['Edit Image','Resize & Crop']);
 const row=all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row');assert.ok(all(row).some(e=>e.textContent==='1 image input'));assert.ok(all(row).some(e=>e.textContent?.includes('performance untested')));
 assert.equal(h.requests.some(r=>r.url.includes('/retrieve')),false);
 h.get('rr-repository-query').value='face swap';h.get('rr-repository-query').oninput();assert.ok(all(h.get('rr-repository-results')).some(e=>e.textContent?.includes('No graph-verified face-swap')));
});
test('preparation selection made during another archive waits then prepares the newest choice',async()=>{
 const other={...indexed,name:'other',discovery:{title:'Other workflow'}};
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[indexed,other],installed:[],updates:[],operations:[],warnings:[]}});await tick();
 h.repositoryOperation={token:'repo-op',kind:'archive',state:'running',sources:[],progress:'Retrieving'};await selectRepo(h);h.get('rr-back').click();
 await all(h.get('rr-repository-results')).find(e=>e['data-package']==='other').click();assert.equal(h.get('rr-page-title').textContent,'Other workflow');
 assert.equal(h.requests.filter(r=>r.url.endsWith('/repositories/retrieve')).length,1);
 h.repositoryOperation={token:'repo-op',kind:'archive',state:'done',sources:[]};await h.poll();
 const requests=h.requests.filter(r=>r.url.endsWith('/repositories/retrieve'));assert.equal(requests.length,2);assert.equal(JSON.parse(requests[1].options.body).name,'other');assert.equal(h.get('rr-page-title').textContent,'Other workflow');
});


test('installed older version uses actual installed analysis rather than newer catalog claims',async()=>{
 const facts={purpose:'Actual installed graph',actions:[],inputs:[],prompts:[],controls:[],processing:[],outputs:['image'],uncertainties:[],evidence:[],workflow_sha256:'a'.repeat(64)};
 const item={...indexed,version:'2',installed_identity:true,capabilities:{...facts,purpose:'Different NEW graph'}};
 const h=harness([],null,[],undefined,{repositoryData:{sources:[],packages:[item],installed:[{name:'demo',version:'1',workflow_available:true}],updates:[],operations:[],warnings:[]},readinessReport:{state:'ready',capabilities:facts}});await tick();
 await all(h.get('rr-repository-results')).find(e=>e.className==='rr-store-row').click();await tick();
 assert.ok(all(h.get('rr-capabilities')).some(e=>e.textContent==='Actual installed graph'));assert.equal(all(h.get('rr-capabilities')).some(e=>e.textContent==='Different NEW graph'),false);assert.equal(h.get('rr-direct-open').hidden,false);
});
