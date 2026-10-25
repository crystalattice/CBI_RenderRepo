'use strict';
let initialReady=false;
const ACTIONS=[['explore','Explore','explore'],['create','Create Image','create'],['edit','Edit Image','instructions'],['resize','Resize & Crop','resize'],['combine','Combine Images','combine'],['video','Create Video','video'],['installed','My Workflows','workflows'],['manage','Manage','manage']];
const $=id=>document.getElementById(id), main=$('main'), nav=$('navigation');
function node(tag,text,parent,attrs={}){const e=document.createElement(tag);if(text!==null&&text!==undefined)e.textContent=text;for(const [k,v] of Object.entries(attrs)){if(k==='class')e.className=v;else e.setAttribute(k,v);}parent?.append(e);return e;}
function iconNode(key,parent,className){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 48 48');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');svg.setAttribute('class',className);const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#icon-'+key);svg.append(use);parent.append(svg);return svg;}
function button(text,parent,fn,attrs={}){const b=node('button',text,parent,attrs);b.type='button';b.onclick=fn;return b;}
function toast(text){clearTimeout(toastTimer);$('toast').textContent=text;$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
function setMenu(open){$('store').classList.toggle('menu-open',open);$('menu').setAttribute('aria-expanded',String(open));$('scrim').hidden=!open;if(open)nav.querySelector('[aria-current=page]')?.focus();else $('menu').focus();}
function clearToast(){clearTimeout(toastTimer);$('toast').hidden=true;}
function navigate(dest){clearToast();if(state.route==='list')state.scroll=main.scrollTop;state.destination=dest;state.route='list';state.detail=null;state.scroll=0;decision=null;setMenu(false);persist();render();main.scrollTop=0;$('page-title')?.focus();}
function navRender(){nav.replaceChildren();button('Close menu',nav,()=>setMenu(false),{class:'drawer-close'});for(const [id,label,icon] of ACTIONS){const b=button('',nav,()=>navigate(id),{'data-nav':id,class:id==='manage'?'manage':''});if(state.destination===id)b.setAttribute('aria-current','page');iconNode(icon,b,'nav-icon');node('span',label,b);}nav.onkeydown=e=>{const visible=[...nav.querySelectorAll('button')].filter(b=>b.getClientRects().length);if(e.key==='Tab'&&$('store').classList.contains('menu-open')){const first=visible[0],last=visible[visible.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){const buttons=visible,i=buttons.indexOf(document.activeElement);let n=e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;e.preventDefault();buttons[n].focus();}};}
function title(label,subtitle,count){const head=node('div',null,main,{class:'page-head'}),copy=node('div',null,head);node('h1',label,copy,{id:'page-title',tabindex:'-1'});if(subtitle)node('p',subtitle,copy,{class:'muted'});if(count!==undefined)node('span',count+' workflow'+(count===1?'':'s'),head,{class:'count'});return head;}
function openDetail(w){clearToast();if(state.route==='list')state.scroll=main.scrollTop;state.route='detail';state.detail=w.id;decision=null;persist();render();main.scrollTop=0;$('workflow-name')?.focus();prepare(w);}
function back(){state.route='list';state.detail=null;decision=null;persist();render();requestAnimationFrame(()=>{main.scrollTop=state.scroll;[...main.querySelectorAll('[data-workflow]')].find(e=>e.dataset.workflow===state.lastViewed)?.focus({preventScroll:true});});}
function clearFilters(){state.query='';state.destination='explore';state.scroll=0;$('search').value='';persist();render();main.scrollTop=0;}
function matches(w,q){const text=[w.name,w.purpose,w.prompts,w.jsonSubgraph,w.jsonSource,...(w.jsonNodeTypes||[]),...w.tags,...w.provide,...w.controls].join(' ').toLowerCase();return q.toLowerCase().split(/\s+/).filter(Boolean).every(t=>text.includes(t));}
function renderCard(w,parent,related=false){const li=node('li',null,parent),card=button('',li,()=>{state.lastViewed=w.id;openDetail(w);},{class:'workflow-card','data-workflow':w.id,'aria-label':'View '+w.name});const h=node('span',null,card,{class:'card-heading'});iconNode(w.icon||'unknown',h,'thumb');const n=node('span',null,h);node('span',w.name,n,{class:'card-name'});if(isInstalled(w))node('span','Installed',n,{class:'installed-badge'});node('span',w.purpose,card,{class:'card-purpose'});const inputs=node('span',null,card,{class:'input-facts'});const imageLine=node('span',null,inputs,{class:'input-line'});node('span','🖼️',imageLine,{class:'input-emoji','aria-hidden':'true'});node('span',imageSummary(w),imageLine);const promptLine=node('span',null,inputs,{class:'input-line'});if(w.prompts!=='No prompt'){node('span',w.id==='unfamiliar'?'📝':'✍️',promptLine,{class:'input-emoji','aria-hidden':'true'});}node('span',w.prompts,promptLine);const tags=node('span',null,card,{class:'tags'});for(const label of w.tags.slice(0,3))node('span',label,tags,{class:'tag'});node('span',related?'Related editor · not a dedicated face-swap workflow':finding(w,true),card,{class:'environment-status'+(blocked(w)?' blocked':unresolved(w)?' uncertain':'')});if(related)node('span',finding(w,true),card,{class:'muted'});}
function facts(label,values,parent){const block=node('section',null,parent);node('h2',label,block);const ul=node('ul',null,block);values.forEach(s=>node('li',s,ul));}
function renderWorkflowPrompts(w,parent){
 const section=node('section',null,parent,{'aria-label':'Current workflow prompt'});
 node('h2','Current workflow prompt',section);
 const prompts=RR.promptValues(w.capabilities);
 if(!prompts.length)node('p','No prompt value is embedded.',section);
 for(const prompt of prompts){
  if(prompts.length>1)node('p',prompt.label,section);
  node('p',prompt.value,section,{class:'workflow-prompt-value',style:'white-space:pre-wrap;overflow-wrap:anywhere'});
 }
}
function renderDetail(w){button('← Back',main,back,{class:'back',id:'back'});const h=node('div',null,main,{class:'workflow-title'});node('p',ACTIONS.find(a=>a[0]===state.destination)?.[1],h,{class:'eyebrow'});node('h1',w.name,h,{id:'workflow-name',tabindex:'-1'});node('p',w.purpose,h);const grid=node('div',null,main,{class:'detail-grid'}),body=node('div',null,grid,{class:'detail-body'});node('aside',null,grid,{id:'install-panel',class:'install-panel','aria-label':'Installation and environment'});const preview=node('div',null,body,{class:'preview','aria-label':'Preview unavailable'});let labels=w.images.required===null?['Unidentified inputs','Output unknown']:w.images.required===0?['Text prompt',w.output]:w.images.required===2?['Image 1','Image 2','Output preview']:w.optionalImageLabel?['Starting image','Optional last frame','Video output']:['Input example','Output preview'];for(const label of labels){const box=node('div',null,preview,{class:'preview-box'});iconNode(w.icon||'unknown',box,'preview-icon');node('span',label,box);}node('span','Preview not supplied · workflow results unknown',preview,{class:'preview-label'});const f=node('div',null,body,{class:'facts'});facts('You provide',w.provide,f);facts('You can adjust',w.controls,f);renderWorkflowPrompts(w,body);const out=node('div',null,body,{class:'produces'});node('h2','Produces',out);node('p',w.output,out);if(w.automatic){const a=node('div',null,body,{class:'automatic'});node('p',w.automatic,a);}renderTechnical(w,body);renderOriginalCapsule(w,main);renderPanel(w);}
function actionButton(label,w,fn,disabled=false){const b=button(label,$('install-panel'),fn,{class:'primary',id:'primary-action','data-primary':'','aria-label':label});b.disabled=disabled;return b;}
function check(label,parent,id,value,fn){const l=node('label',null,parent,{class:'check'}),c=node('input',null,l,{type:'checkbox',id});c.checked=value;c.onchange=()=>fn(c.checked);node('span',label,l);return c;}
function showDialog(title,build){const d=$('dialog');const restore=document.activeElement;d.replaceChildren();node('h2',title,d,{id:'dialog-title'});build(d,()=>d.close());d.onclose=()=>restore?.focus({preventScroll:true});d.showModal();}
function manageCard(titleText,parent){const c=node('section',null,parent,{class:'manage-card'});node('h2',titleText,c);return c;}

let state={destination:'explore',query:'',route:'list',detail:null,scroll:0,manage:'sources',lastViewed:null};
let rows=[], configuration=null, repositories=null, history=[], decision=null, toastTimer, importType='json';
const reviews=new Map(), operations=new Map(), preparation=new Map(), errors=new Map(), pending=new Set();
const catalog=()=>rows;
const workflow=id=>rows.find(w=>w.id===id);
const identity=w=>w.id;
const targetName=()=>configuration?.environment?.name || 'This Comfy environment';
const installedRecord=w=>(repositories?.installed || []).find(i=>i.name===w.packageName && i.version===w.version && (i.source ? i.source===w.raw.repository || i.source===w.raw.source : !w.raw.repository));
const isInstalled=w=>!!installedRecord(w)?.workflow_available && (w.raw.workflow_available===true ||
 (w.raw.installed_identity===true && w.raw.installed_version===w.version) ||
 (operations.get(w.id)?.workflow_available===true && RR.operationLabel(operations.get(w.id))==='Complete'));
const currentJob=w=>operations.get(w.id);
const blocked=()=>false;
const unresolved=()=>false;
const finding=w=>reviews.get(w.id)?.target_check ? 'Static requirements: '+reviews.get(w.id).target_check.state+'. Execution and performance unknown.' : (w.readiness?'Static requirements: '+w.readiness.state:w.targetStatus)+'. Execution and performance unknown.';
function persist(){try{localStorage.setItem('renderrepo-storefront-preferences',JSON.stringify({destination:state.destination,manage:state.manage}));}catch{}}
function humanBytes(value){if(value==null)return 'Unknown';return value>=1e9?(value/1e9).toFixed(2)+' GB':value>=1e6?(value/1e6).toFixed(1)+' MB':value.toLocaleString()+' bytes';}
function imageSummary(w){return w.images.required===null?'Image requirements unknown':w.images.required+' required image'+(w.images.required===1?'':'s');}
async function act(fn){try{return await fn();}catch(error){toast(error.message+' No automatic install retry was made.');}}
function repaint(w){if(state.route==='detail' && state.detail===w.id)renderPanel(w);renderStrip();}
function applyReview(w,result){
 const r=RR.validateReview(result);reviews.set(w.id,r);operations.delete(w.id);errors.delete(w.id);decision=null;
 const updated=RR.summary({...w.raw,name:r.name,version:r.version,capabilities:r.capabilities||w.capabilities});
 Object.assign(w,updated,{id:w.id,raw:w.raw});
 repaint(w);
}
async function prepare(w){
 if(isInstalled(w)){
  if(!w.readiness){w.readiness={state:'checking'};try{const report=await RR.json('/readiness',{installed:w.packageName});w.readiness=report;if(report.capabilities){const updated=RR.summary({...w.raw,capabilities:report.capabilities});Object.assign(w,updated,{id:w.id,raw:w.raw});}if(state.detail===w.id)render();}catch(error){w.readiness={state:'unknown',error:error.message};toast(error.message);}}return;
 }
 if(operations.has(w.id)||reviews.has(w.id)||preparation.has(w.id)||errors.has(w.id))return;
 const task=(async()=>{
  try{
   if(!w.raw.repository)throw new Error('No retained review is available; import this workflow again.');
   const readiness=await RR.json('/readiness',RR.selection(w));w.readiness=readiness;
   const op=await RR.json('/repositories/retrieve',RR.selection(w));
   const r=await RR.repositoryOperation(op,message=>{w.preparingMessage=message;repaint(w);});
   applyReview(w,r);
  }catch(error){errors.set(w.id,error.message);}finally{preparation.delete(w.id);if(state.detail===w.id)render();}
 })();preparation.set(w.id,task);await task;
}
function renderCatalog(){
 const candidates=rows.filter(w=>state.destination==='explore'||w.actions.includes(state.destination));
 const matchesQuery=candidates.filter(w=>matches(w,state.query));
 const head=title(ACTIONS.find(a=>a[0]===state.destination)?.[1]||'Explore','Find a workflow for what you want to do.',matchesQuery.length);
 button('Import workflow',head,()=>{state.destination='manage';state.manage='import';render();},{class:'import-entry','data-import-entry':''});
 if(!matchesQuery.length){node('p',repositories?'No workflows match. Add or refresh a source in Manage, or import a workflow.':'Loading RenderRepo catalog…',main,{class:'empty'});return;}
 const list=node('ul',null,main,{class:'catalog'});matchesQuery.forEach(w=>renderCard(w,list));
}
function renderTechnical(w,parent){
 const details=node('details',null,parent,{class:'technical',id:'workflow-details'});node('summary','Details',details);
 const body=node('div',null,details,{class:'technical-body'}),r=reviews.get(w.id),target=r?.target_check||currentJob(w)?.target_check;
 const nodeEvidence=RR.nodeEvidence(w,target,w.readiness);
 node('h3','Package and source',body);node('p',w.packageName+' · '+(w.version||'Unknown version')+' · '+w.source,body);
 node('h3','Nodes in workflow JSON',body);
 if(nodeEvidence.length){const ul=node('ul',null,body,{class:'json-node-list'});for(const n of nodeEvidence){const li=node('li',null,ul);node('span',n.type,li,{class:'json-node-name'});node('span',({available:'Present on target',resolvable:'Installable on target',needs_source:'Source required',unavailable:'Blocked on target',unknown:'Target status unknown'})[n.state]||'Target status unknown',li,{class:'json-node-status'});}}
 else node('p','Executable node inventory unavailable; reimport or review this workflow.',body);
 node('h3','Full controls',body);node('p',w.controls.join(' · ')||'Unknown',body);
 node('h3','Target observations',body);
 const observed=w.readiness||target?.evidence;
 if(observed)node('p','Observed '+(observed.checked_at||'time unknown')+' on '+(observed.server||targetName())+'. Node registration and file checks do not establish execution or hardware suitability.',body);
 else node('p','Target observations unavailable.',body);
 for(const installation of w.readiness?.installation_observations||target?.installation_observations||[]){
  node('p','Prior RR installation: '+installation.state+' · '+installation.download_count+' downloads.',body);
  const ul=node('ul',null,body);for(const artifact of installation.artifacts||[])node('li',artifact.filename+' · '+(artifact.operation==='reuse'?'reused':'acquired')+' · '+(artifact.hash_matched?'hash matched package fingerprint':'hash match not recorded'),ul);
  node('p','Package fingerprints are not proof of upstream identity.',body);
 }
 if(w.capabilities.models?.length&&!w.capabilities.models.some(m=>m.expected_sha256))node('p','The workflow supplies no expected model hashes. Local package fingerprint checks do not verify upstream identity.',body);
 node('h3','Analysis evidence and uncertainties',body);node('p',w.capabilities.provenance||'Unknown',body);
 for(const path of w.capabilities.conditioning||[])node('p',path.label,body);
 for(const p of w.capabilities.prompts||[])if(p.role_evidence)node('p','Prompt role: '+p.role_evidence+'.',body);
 for(const u of w.capabilities.uncertainties||[])node('p',u,body,{class:'uncertainty'});
 node('p','Execution: untested. Hardware suitability and performance: unknown.',body);
 const advanced=node('details',null,body,{id:'advanced'});node('summary','Advanced',advanced);
 if(r){
  const locked=pending.has(w.id)||!!currentJob(w)?.worker_active||currentJob(w)?.state==='running';
  for(const artifact of r.manifest.artifacts||[]){
   const options=artifact.sources||[];
   node('h3',artifact.filename,advanced);node('p','Declared size: '+humanBytes(artifact.size_bytes),advanced);
   const label=node('label','Source for '+artifact.filename,advanced),input=node('input',null,label,{type:'text'});
   input.value=(r.options.sources||[]).find(s=>s.startsWith(artifact.filename+'='))?.slice(artifact.filename.length+1)||options[0]?.uri||'';
   const update=()=>revise(w,{...r.options,sources:[...(r.options.sources||[]).filter(s=>!s.startsWith(artifact.filename+'=')),artifact.filename+'='+input.value]});
   input.disabled=locked;button('Review source',advanced,()=>act(update)).disabled=locked;
   if(options.length>1){const select=node('select',null,advanced,{'aria-label':'Mirror for '+artifact.filename});for(const source of options)node('option',source.uri,select,{value:source.uri});select.value=input.value;select.disabled=locked;select.onchange=()=>{input.value=select.value;act(update);};}
  }
  check('Include Python requirements (opt-in)',advanced,'include-python',!!r.options.python_dependencies,value=>act(()=>revise(w,{...r.options,python_dependencies:value}))).disabled=locked;
  for(const n of r.target_check?.nodes||[]){if(n.state!=='needs_source')continue;
   const area=node('section',null,advanced);node('h3','Resolve '+n.type,area);
   const fields={};for(const [key,label] of [['repository','Package name'],['source','Repository URL'],['revision','Exact revision']]){const l=node('label',label,area);fields[key]=node('input',null,l);fields[key].disabled=locked;}
   button('Review node source',area,()=>act(async()=>{await mutateReview(w,()=>RR.json('/resolve/'+r.session,{kind:'node',name:n.type,...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value]))}));})).disabled=locked;
  }
  node('pre',JSON.stringify({plan:r.plan,target:r.target_check,imported_workflow:r.imported_workflow},null,2),advanced);
 }
}
async function mutateReview(w,fn){
 if(pending.has(w.id))return;pending.add(w.id);decision=null;repaint(w);
 try{applyReview(w,await fn());}catch(error){reviews.delete(w.id);errors.set(w.id,error.message);throw error;}
 finally{pending.delete(w.id);if(state.detail===w.id)render();}
}
async function revise(w,options){const r=reviews.get(w.id);await mutateReview(w,()=>RR.json('/review/'+r.session,options));}
function renderPanel(w){
 const panel=$('install-panel');if(!panel)return;const rateEdit=document.activeElement?.id==='download-rate'?document.activeElement.value:null;panel.replaceChildren();
 node('p',targetName(),panel,{class:'target'});node('p',finding(w),panel,{class:'finding'});if(installedRecord(w)?.workflow_modified)node('p','The installed workflow has local edits. Current graph evidence may differ from the published catalog.',panel,{class:'uncertainty'});
 const r=reviews.get(w.id),job=currentJob(w);panel.dataset.state=job?RR.operationLabel(job):'plan';
 if(pending.has(w.id)){actionButton('Preparing…',w,()=>{},true);return;}
 if(job){renderJob(w,job,panel);if(rateEdit!==null&&$('download-rate')){$('download-rate').value=rateEdit;$('download-rate').focus({preventScroll:true});}return;}
 if(isInstalled(w)){actionButton('Open workflow',w,()=>act(()=>handoff(w)));return;}
 if(errors.has(w.id)){node('p',errors.get(w.id),panel,{class:'uncertainty',role:'alert'});actionButton('Review again',w,()=>{errors.delete(w.id);prepare(w);renderPanel(w);});return;}
 if(!r){node('p',w.preparingMessage||'Checking the workflow and preparing its installation scope…',panel,{class:'status-copy'});actionButton('Preparing…',w,()=>{},true);return;}
 node('p',r.store_scope.summary,panel,{class:'scope',id:'scope-summary'});
 node('p',`${r.store_scope.downloads} file downloads · ${humanBytes(r.store_scope.download_bytes)} known · ${r.store_scope.unknown_sizes} unknown sizes`,panel);
 if(!r.can_install){node('p','Installation blocked. Inspect Details → Advanced for unresolved requirements and the backend report.',panel,{class:'uncertainty'});actionButton('Install',w,()=>{},true);return;}
 const decisions=r.store_scope.decisions||[];
 if(decision?.workflow===w.id && decision.review===r.review_id){
  for(const d of decisions)check(d.message,panel,'ack-'+d.id,decision.ids.has(d.id),v=>{v?decision.ids.add(d.id):decision.ids.delete(d.id);renderPanel(w);});
  actionButton('Confirm and install',w,()=>act(()=>startInstall(w,[...decision.ids])),decisions.some(d=>!decision.ids.has(d.id)));
  button('Back',panel,()=>{decision=null;renderPanel(w);},{id:'decision-back'});
 }else actionButton('Install',w,()=>{if(decisions.length){decision={workflow:w.id,review:r.review_id,ids:new Set()};renderPanel(w);}else act(()=>startInstall(w,[]));});
}
async function startInstall(w,confirmations){
 const r=reviews.get(w.id);if(!r||pending.has(w.id)||!r.can_install)return;
 pending.add(w.id);decision=null;repaint(w);
 try{
  const result=await RR.json('/install/'+r.session,{approved:true,review_id:r.review_id,confirmations});
  if(result.approval_required||result.confirmation_required){applyReview(w,result);toast('The plan changed. Review it and choose Install again.');}
  else {operations.set(w.id,{...result,session:r.session});watch(w,r.session);}
 }catch(error){operations.set(w.id,{session:r.session,state:'unknown',failure:'Install acknowledgement unavailable. Check backend status before retrying. '+error.message});throw error;}
 finally{pending.delete(w.id);repaint(w);}
}
async function watch(w,session){
 if(pending.has('watch:'+session))return;pending.add('watch:'+session);
 try{
  while(true){const job={...await RR.json('/status/'+session),session};operations.set(w.id,job);repaint(w);
   if(job.state!=='running'&&!job.worker_active){if(['reviewed','needs_review'].includes(job.state))applyReview(w,await RR.json('/review/'+session,job.options||{}));if(RR.operationLabel(job)==='Complete')await refresh();break;}
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
 }catch(error){const job=operations.get(w.id)||{};operations.set(w.id,{...job,session,connectionError:error.message});repaint(w);}
 finally{pending.delete('watch:'+session);}
}
function renderJob(w,job,panel){
 const label=RR.operationLabel(job);node('p',label,panel,{class:'state-title',id:'job-state',role:'status'});
 if(job.connectionError||job.state==='unknown'){
  node('p',job.connectionError||job.failure,panel,{role:'alert'});actionButton('Reconnect status',w,()=>watch(w,job.session));return;
 }
 if(label==='Complete'){actionButton('Open workflow',w,()=>act(()=>handoff(w)));return;}
 const t=job.transfer,active=job.state==='running'||job.worker_active;
 if(t){node('p',t.filename,panel,{class:'progress-file'});node('p',humanBytes(t.bytes)+' / '+humanBytes(t.total_bytes),panel,{class:'progress-meta',id:'transfer-bytes'});
  const progress=node('progress',null,panel,{max:'100','aria-label':'Current file download progress'});if(t.total_bytes)progress.value=100*t.bytes/t.total_bytes;
  if(active&&label==='Downloading'&&t.state==='transferring'){if(t.bytes_per_second!=null)node('p',humanBytes(t.bytes_per_second)+'/s',panel,{id:'transfer-speed'});if(t.eta_seconds!=null)node('p',Math.ceil(t.eta_seconds)+' sec remaining',panel);}
 }
 if(active){
  if(t){button(label==='Paused'?'Resume':'Pause',panel,()=>act(()=>control(w,job,label==='Paused'?'run':'pause'))).disabled=['Pausing…','Resuming…','Cancelling…'].includes(label);}
  button('Cancel',panel,()=>act(()=>control(w,job,'cancel'))).disabled=label==='Cancelling…';
  const l=node('label','Download limit (MB/s; 0 unlimited)',panel),rate=node('input',null,l,{type:'number',id:'download-rate',min:'0',max:'10000',step:'0.1'});rate.value=job.control?.rate_mbps??5;
  button('Apply limit',panel,()=>act(()=>control(w,job,job.control?.action||'run',Number(rate.value)))).disabled=label==='Cancelling…';
 }else{
  node('p',job.failure||job.result?.log||'Installation needs review before continuing.',panel,{class:'uncertainty'});
  actionButton('Resume installation',w,()=>act(async()=>{
   if(pending.has(w.id))return;pending.add(w.id);repaint(w);
   try{const r=await RR.json('/resume/'+job.session,{});if(r.approval_required)applyReview(w,r);else{operations.set(w.id,{...r,session:job.session});watch(w,job.session);}}
   finally{pending.delete(w.id);repaint(w);}
  }));
  button('Review plan',panel,()=>act(async()=>{await mutateReview(w,()=>RR.json('/review/'+job.session,job.options||{}));}));
 }
 if(job.progress){const d=node('details',null,panel);node('summary','Installation report',d);node('pre',job.progress,d);}
}
async function control(w,job,action,rate=job.control?.rate_mbps??5){
 await RR.json('/download-control/'+job.session,{action,rate_mbps:rate});await watchOnce(w,job.session);
}
async function watchOnce(w,session){operations.set(w.id,{...await RR.json('/status/'+session),session});repaint(w);}
function renderStrip(){
 const strip=$('operation-strip');strip.replaceChildren();const entry=[...operations].find(([id,j])=>id!==state.detail&&RR.operationLabel(j)!=='Complete');
 strip.hidden=!entry;if(!entry)return;const w=workflow(entry[0]);if(!w)return;
 node('span',w.name+' · '+RR.operationLabel(entry[1]),strip);button('View workflow',strip,()=>openDetail(w));
}
function environmentDialog(){showDialog('Environment',(d,close)=>{node('p',targetName(),d);node('p',configuration?.environment?.root||'Environment identity unavailable',d);node('p','This bridge is bound to its own Comfy root and interpreter. Target discovery and switching are unavailable.',d);button('Back',d,close);});}
let bridgeID=0;
function hostRequest(action,payload={}){
 if(window.parent===window)return Promise.reject(new Error('Open RenderRepo from the Comfy extension to use this action.'));
 return new Promise((resolve,reject)=>{
  const id='rr-'+(++bridgeID),timer=setTimeout(()=>{window.removeEventListener('message',receive);reject(new Error('Comfy handoff timed out.'));},30000);
  function receive(event){if(event.origin!==location.origin||event.source!==parent||event.data?.type!=='renderrepo:reply'||event.data.id!==id)return;clearTimeout(timer);window.removeEventListener('message',receive);event.data.error?reject(new Error(event.data.error)):resolve(event.data.result);}
  window.addEventListener('message',receive);parent.postMessage({type:'renderrepo:host',id,action,...payload},location.origin);
 });
}
async function handoff(w){
 const job=currentJob(w),path=job?.workflow_available?'/workflow/'+job.session:'/installed-workflow?name='+encodeURIComponent(w.packageName);
 const graph=await RR.json(path);await hostRequest('open',{graph});toast('Workflow opened in Comfy. Choose inputs, then press Run.');
}
const capsuleExports=new Map();
function renderOriginalCapsule(w,parent){
 const r=reviews.get(w.id);if(!r?.imported_workflow)return;
 const section=node('section',null,parent,{class:'original-capsule'});
 node('h2','Original workflow → RR capsule',section);
 node('p','Open the reviewed source without installing or running it. Create a new capsule from the original uploaded bytes, not the editor serialization.',section);
 button('Open original in ComfyUI',section,()=>act(async()=>{const graph=await RR.json('/original/'+r.session);await hostRequest('open',{graph});}));
 const fields={};
 for(const [key,label,value] of [['name','New capsule name',w.packageName+'-capsule'],['version','Capsule version','0.1.0'],['repository','Upstream repository URL',''],['revision','Upstream commit revision',''],['path','Upstream file path','']]){
  const labelNode=node('label',label,section);fields[key]=node('input',null,labelNode,{type:'text','data-capsule-field':key});fields[key].value=value;
 }
 node('p','Provenance is your recorded source reference; RR does not authenticate that upstream reference. Target availability, execution and hardware findings remain outside portable claims.',section,{class:'muted'});
 const save=button('Save original as RR capsule',section,()=>act(async()=>{
  save.disabled=true;try{const result=await RR.json('/capsule/'+r.session,Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value])));capsuleExports.set(w.id,result);render();$('capsule-comparison')?.scrollIntoView({block:'start'});}finally{save.disabled=false;}
 }));
 const result=capsuleExports.get(w.id);if(!result)return;
 const comparison=node('section',null,section,{id:'capsule-comparison'});node('h2','Original JSON beside capsule contents',comparison);
 node('p',result.byte_identical?'Byte-for-byte identical workflow payload':'Workflow bytes differ',comparison,{class:'capsule-byte-result'});
 node('p',result.capsule.format+' · profile '+result.capsule.profile+' · schema '+result.capsule.schema_version+' · '+result.manifest.name+' '+result.manifest.version,comparison);
 node('p','Source SHA-256: '+result.source_sha256,comparison);node('p','Capsule workflow SHA-256: '+result.workflow_sha256,comparison);
 node('p','Capsule identity SHA-256: '+result.capsule.sha256,comparison);node('p','Payload SHA-256: '+result.capsule.payload_sha256,comparison);node('p','Archive SHA-256: '+result.archive_sha256,comparison);
 node('p','JSON is the serialization. The RenderRepo profile defines capsule identity, schema version, provenance and deterministic hashes. A changed package creates a predecessor-linked revision; the capsule hash excludes its own sha256 field. S4 cross-application metadata remains reserved, not middleware.',comparison);
 const columns=node('div',null,comparison,{class:'capsule-columns'}),left=node('section',null,columns),right=node('section',null,columns);
 node('h3','Original upstream JSON',left);node('pre',result.original_json,left,{class:'capsule-source'});
 node('h3','Capsule archive contents',right);node('pre',result.contents.map(x=>x.path+' · '+x.size_bytes+' bytes').join('\n'),right);node('h3','capsule.json',right);node('pre',JSON.stringify(result.capsule,null,2),right,{class:'capsule-descriptor'});
 button('Download capsule archive',comparison,()=>act(async()=>{const blob=await(await RR.request('/capsule/'+r.session+'/archive')).blob(),url=URL.createObjectURL(blob),a=node('a',null);a.href=url;a.download=result.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}));
}
function renderInstalled(){
 const installed=rows.filter(isInstalled).filter(w=>matches(w,state.query));title('My Workflows','Installed in '+targetName()+'. Opening does not run a workflow.',installed.length);
 const list=node('div',null,main,{class:'installed-list'});
 for(const w of installed){const row=node('article',null,list,{class:'installed-row','data-installed':w.id});iconNode(w.icon,row,'thumb');const info=node('div',null,row,{class:'installed-info'});node('h2',w.name,info);node('p',w.purpose,info);if(installedRecord(w)?.workflow_modified)node('p','Local workflow edits are preserved.',info,{class:'uncertainty'});const a=node('div',null,row,{class:'installed-actions'});button('Open',a,()=>act(()=>handoff(w)),{class:'primary'});button('Details',a,()=>openDetail(w));const remove=button('Remove',a,()=>{});remove.disabled=true;remove.title='Workflow-only removal is not exposed by this bridge.';}
 if(!installed.length)node('p','No installed workflows match.',main,{class:'empty'});
}
function renderManage(){
 title('Manage','Sources, imports, history and technical information.');const tabs=node('div',null,main,{class:'manage-tabs'});
 for(const [id,label] of [['sources','Sources'],['history','History'],['import','Import / export'],['analysis','Analysis evidence'],['advanced','Advanced settings']]){const b=button(label,tabs,()=>{state.manage=id;persist();render();main.scrollTop=0;},{'data-manage':id});if(state.manage===id)b.setAttribute('aria-current','page');}
 const body=node('div',null,main,{class:'manage-section'});
 if(state.manage==='import'){renderImport(body);return;}
 if(state.manage==='sources'){
  const c=manageCard('Catalog sources',body);
  for(const s of repositories?.sources||[]){node('p',s.name+' · '+s.uri+' · '+(s.enabled?'Enabled':'Disabled'),c);
   for(const action of [s.enabled?'disable':'enable','remove'])button(action,c,()=>act(async()=>{await RR.json('/repositories/source',{action,name:s.name});await refresh();}));
  }
  const n=node('label','Source name',c),name=node('input',null,n,{id:'catalog-name'}),l=node('label','Catalog location',c),uri=node('input',null,l,{id:'catalog-location',type:'url'});
  button('Add source',c,()=>act(async()=>{await RR.json('/repositories/source',{action:'add',name:name.value,uri:uri.value});await refresh();}));
  button('Refresh catalogs',c,()=>act(async()=>{await RR.repositoryOperation(await RR.json('/repositories/refresh',{source:null}),toast);await refresh();}));
  for(const warning of repositories?.warnings||[])node('p',warning,c,{class:'uncertainty'});
 }else if(state.manage==='history'){
  const c=manageCard('Installation history',body);node('p','Retained by the RenderRepo backend.',c);
  for(const entry of history){const row=node('article',null,c,{class:'installed-row'});node('p',entry.name+' '+entry.version+' · '+(entry.outcome||entry.state),row);node('small',entry.last_activity||'Time unknown',row);
   if(entry.state==='corrupt')node('p',entry.error,row,{class:'uncertainty'});
   else button('View workflow',row,()=>act(()=>restore(entry.session)));
  }
 }else if(state.manage==='analysis'){
  const c=manageCard('Workflow capability evidence',body);
  for(const w of rows){const details=node('details',null,c);node('summary',w.name,details);node('pre',JSON.stringify(w.capabilities,null,2),details);}
 }else{
  const c=manageCard('Environment and opening',body);node('p',targetName()+' · '+(configuration?.environment?.root||'Unknown'),c);node('p','Python: '+(configuration?.python||'Unknown'),c);button('Environment',c,environmentDialog);node('p','Performance and hardware fit: Unknown. Installation and recovery state are retained by the backend.',c);
 }
}
function renderImport(body){
 const c=manageCard('Import a workflow',body);node('p','Inspect Comfy editor JSON or an RR package (up to 64 MiB). Inspection does not install dependencies or execute the workflow.',c);
 const types=node('div',null,c,{class:'import-types'});for(const [type,label] of [['json','Comfy workflow JSON'],['package','RR package'],['url','Direct package URL']])button(label,types,()=>{importType=type;render();},{'aria-pressed':String(importType===type),'data-import-type':type});
 const label=node('label',importType==='url'?'Package URL':'Workflow file',c),input=node('input',null,label,importType==='url'?{type:'url',id:'import-url'}:{type:'file',id:'import-file',accept:importType==='json'?'.json':'.rrpkg'});
 input.disabled=!initialReady;
 if(!initialReady)node('p','Loading existing sources and history…',c,{class:'muted'});
 const submit=button('Prepare workflow',c,()=>act(async()=>{
  submit.disabled=true;
  try{let r;if(importType==='url')r=await RR.repositoryOperation(await RR.json('/package-link',{url:input.value}),toast);
   else{const file=input.files?.[0];if(!file)throw new Error('Choose a file.');if(file.size>64*1024*1024)throw new Error('File exceeds 64 MiB.');r=await RR.json('/preview',file,true);}
   const w=RR.summary({...r.manifest,capabilities:r.capabilities,source:r.source});rows.push(w);applyReview(w,r);state.destination='explore';openDetail(w);
  }finally{submit.disabled=false;}
 }),{id:'prepare-import'});submit.disabled=!initialReady;
 const e=manageCard('Export',body);node('p','Export the current Comfy editor graph using the existing package exporter.',e);
 const n=node('label','Package name',e),name=node('input',null,n,{id:'export-name'}),v=node('label','Version',e),version=node('input',null,v,{id:'export-version'});version.value='0.1.0';
 node('p','Custom-node repositories and bundled inputs must be selected explicitly.',e);
 const nodes=node('div',null,e),selected=new Set();for(const item of configuration?.custom_nodes||[]){const c=check(item.name+(item.exportable?'':' · '+item.unavailable_reason),nodes,'export-node-'+item.name,false,v=>v?selected.add(item.name):selected.delete(item.name));c.disabled=!item.exportable;}
 const inputsLabel=node('label','Input filenames to bundle (one per line)',e),inputs=node('textarea',null,inputsLabel);
 const exportButton=button('Export package',e,()=>act(async()=>{
  exportButton.disabled=true;
  try{const graph=await hostRequest('graph'),r=await RR.json('/export',{workflow:graph,name:name.value,version:version.value,custom_nodes:[...selected],bundle_inputs:inputs.value.split('\n').map(x=>x.trim()).filter(Boolean)});
   let job;do{job=await RR.json('/status/'+r.session);if(job.state==='exported'||job.state==='done')break;toast('Preparing export…');await new Promise(resolve=>setTimeout(resolve,1000));}while(job.state==='exporting'||job.state==='running');
   if(job.result?.exit_code!==0)throw new Error(job.result?.log||'Export incomplete.');
   const blob=await(await RR.request('/archive/'+r.session)).blob(),url=URL.createObjectURL(blob),a=node('a',null);a.href=url;a.download=(name.value.replace(/[^A-Za-z0-9_.-]/g,'-')||'workflow')+'.rrpkg';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('Package exported. Review unresolved dependency notices.');node('pre',job.result.log,e);
  }finally{exportButton.disabled=false;}
 }));
}
async function restore(session){
 const job=await RR.json('/status/'+session),m=job.manifest;
 if(!m)throw new Error('Retained workflow metadata unavailable.');
 let w=rows.find(w=>reviews.get(w.id)?.session===session||operations.get(w.id)?.session===session);
 if(!w){w=RR.summary({...m,source:'Retained installation',capabilities:job.capabilities});w.id='session:'+session;rows.push(w);}
 if(['reviewed','needs_review'].includes(job.state)){applyReview(w,await RR.json('/review/'+session,job.options||{}));}else operations.set(w.id,{...job,session});state.destination='explore';openDetail(w);if(job.state==='running'||job.worker_active)watch(w,session);
}
function renderRefreshed(){
 // Both the refresh and its initial completion must preserve a selected file.
 if(!(state.destination==='manage'&&state.manage==='import'&&$('import-file')?.files?.length))render();
}
async function refresh(){
 const [config,repo]=await Promise.all([RR.json('/config'),RR.json('/repositories')]);configuration=config;repositories=repo;history=config.installations||[];
 const keep=rows.filter(w=>reviews.has(w.id)||operations.has(w.id)||!w.raw.repository);
 rows=(repo.packages||[]).map(RR.summary);for(const w of keep){const i=rows.findIndex(x=>x.id===w.id);if(i<0)rows.push(w);else rows[i]=w;}
 for(const item of repo.installed||[]){if(!rows.some(w=>isSameInstalled(w,item)&&isInstalled(w))){const w=RR.summary({...item,source:item.source});rows.push(w);}}
 renderRefreshed();
}
function isSameInstalled(w,item){return w.packageName===item.name&&w.version===item.version&&(!item.source||w.raw.repository===item.source||w.raw.source===item.source);}
function render(){navRender();$('environment').textContent=targetName();$('search').value=state.query;main.replaceChildren();if(state.route==='detail'&&workflow(state.detail))renderDetail(workflow(state.detail));else if(state.destination==='installed')renderInstalled();else if(state.destination==='manage')renderManage();else renderCatalog();renderStrip();}
$('menu').onclick=()=>setMenu(!$('store').classList.contains('menu-open'));$('scrim').onclick=()=>setMenu(false);$('environment').onclick=environmentDialog;
$('search').oninput=()=>{state.query=$('search').value;if(state.destination==='manage')state.destination='explore';state.route='list';state.detail=null;state.scroll=0;decision=null;render();main.scrollTop=0;};
$('close').onclick=()=>{if(parent!==window)hostRequest('close').catch(error=>toast(error.message));else{$('store').hidden=true;$('closed').hidden=false;}};
$('reopen').onclick=()=>{$('store').hidden=false;$('closed').hidden=true;render();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('store').classList.contains('menu-open'))setMenu(false);});
try{const saved=JSON.parse(localStorage.getItem('renderrepo-storefront-preferences'));if(ACTIONS.some(a=>a[0]===saved?.destination))state.destination=saved.destination;if(['sources','history','import','analysis','advanced'].includes(saved?.manage))state.manage=saved.manage;}catch{}
render();act(async()=>{await refresh();for(const entry of history.filter(e=>e.state==='running'||e.worker_active)){const job=await RR.json('/status/'+entry.session),w=RR.summary({...job.manifest,source:'Retained installation',capabilities:job.capabilities});w.id='session:'+entry.session;rows.push(w);operations.set(w.id,{...job,session:entry.session});watch(w,entry.session);}initialReady=true;renderRefreshed();});
