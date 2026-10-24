/* Same-origin adapter for the existing Comfy-local bridge. No install policy here. */
'use strict';
(function (root) {
  const actionIDs = {'create-image':'create','edit-image':'edit','resize-crop':'resize','combine-images':'combine','create-video':'video'};
  const list = value => Array.isArray(value) ? value : [];
  const labels = value => list(value).map(x => x.label).filter(x => typeof x === 'string');
  const controlLabel = x => x.label + (Object.hasOwn(x,'value') ? ': '+String(x.value) : '');
  const promptValues = c => list(c?.prompts).filter(p=>typeof p.value==='string'&&p.value.trim().length)
    .map(p=>({label:p.label,value:p.value}));
  function summary(item) {
    if (!item || typeof item.name !== 'string') throw new Error('Invalid workflow identity from RenderRepo.');
    const c = item.capabilities || {}, d = item.discovery || {}, count = c.input_counts?.image;
    const exact = count && count.min === count.max;
    const actions = list(c.actions).map(x => actionIDs[x]).filter(Boolean);
    return {id: JSON.stringify([item.repository || item.source || '', item.name, item.version || '', item.sha256 || '']),
      packageName:item.name, version:item.version, source:item.repository || item.source || 'Imported file', raw:item,
      name:d.title || item.name, purpose:d.summary || item.description || c.purpose || 'Purpose unknown', actions,
      images:{required:exact ? count.min : null, optional:null},
      prompts:labels(c.prompts).join(' · ') || 'Prompt requirements unknown',
      provide:labels(c.inputs).map((label,i)=>label+' · '+(c.inputs[i].requirement || 'unknown')).concat(labels(c.prompts),exact&&count.max===0?['No input image']:[]),
      controls:list(c.controls).map(controlLabel), output:labels(c.output_details).join(' · ') || list(c.outputs).map(x=>typeof x==='string'?x:x.kind || x.type || x.label).filter(Boolean).join(' · ') || 'Output unknown',
      automatic:labels(c.processing).join(' · ') || null, tags:list(d.tags),
      icon:({edit:'instructions',create:'create',resize:'resize',combine:'combine',video:'video'})[actions[0]] || 'unknown',
      capabilities:c, targetStatus:item.target_status || 'Requirements unknown', jsonNodeTypes:Array.isArray(c.nodes)?c.nodes:null};
  }
  function nodeEvidence(w, target, readiness) {
    const known = list(target?.nodes);
    const checks = list(readiness?.checks).filter(c=>c.kind==='node');
    const types = w.jsonNodeTypes || known.map(n=>n.type);
    return types.map(type=>{const check=checks.find(c=>c.detail===type),found=known.find(n=>n.type===type);
      return {type,state:check?({ready:'available',missing:'unavailable'})[check.state]||'unknown':found?.state||'unknown'};});
  }
  function operationLabel(job) {
    if (job.state === 'running' || job.worker_active) {
      if (job.control?.action === 'cancel') return 'Cancelling…';
      if (job.control?.action === 'pause') return job.transfer?.state === 'paused' ? 'Paused' : 'Pausing…';
      if (job.transfer?.state === 'paused') return 'Resuming…';
      return job.transfer && !job.transfer.complete ? 'Downloading' : 'Installing';
    }
    if (job.state === 'done' && job.result?.exit_code === 0) return 'Complete';
    return job.outcome || job.state || 'Unknown';
  }
  function validateReview(value) {
    if (!value || typeof value.session !== 'string' || typeof value.review_id !== 'string' ||
        !value.manifest || !value.plan || !value.store_scope || typeof value.can_install !== 'boolean') {
      throw new Error('Incomplete RenderRepo review; installation is unavailable.');
    }
    return value;
  }
  async function request(path, body, raw = false) {
    const headers = {'X-RenderRepo-Request':'1'};
    if (body !== undefined && !raw) headers['Content-Type'] = 'application/json';
    if (raw && body.name) headers['X-RenderRepo-Filename'] = encodeURIComponent(body.name);
    const response = await fetch('/renderrepo'+path, {method:body === undefined ? 'GET':'POST', headers,
      body:body === undefined ? undefined : raw ? body : JSON.stringify(body)});
    if (!response.ok) {
      const error = await response.json().catch(()=>({}));
      throw new Error(`${path}: HTTP ${response.status}: ${error.error || response.statusText}`);
    }
    return response;
  }
  async function json(path, body, raw) { return (await request(path,body,raw)).json(); }
  async function repositoryOperation(value, notify = ()=>{}) {
    while (value.state === 'running') {
      notify(value.progress || 'Preparing…');
      await new Promise(resolve=>setTimeout(resolve,700));
      value = await json('/repositories/operation/'+encodeURIComponent(value.token));
    }
    if (value.state !== 'done') throw new Error(value.error || value.progress || 'Repository operation failed.');
    return value.result;
  }
  const api = {list, summary, promptValues, nodeEvidence, operationLabel, validateReview, request, json, repositoryOperation,
    selection:w=>({name:w.packageName,version:w.raw.version,source:w.raw.repository,sha256:w.raw.sha256})};
  root.RR = api;
  if (typeof module !== 'undefined') module.exports = api;
})(globalThis);
