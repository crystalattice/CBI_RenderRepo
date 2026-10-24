// SPDX-License-Identifier: GPL-3.0-only
import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

function element(tag, text, parent) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  parent?.append(item);
  return item;
}
function installationFailure(result) {
  const report = result.failure || result.result?.log || result.progress || '';
  const cause = report.split('\n').filter(line => /^(FAILED SOURCE:|ERROR:)/.test(line)).at(-1);
  const source = cause?.match(/^FAILED SOURCE: (.+?): (\S+): (.+)$/);
  const message = source ? `Could not download ${source[1]}: ${source[3]}` : cause?.replace(/^ERROR:\s*/, '');
  return message ? `${message.slice(0, 240)}. Correct the reported issue, then retry. Verified files and eligible partials are kept.` :
    'Installation stopped. Check the report, then retry. Verified files and eligible partials are kept.';
}
function transferLabel(job) {
  const active = job.state === 'running' || job.worker_active;
  if (!active) return job.outcome || job.state;
  const action = job.control?.action;
  if (action === 'cancel') return 'Cancelling…';
  if (action === 'pause') return job.transfer?.state === 'paused' ? 'Paused' : 'Pausing…';
  if (job.transfer?.state === 'paused') return action === 'run' ? 'Resuming…' : 'Paused';
  return 'Installing';
}
function requestDetails(path, body) {
  const operations = {'/preview':'Import and review package', '/config':'Load RenderRepo configuration',
    '/history':'Read installation history', '/repositories':'Browse repository packages',
    '/repositories/source':'Manage repository source', '/repositories/refresh':'Refresh repository indexes',
    '/package-link':'Inspect package link', '/repositories/retrieve':'Inspect selected package', '/repositories/operation':'Read repository operation status',
    '/review':'Review installation plan', '/install':'Approve installation', '/status':'Read job status',
    '/resume':'Resume installation', '/download-control':'Change transfer controls', '/workflow':'Open installed workflow', '/installed-workflow':'Open installed workflow',
    '/local-files':'Search local files', '/export':'Export workflow', '/archive':'Retrieve exported archive', '/discard':'Remove retained history'};
  const key = Object.keys(operations).sort((a,b) => b.length-a.length).find(key => path.split('?')[0] === key || path.startsWith(key + '/'));
  const operation = operations[key] || 'RenderRepo request';
  const method = body === undefined ? 'GET' : 'POST';
  const route = `/renderrepo${path}`;
  const address = typeof api.apiURL === 'function' ? api.apiURL(route) : route;
  const url = typeof location !== 'undefined' ? new URL(address, location.href).href : address;
  return {operation, method, route, url};
}
async function request(path, body, raw = false) {
  const {operation, method, route, url} = requestDetails(path, body);
  const headers = { 'X-RenderRepo-Request': '1' };
  if (raw && body?.name) headers['X-RenderRepo-Filename'] = encodeURIComponent(body.name);
  if (body !== undefined && !raw) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await api.fetchApi(route, {method, headers,
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body)});
  } catch (error) {
    throw new Error(`${operation}: ${method} ${url} — no HTTP response (${error.message}). ` +
      'The Comfy backend may have disconnected, timed out, or the browser blocked the connection. ' +
      'Check the fresh Comfy startup log for RenderRepo import/route errors and confirm the backend is running at this page’s origin. ' +
      'Reopen installation history before retrying an install or resume; this request was not automatically retried.');
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const guidance = response.status === 404 ? 'RenderRepo route is missing. Check startup import/registration logs, restart Comfy, and reload this local page.' :
      response.status === 403 ? 'Use the local same-origin Comfy page; this endpoint requires a local RenderRepo request.' :
      response.status >= 500 ? 'Check the Comfy backend traceback for this operation; inspect history before retrying installation.' :
      'Resolve the reported input or plan error and review again.';
    const failure = new Error(`${operation}: ${method} ${response.url || url} — HTTP ${response.status}: ${error.error || response.statusText || 'request rejected'}. ${guidance}`);
    failure.status = response.status; throw failure;
  }
  return response;
}
async function json(path, body, raw = false) {
  const response = await request(path, body, raw);
  try { return await response.json(); }
  catch (error) {
    const {operation, method, url} = requestDetails(path, body);
    throw new Error(`${operation}: ${method} ${response.url || url} — HTTP ${response.status}, but its response body could not be read (${error.message}). ` +
      'Check the Comfy backend log for a disconnect or invalid response. Inspect history before retrying; no installation was automatically retried.');
  }
}
function lines(value) { return value.split('\n').map(x => x.trim()).filter(Boolean); }

async function loadInstalledGraph(graph) {
  // Bundled inputs were copied after the frontend loaded its combo choices.
  // Refresh before configuring the graph so media widgets recognize those files.
  try { await app.refreshComboInNodes(); }
  catch (error) { throw new Error(`Refresh Comfy input choices before opening workflow: ${error.message}. Check the local Comfy connection, then use Open workflow again. The saved workflow and installed files were kept.`); }
  await app.loadGraphData(graph, true, true, null, {openSource: 'renderrepo'});
}

function openPanel() {
  const existing = document.getElementById('renderrepo-panel');
  if (existing) { existing.showModal(); return; }
  const dialog = element('dialog'); dialog.id = 'renderrepo-panel';
  element('style', `
    #renderrepo-panel{width:min(1000px,96vw);height:min(840px,92dvh);max-height:92dvh;box-sizing:border-box;overflow:hidden;padding:0;border:1px solid #606977;border-radius:12px;background:#20242b;color:#eee;font:14px system-ui;display:grid;grid-template-rows:auto auto auto minmax(0,1fr) auto}
    #renderrepo-panel:not([open]){display:none} #renderrepo-panel::backdrop{background:#0009}
    #renderrepo-panel *{box-sizing:border-box} #renderrepo-panel [hidden]{display:none!important}
    #renderrepo-panel .rr-header{display:flex;justify-content:space-between;align-items:center;padding:12px 18px;border-bottom:1px solid #4b5360;gap:12px}
    #renderrepo-panel h2,#renderrepo-panel h3,#renderrepo-panel h4{margin:0 0 12px} #renderrepo-panel .rr-header h2{margin:0;font-size:20px}
    #renderrepo-panel nav{display:grid;grid-template-columns:1fr 1fr auto;gap:4px;padding:8px 12px;border-bottom:1px solid #4b5360}
    #renderrepo-panel button{padding:8px 12px;margin:4px 4px 4px 0;border:1px solid #667;border-radius:6px;background:#303844;color:#fff;cursor:pointer;font:inherit}
    #renderrepo-panel button:disabled{opacity:.45;cursor:default} #renderrepo-panel button[aria-selected=true],#renderrepo-panel .rr-primary{background:#235b88;border-color:#68baf8}
    #renderrepo-panel :focus-visible{outline:3px solid #80d0ff;outline-offset:2px}
    #renderrepo-panel .rr-statusbar{padding:8px 18px;background:#18202a;display:flex;align-items:center;gap:8px;border-bottom:1px solid #4b5360}
    #rr-install-status{flex:1;margin:0;white-space:pre-wrap;max-height:4em;overflow:auto;font-size:13px;color:#9cdaff}
    #rr-content{overflow:auto;min-height:0;padding:18px} #renderrepo-panel section{min-width:0}
    #renderrepo-panel label{display:block;margin:10px 0 5px} #renderrepo-panel input:not([type=checkbox]),#renderrepo-panel textarea,#renderrepo-panel select{width:100%;padding:8px;background:#12151b;color:#eee;border:1px solid #626975;border-radius:5px}
    #renderrepo-panel input[type=checkbox]{margin-right:8px} #renderrepo-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#12151b;padding:10px;max-height:280px;overflow:auto}
    #renderrepo-panel details{margin:10px 0;padding:8px;border:1px solid #48515d;border-radius:6px} #renderrepo-panel summary{cursor:pointer;font-weight:600}
    #renderrepo-panel .rr-card{padding:14px;margin:12px 0;border:1px solid #4b5360;border-radius:8px;background:#252c35} #renderrepo-panel .rr-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:12px 0}
    #renderrepo-panel .rr-review-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px} #renderrepo-panel .rr-review-groups .rr-card{margin:0;padding:10px} #renderrepo-panel .rr-field{overflow-wrap:anywhere} #renderrepo-panel .rr-field strong{display:block;color:#acb6c5;font-size:12px;margin-bottom:3px}
    #renderrepo-panel .rr-badge{display:inline-block;padding:3px 8px;border-radius:12px;background:#34424c;margin:0 5px 5px 0;font-size:12px} #renderrepo-panel .rr-warning{background:#57431f;color:#ffe2a1}
    #renderrepo-panel .rr-approval{background:#20242b;padding:10px 18px;border-top:1px solid #667;margin:0} #renderrepo-panel .rr-approval label{margin:4px 0} #renderrepo-panel .rr-toolbar,#renderrepo-panel .rr-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap} #renderrepo-panel ul{padding-left:20px} #rr-repository-sources,#rr-history{list-style:none;padding:0}
    #rr-install-progress{width:100%;height:18px;accent-color:#42c998} #rr-download-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap} #rr-download-controls input{max-width:100px}
    #rr-steps{display:flex;gap:8px;flex-wrap:wrap;padding:0;list-style:none} #rr-steps li{padding:5px 10px;border-radius:6px;background:#303844} #rr-steps li[aria-current=step]{background:#235b88}
    #renderrepo-panel .rr-package{padding:10px 12px;margin:8px 0;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 12px;align-items:start}
    #renderrepo-panel .rr-package h4{margin:0;font-size:15px;grid-column:1}
    #renderrepo-panel .rr-package p{margin:0;line-height:1.4;overflow-wrap:anywhere}
    #renderrepo-panel .rr-package .rr-package-summary{grid-column:1 / -1}
    #renderrepo-panel .rr-package .rr-package-meta{grid-column:1 / -1;display:flex;gap:6px 12px;flex-wrap:wrap;align-items:center}
    #renderrepo-panel .rr-package .rr-package-inputs{grid-column:1 / -1}
    #renderrepo-panel .rr-package .rr-package-details{grid-column:1 / -1;margin:2px 0 0;padding:5px 8px}
    #renderrepo-panel .rr-package .rr-package-action{grid-column:2;grid-row:1;margin:0}
    #renderrepo-panel .rr-package .rr-badge{margin:0}
    #renderrepo-panel .rr-change-summary{font-size:16px;line-height:1.5;margin:12px 0}
    #renderrepo-panel .rr-consequence{border-left:3px solid #e6b354;padding:8px 12px;margin:8px 0;background:#302c25;overflow-wrap:anywhere}
    #renderrepo-panel .rr-consequence h4{margin:0 0 4px} #renderrepo-panel .rr-consequence p{margin:0;line-height:1.5}
    #renderrepo-panel .rr-failure{line-height:1.5;margin:8px 0;overflow-wrap:anywhere}
    #renderrepo-panel .rr-muted{color:#b0bac8;font-size:13px} #renderrepo-panel .rr-status{white-space:pre-wrap;color:#9cdaff}
    @media(max-width:560px){#renderrepo-panel{width:98vw;height:96dvh;max-height:96dvh;border-radius:6px} #rr-content{padding:12px} #renderrepo-panel .rr-fields,#renderrepo-panel .rr-review-groups{grid-template-columns:1fr} #renderrepo-panel nav button{padding:8px 2px;font-size:13px} #renderrepo-panel .rr-header{padding:10px 12px}}
  `, dialog);
  element('style', `
    #renderrepo-panel{width:min(1360px,98vw);height:min(960px,95dvh);max-height:95dvh;grid-template-columns:210px minmax(0,1fr);grid-template-rows:auto minmax(0,1fr) auto;border-radius:14px}
    #renderrepo-panel .rr-header{grid-column:1/-1;padding:16px 24px}
    #renderrepo-panel nav{grid-column:1;grid-row:2;display:flex;flex-direction:column;align-items:stretch;overflow:auto;padding:20px 12px;background:#1b2027;border:0;border-right:1px solid #39414c;gap:8px}
    #renderrepo-panel nav button{text-align:left;flex-shrink:0;margin:0;padding:12px}
    #renderrepo-panel nav details{border:0;margin:0;padding:8px 0} #rr-categories{display:grid;gap:4px}#rr-categories button[aria-pressed=true]{background:#235b88;border-color:#68baf8}
    #renderrepo-panel #rr-content{grid-column:2;grid-row:2;padding:30px 36px}
    #renderrepo-panel .rr-statusbar{grid-column:1/-1;grid-row:3;border:0;border-top:1px solid #4b5360;padding:8px 20px}
    #renderrepo-panel .rr-store-row{display:flex;align-items:center;gap:18px;width:100%;text-align:left;background:#252c35;margin:10px 0;padding:18px;border-color:#3d4753}
    #renderrepo-panel .rr-store-row>span:nth-child(2){flex:1;min-width:0} #renderrepo-panel .rr-store-row strong{font-size:17px;display:block;margin-bottom:6px}
    #renderrepo-panel .rr-row-purpose{display:block;font-size:14px;color:#c1cbd7;line-height:1.5}
    #renderrepo-panel .rr-tile-icon,#renderrepo-panel .rr-page-icon{font-size:32px;color:#93bce0;background:#303d4b;padding:12px;border-radius:12px}
    #renderrepo-panel .rr-page-hero{display:flex;align-items:center;gap:20px;margin:24px 0} #renderrepo-panel .rr-page-hero>div:first-of-type{flex:1;min-width:0}
    #renderrepo-panel .rr-page-hero h2{font-size:28px} #renderrepo-panel .rr-page-hero p{line-height:1.6} #renderrepo-panel .rr-page-actions{min-width:150px}
    #renderrepo-panel .rr-page-actions button{font-size:16px;padding:12px 20px;width:100%} #renderrepo-panel .rr-example{max-width:100%;max-height:250px;object-fit:contain}
    #renderrepo-panel .rr-search-box{flex:1;max-width:540px;min-width:180px}#renderrepo-panel .rr-search-box label{font-size:12px;margin:0 0 4px}#renderrepo-panel .rr-header{flex-wrap:wrap}#renderrepo-panel .rr-header button{margin-left:auto} #renderrepo-panel .rr-change-summary{font-size:17px} #rr-page-about{margin-top:28px;line-height:1.6} #rr-page-about h3{margin-top:26px}
    #renderrepo-panel #rr-approval-actions{margin:16px 0;border:1px solid #a78b4b;border-radius:8px} #rr-page-state{color:#add2ef}#rr-install-status{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-height:2em}
    @media(max-width:720px){#renderrepo-panel .rr-search-box{order:3;flex-basis:100%;max-width:none}#renderrepo-panel #rr-environment{display:none}#renderrepo-panel{grid-template-columns:145px minmax(0,1fr)}#renderrepo-panel #rr-content{padding:18px}#renderrepo-panel .rr-page-hero{flex-wrap:wrap}.rr-page-actions{flex-basis:100%}#renderrepo-panel .rr-store-row{gap:10px;padding:12px}#renderrepo-panel .rr-tile-icon{display:none}}
    @media(max-width:480px){#renderrepo-panel{grid-template-columns:110px minmax(0,1fr)}#renderrepo-panel nav{padding:12px 5px}#renderrepo-panel nav button{padding:10px 5px;overflow-wrap:anywhere}#renderrepo-panel #rr-content{padding:12px}#renderrepo-panel .rr-page-icon{display:none}#renderrepo-panel .rr-page-hero h2{font-size:22px}#renderrepo-panel .rr-store-row{flex-wrap:wrap}#renderrepo-panel .rr-header{padding:10px}}
  `,dialog);
  element('style','#renderrepo-panel .rr-store-row .rr-muted{display:block} #rr-categories button{overflow-wrap:anywhere}',dialog);
  const header = element('div', undefined, dialog); header.className = 'rr-header';
  element('h2', 'RenderRepo', header);
  const environment = element('span', 'This Comfy environment', header); environment.id='rr-environment'; environment.className='rr-muted';
  const close = element('button', 'Close', header); close.id = 'rr-close-top'; close.setAttribute('aria-label', 'Close RenderRepo'); close.title = 'Close panel; active operations continue';
  const navigation = element('nav', undefined, dialog); navigation.id='rr-sidebar'; navigation.setAttribute('role', 'tablist'); navigation.setAttribute('aria-label', 'RenderRepo views');
  const statusbar = element('div', undefined, dialog); statusbar.className = 'rr-statusbar';
  const status = element('p', 'Loading your packages…', statusbar); status.id = 'rr-install-status'; status.setAttribute('role', 'status');
  const current = element('button', 'Return to workflow', statusbar); current.id = 'rr-current-activity';
  const content = element('main', undefined, dialog); content.id = 'rr-content';
  const views = {}, tabs = {};
  let currentView = 'browse', activeStatus = '';
  const viewStatus = {browse: 'Loading your packages…'};
  const viewDefaults = {browse: 'Browse packages or import a .rrpkg.', installed: 'Open an installed workflow or review available versions.', activity: 'Check active operations and installation history.', sources: 'Manage sources and refresh package listings.', installation: 'Choose a package and review its installation.', export: 'Export the current workflow.'};
  function renderStatus() { status.textContent=(activeStatus || viewStatus[currentView] || '').replaceAll('\n',' · ');statusbar.hidden=currentView==='installation' || !(running || repoBusy || document.getElementById('rr-inline-recovery') || document.getElementById('rr-operation-error'));current.hidden=false; }
  for (const label of ['Browse','Installed','Activity','Sources']) {
    const key = label.toLowerCase();
    const tab = element('button', {Browse:'Explore',Installed:'My Workflows'}[label] || label, navigation); tab.id = 'rr-tab-' + key; tab.setAttribute('role','tab'); tab.setAttribute('aria-controls','rr-view-' + key);
    const panel = element('section', undefined, content); panel.id = 'rr-view-' + key; panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby',tab.id);
    tabs[key] = tab; views[key] = panel; tab.onclick = () => switchView(key);
    tab.addEventListener('keydown', event => {
      const keys = ['browse','installed'], position = keys.indexOf(key);
      const next = event.key === 'ArrowRight' ? keys[(position+1)%2] : event.key === 'ArrowLeft' ? keys[(position+1)%2] : event.key === 'Home' ? keys[0] : event.key === 'End' ? keys[1] : null;
      if (next) { event.preventDefault(); switchView(next); tabs[next].focus(); }
    });
  }
  const manage = element('details', undefined, navigation); manage.id='rr-manage';
  element('summary','Settings / Manage',manage);
  for (const key of ['sources','activity']) { manage.append(tabs[key]); tabs[key].removeAttribute('role'); tabs[key].onclick=()=>{switchView(key);manage.open=false;}; }
  const browseView = views.browse, installedView = views.installed, activityView = views.activity, sourcesView = views.sources;
  const installationPanel = element('section', undefined, content); installationPanel.id = 'rr-installation-panel'; installationPanel.tabIndex = -1; installationPanel.setAttribute('aria-label','Installation');
  const exportPanel = element('section', undefined, content); exportPanel.id = 'rr-export-panel'; exportPanel.tabIndex = -1; exportPanel.setAttribute('aria-label','Export workflow');
  const panels = [...Object.values(views), installationPanel, exportPanel];
  function switchView(key) {
    currentView = key; renderStatus();
    for (const panel of panels) panel.hidden = panel !== (views[key] || (key === 'installation' ? installationPanel : exportPanel));
    for (const [name, tab] of Object.entries(tabs)) { tab.setAttribute('aria-selected', String(name === (views[key] ? key : 'browse'))); tab.tabIndex = name === (views[key] ? key : 'browse') ? 0 : -1; }
    const approval = document.getElementById('rr-approval-actions'); if (approval) approval.hidden = key !== 'installation' || !decisionOpen;
    content.scrollTop = 0; (tabs[key] || (key === 'installation' ? installationPanel : exportPanel)).focus();
  }
  current.onclick = () => switchView('installation');
  const toolbar = element('div', undefined, browseView); toolbar.className = 'rr-toolbar';
  const importAction = element('button', 'Import workflow', manage); importAction.id = 'rr-import-action';
  const exportAction = element('button', 'Export workflow', manage); exportAction.id = 'rr-export-action'; exportAction.onclick = () => switchView('export');
  element('h3', 'My Workflows', installedView);
  const installedResults = element('div', undefined, installedView); installedResults.id = 'rr-installed-results';
  element('h3', 'Activity', activityView);
  const activeOperation = element('div', undefined, content); activeOperation.id = 'rr-active-operation'; activeOperation.hidden=true;
  const repositoryActivity = element('div', undefined, activityView); repositoryActivity.id = 'rr-repository-activity';
  const back = element('button', 'Back', installationPanel); back.id='rr-back';
  let returnView='browse'; back.onclick=()=>{switchView(returnView); repoQuery.focus();};
  const pageHero=element('div',undefined,installationPanel);pageHero.className='rr-page-hero';
  const pageIcon=element('span','◇',pageHero);pageIcon.className='rr-page-icon';pageIcon.setAttribute('aria-hidden','true');
  const pageHeading=element('div',undefined,pageHero);
  const pageTitle=element('h2','Workflow',pageHeading);pageTitle.id='rr-page-title';
  const pagePurpose=element('p','',pageHeading);pagePurpose.id='rr-page-purpose';
  const pageMeta=element('p','',pageHeading);pageMeta.className='rr-muted';
  const pageActions=element('div',undefined,pageHero);pageActions.className='rr-page-actions';
  const directOpen=element('button','Open workflow',pageActions);directOpen.id='rr-direct-open';directOpen.className='rr-primary';directOpen.hidden=true;
  const pageState=element('p','Preparing…',installationPanel);pageState.id='rr-page-state';pageState.setAttribute('role','status');
  const pageAbout=element('div',undefined,installationPanel);pageAbout.id='rr-page-about';
  let selectionGeneration=0,pendingChoice=null;
  let pageResult=null, selectedCategory='', lastChoice=null, previewURLs=[],pageEpoch=0;
  const workflowActions={'create-image':'Create Image','edit-image':'Edit Image','resize-crop':'Resize & Crop','combine-images':'Combine Images','create-video':'Create Video'};
  function actionIds(item) { return [...new Set([...(item.capabilities?.actions || []),...(item.discovery?.actions || [])])].filter(a=>workflowActions[a]); }
  function inputSummary(c) {
    if(!c)return 'Input details not yet available.';
    const facts=[];
    for(const kind of ['image','video']) {
      const slots=(c.inputs || []).filter(i=>i.kind===kind && i.requirement!=='optional');
      if(!slots.length)continue;
      const unknown=slots.some(i=>i.requirement==='unknown'),conditional=slots.some(i=>i.requirement==='mode-dependent');
      facts.push(unknown ? `${slots.length} connected ${kind} input(s); total unknown` : conditional ? `Up to ${slots.length} ${kind} inputs; depends on mode` : `${slots.length} ${kind} input${slots.length===1?'':'s'}`);
    }
    const prompts=c.prompts || [];if(prompts.length)facts.push(prompts.every(p=>p.role==='unknown') ? `${prompts.length} text field${prompts.length===1?'':'s'}; roles unknown` : prompts.map(p=>p.label).join('; '));
    if(c.uncertainties?.some(u=>u.startsWith('Unknown behavior:')))facts.push('Other input requirements unknown');
    return facts.join(' · ') || (c.uncertainties?.length ? 'Input requirements uncertain' : 'No connected file inputs or prompts');
  }
  function capabilitySections(c, parent, replace=false) {
    const section=replace ? parent : element('section',undefined,parent);if(replace)section.replaceChildren();section.id='rr-capabilities';
    element('h3','What this workflow does',section);
    if(!c){element('p','Input details not yet available.',section);return;}
    element('p',c.purpose,section);
    element('p',inputSummary(c),section);
    for(const [label,values] of [
      ['Inputs',(c.inputs || []).map(i=>i.label+(i.slots?' ('+i.slots+' available)':'')+' — '+i.requirement+(i.role && i.role!=='input'?' · '+i.role:''))],
      ['Text fields / prompts',(c.prompts || []).map((i,n)=>(i.role==='unknown'?'Text field '+(n+1)+' (role unknown)':i.label)+' — '+i.requirement)],
      ['Controls',(c.controls || []).map(i=>i.label)],
      ['Processing',(c.processing || []).map(i=>i.label+(i.exposed?'':' (internal preprocessing)'))],
      ['Output',c.outputs || []]]) {
      if(!values.length)continue;const line=element('p',undefined,section);element('strong',label+': ',line);line.append(document.createTextNode([...new Set(values)].join('; ')));
    }
    if(c.uncertainties?.length){const uncertainty=element('p','Some input or branch behavior is uncertain. See Technical details.',section);uncertainty.className='rr-muted';uncertainty.id='rr-capability-uncertainty';}
    element('p','Graph analysis · execution and performance untested by these checks.',section).className='rr-muted';
    const details=element('details',undefined,section);element('summary','Technical details',details);
    element('p','Workflow digest: '+c.workflow_sha256,details);
    for(const issue of c.uncertainties || [])element('p',issue,details);
    for(const e of c.evidence || [])element('p',`Node ${e.node}: ${e.type} — ${e.reason}`,details);
  }
  function pageMetadata(item={}, manifest=null, check=null, analyzed=null) {
    pageEpoch++;
    const d=manifest?.discovery || item.discovery || {};
    pageTitle.textContent=d.title || item.name || manifest?.name || 'Imported workflow';
    pagePurpose.textContent=d.summary || manifest?.description || item.description || '';
    pageMeta.textContent=[item.author || manifest?.author,item.repository || item.source,item.version || manifest?.version].filter(Boolean).join(' · ');
    previewURLs.forEach(url=>URL.revokeObjectURL(url));previewURLs=[];pageAbout.replaceChildren();
    capabilitySections(item._summaryUnavailable ? null : analyzed || manifest?.capabilities || item.capabilities,pageAbout);
    const versions=(repoData?.packages || []).filter(p=>p.name===(item.name || manifest?.name));
    if(versions.length>1 || versions.some(v=>v.version!==(item.version || manifest?.version))){const group=element('details',undefined,pageAbout);element('summary','Other sources and versions',group);const choose=element('select',undefined,group);choose.setAttribute('aria-label','Source and version');
      versions.forEach((v,i)=>{const o=element('option',v.version+' — '+v.repository,choose);o.value=String(i);});
      choose.value=String(Math.max(0,versions.findIndex(v=>v.version===item.version && v.repository===item.repository)));
      const inspect=element('button','Inspect selected version',group);
      const inspectVersion=()=>{if(running)return;invalidate();return showWorkflow({...versions[Number(choose.value)],explicitVersion:true});};
      choose.onchange=inspectVersion;inspect.onclick=inspectVersion;}

    for(const [label,values] of [['Description',manifest?.description && manifest.description!==pagePurpose.textContent ? [manifest.description] : []],['Author input notes',d.required_inputs],['Author limitations',d.limitations]]) {
      if(!values?.length)continue; element('h3',label,pageAbout);const list=element('ul',undefined,pageAbout);for(const value of values)element('li',value,list);
    }
    const nodeTypes=check?.nodes?.map(n=>n.type) || item.dependency_inventory?.nodes || [];
    if(nodeTypes.length || manifest?.artifacts?.length || manifest?.runtime?.custom_nodes?.length) {
      const group=element('details',undefined,pageAbout);element('summary','Dependencies',group);
      for(const name of nodeTypes)element('p',name+' · node',group);
      for(const a of manifest?.artifacts || [])element('p',a.filename,group);
      for(const n of manifest?.runtime?.custom_nodes || []){element('p',n.name+' · '+n.revision,group);let url;try{url=new URL(n.source);}catch{}if(url&&['http:','https:'].includes(url.protocol)&&!url.username&&!url.password){const link=element('a','Upstream: '+n.source,group);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';}}
    }
    if(manifest?.license)element('p','License: '+manifest.license,pageAbout);
    for(const example of d.examples || []) {
      let url;try{url=new URL(example);}catch{continue;}
      if(!['http:','https:'].includes(url.protocol)||url.username||url.password||! /\.(png|jpe?g|webp|gif)$/i.test(url.pathname))continue;
      const im=element('img',undefined,pageAbout);im.src=url.href;im.alt='Supplied example';im.referrerPolicy='no-referrer';im.className='rr-example';im.onerror=()=>im.remove();
    }
  }

  const steps = element('ol', undefined, installationPanel); steps.id = 'rr-steps'; steps.hidden=true;
  const stepItems = {};
  for (const title of ['Package','Review','Install','Complete']) stepItems[title] = element('li', title, steps);
  function setStep(title) { for (const [name, item] of Object.entries(stepItems)) item.setAttribute('aria-current', name === title ? 'step' : 'false'); }
  setStep('Package');
  const packagePhase = element('div', undefined, installationPanel); packagePhase.id = 'rr-package-phase';
  const packageError = element('p', '', packagePhase); packageError.id = 'rr-package-error';
  const reviewPhase = element('div', undefined, installationPanel); reviewPhase.id = 'rr-review-phase'; reviewPhase.hidden = true;
  const summary = element('div', undefined, reviewPhase); summary.id = 'rr-review-summary';
  const recovery = element('p', '', reviewPhase); recovery.id = 'rr-review-recovery';
  const keepInstalled = element('button', 'Go to Installed', reviewPhase); keepInstalled.id = 'rr-keep-installed'; keepInstalled.hidden = true;
  const openKept = element('button', 'Open installed workflow', reviewPhase); openKept.id = 'rr-open-kept'; openKept.hidden = true;
  const advanced = element('details', undefined, reviewPhase); advanced.id = 'rr-review-options'; element('summary', 'Advanced options', advanced);
  const technical = element('details', undefined, installationPanel); technical.id = 'rr-technical-details'; element('summary', 'Technical details and full report', technical);
  const completion = element('div', undefined, installationPanel); completion.id = 'rr-completion'; completion.hidden = true;
  const meter = element('progress', undefined, activeOperation); meter.id = 'rr-install-progress'; meter.max = 100; meter.hidden = true; meter.setAttribute('aria-label', 'Current file transfer progress');
  const transferInfo = element('p', 'No installation currently running.', activeOperation); transferInfo.id = 'rr-transfer-info';
  const downloadControls = element('div', undefined, activeOperation); downloadControls.id = 'rr-download-controls'; downloadControls.hidden = true;
  installationPanel.append(activeOperation,completion);
  const detailsAction = element('button', 'View installation details', completion); detailsAction.id = 'rr-view-details'; detailsAction.onclick = () => { technical.open = true; switchView('installation'); };
  let reviewIdentity = null;
  let session = null, running = false, planReady = false, poll = null, detached = false, watching = 0;
  const jobFailures = new Map();
  let retainedJobs=[];
  let controlGeneration = 0, controlPending = false;
  const setStatus = (text, view = currentView) => { viewStatus[view] = text; activeStatus = running || repoBusy ? text : ''; renderStatus(); };
  async function action(operation, report = setStatus) {
    const origin = currentView;
    try { await operation(); } catch (error) {
      if (detached) return;
      if (report !== setStatus) { report(error.message); return; }
      log.textContent = error.message;pageState.textContent='The operation could not finish. Reconnect to check its status.';
      setStatus(`${error.message.split(':')[0]} failed${error.status ? ' (HTTP ' + error.status + ')' : ''}. See the operation below for details and recovery.`, origin);
      document.getElementById('rr-operation-error')?.remove();
      const failure = element('div', undefined, activeOperation); failure.id = 'rr-operation-error'; failure.className = 'rr-card';
      element('h4', 'Operation needs attention', failure);
      if(session){const reconnect=element('button','Reconnect to installation',failure);reconnect.onclick=()=>action(async()=>{failure.remove();switchView('installation');await selectJob(session);});}
      element('p', 'Check the reported source, file or Comfy connection. Inspect installation history before retrying an install; nothing is retried automatically.', failure);
      activeOperation.hidden=false;
      const details = element('details', undefined, failure); element('summary', 'What failed and how to recover', details); element('pre', error.message, details);
      const view = element('button', 'View error details', failure); view.onclick = () => { technical.open = true; switchView('installation'); };
    }
  }
  element('h3', 'Installation history', activityView);
  const history = element('ul', undefined, activityView); history.id = 'rr-history';
  const refresh = element('button', 'Refresh history', activityView); refresh.id = 'rr-history-refresh';
  async function refreshHistory() {
    const generation = controlGeneration;
    const result = await json('/history');
    if (!detached && generation === controlGeneration && !controlPending) showHistory(result.installations || []);
  }
  refresh.onclick = () => action(refreshHistory);
  function showHistory(entries) {
    retainedJobs=entries;history.replaceChildren();
    if (!entries.length) element('li', 'No retained installations yet.', history);
    for (const job of entries) {
      const row = element('li', undefined, history);
      const transfer = job.transfer;
      const percent = transfer?.total_bytes > 0 ? ` — ${(transfer.bytes / transfer.total_bytes * 100).toFixed(1)}% (${transfer.filename})` : '';
      element('p', `${job.name || 'Package'} ${job.version || ''} — ${transferLabel(job)}${percent}\nLast activity: ${job.last_activity ? humanTime(job.last_activity) : 'Unknown'}${job.error ? '\n' + job.error : ''}`, row);
      const failure = job.failure ? installationFailure(job) : jobFailures.get(job.session);
      if (failure) { const explanation = element('p', failure, row); explanation.className = 'rr-failure'; if (job.session === session) explanation.id = 'rr-activity-error'; }
      if (job.state !== 'corrupt') {
        const view = element('button', 'View status / report', row);
        view.disabled = running && session !== job.session;
        view.onclick = () => action(async () => { switchView('installation'); await selectJob(job.session); });
        if (['interrupted', 'done', 'needs_review'].includes(job.state) && job.outcome !== 'completed') {
          const resume = element('button', ['interrupted', 'cancelled'].includes(job.outcome || job.state) ? 'Resume installation' : 'Retry installation', row);
          resume.className = 'rr-primary'; resume.disabled = job.worker_active === true;
          resume.onclick = () => action(async () => {
            await selectJob(job.session, false);
            const result = await json(`/resume/${session}`, {});
            if (result.approval_required) showPlan(result);
            else watchInstallation();
            await refreshHistory();
          });
        }
        if (job.workflow_available) {
          const load = element('button', 'Open installed workflow', row);
          load.onclick = () => action(async () => {
            await loadInstalledGraph(await json(`/workflow/${job.session}`)); setStatus('Workflow opened in Comfy.');
          });
        }
      }
      const historyDetails = element('details', undefined, row); element('summary', 'History options', historyDetails);
      element('p', 'Exact last activity: ' + (job.last_activity || 'Unknown'), historyDetails);
      const remove = element('button', 'Remove retained history (installed files stay)', historyDetails);
      remove.disabled = job.state === 'running' || job.worker_active === true;
      remove.onclick = () => action(async () => {
        await json(`/discard/${job.session}`, {});
        if (session === job.session) {
          watching++; clearTimeout(poll); session = null; running = false; invalidate();
          pause.disabled = true; cancel.disabled = true; meter.hidden = true;
          [upload, directories, sources, python, exporter].forEach(x => x.disabled = false);
          review.disabled = true; log.textContent = 'Retained package removed. Installed files were kept.';
        }
        await refreshHistory();
      });
    }
  }
  async function selectJob(token, watch = true) {
    watching++; clearTimeout(poll); session = token; invalidate();
    setStatus('Installation details are shown below. Live progress is available in Activity.', 'installation');
    const result = await json(`/status/${token}`);
    running = result.state === 'running' || result.worker_active === true;
    if(result.manifest){repoChoice=null;lastChoice=null;pageMetadata({name:result.manifest.name,version:result.manifest.version},result.manifest,result.target_check,result.capabilities);pageResult=result;directOpen.hidden=true;reviewPhase.hidden=false;packagePhase.hidden=true;showReviewSummary({...result,can_install:result.plan?.exit_code===0});}
    if (result.manifest) {
      dependency.replaceChildren();
      for (const artifact of result.manifest.artifacts) { const option = element('option', artifact.filename, dependency); option.value = artifact.filename; }
    }
    reviewIdentity=result.review_id;
    if (result.options) {
      directories.value = (result.options.artifact_dirs || []).join('\n');
      sources.value = (result.options.sources || []).join('\n');
      python.checked = result.options.python_dependencies === true;
    }
    if(['reviewed','needs_review'].includes(result.state)){showPlan({...result,session:token,name:result.manifest?.name,version:result.manifest?.version,can_install:result.plan?.exit_code===0});}else if (watch) watchInstallation();
  }
  element('h3', 'Sources', sourcesView);
  element('p', 'Sources provide package listings. Removing a source leaves installed packages and workflows in place.', sourcesView);
  const sourceOperationDetails = element('details', undefined, sourcesView); element('summary', 'Refresh and connection details', sourceOperationDetails);
  const repoNotice = element('p', '', sourceOperationDetails); repoNotice.id = 'rr-repository-status'; repoNotice.className = 'rr-status'; repoNotice.setAttribute('role', 'status');
  const sourceFeedback = {};
  const repoReport = message => { repoNotice.textContent = message; if (!running) setStatus(message.length > 180 ? message.split(':')[0] + ' needs attention. See Sources or Activity for details.' : message, 'sources'); };
  let addFormInitialized = false;
  const addForm = element('details', undefined, sourcesView); addForm.id = 'rr-source-add-form'; addForm.open = true; element('summary', 'Add a package source', addForm);
  element('label', 'Display name (optional)', addForm);
  const repoName = element('input', undefined, addForm); repoName.id = 'rr-repository-name'; repoName.placeholder = 'Derived from location if blank';
  element('label', 'Location on this computer or an index URL', addForm);
  const repoURI = element('input', undefined, addForm); repoURI.id = 'rr-repository-uri'; repoURI.placeholder = '/path/to/packages or https://example.org/packages/index.json';
  const repoAdd = element('button', 'Add source', addForm); repoAdd.id = 'rr-repository-add';
  const repoRefresh = element('button', 'Refresh enabled sources', sourcesView); repoRefresh.id = 'rr-repository-refresh';
  const repoSources = element('ul', undefined, sourcesView); repoSources.id = 'rr-repository-sources';
  element('h3', 'Explore workflows', browseView);
  const repoQuery = element('input', undefined, browseView); repoQuery.id = 'rr-repository-query'; repoQuery.placeholder = 'For example: resize photo, crop portrait';
  const searchBox=element('div',undefined,header);searchBox.className='rr-search-box';const searchLabel=element('label','Find a workflow',searchBox);searchLabel.htmlFor=repoQuery.id;searchBox.append(repoQuery);header.append(close);
  const repoSearch = element('button', 'Reload package list', browseView); repoSearch.id = 'rr-repository-search'; repoSearch.hidden = true;
  const browseNotice = element('p', 'Loading saved package listings…', browseView); browseNotice.id = 'rr-browse-notice'; browseNotice.className = 'rr-muted';
  const repoResults = element('div', undefined, browseView); repoResults.id = 'rr-repository-results';

  const updateHelp = element('p', 'Replacing versions is not an atomic upgrade and has no rollback. Safe replacement is not available in this interface yet. Keep using your installed workflow, or review the available version without removing anything.', installedView); updateHelp.id = 'rr-update-limitation'; updateHelp.hidden = true;
  const repoUpdates = element('div', undefined, installedView); repoUpdates.id = 'rr-repository-updates'; installedView.append(installedResults);
  const repoSelected = element('div', 'Choose a package to review.', packagePhase); repoSelected.id = 'rr-repository-selection';
  const linkForm = element('details', undefined, sourcesView); element('summary', 'Install from link', linkForm);
  element('label', 'Direct .rrpkg URL', linkForm);
  const packageURL = element('input', undefined, linkForm); packageURL.id = 'rr-package-url'; packageURL.placeholder = 'https://publisher.example/package.rrpkg';
  const linkInstall = element('button', 'Inspect package', linkForm); linkInstall.id = 'rr-link-install';
  const linkError = element('p', '', linkForm); linkError.id = 'rr-link-error'; linkError.setAttribute('role','status');
  linkInstall.onclick = () => action(async () => {
    if (running || repoBusy) throw new Error('Wait for the current operation.');
    linkInstall.disabled = true; linkError.textContent = 'Retrieving package for inspection…';
    try {
      beginInspection('Inspecting package link…');
      await watchRepositoryOperation(await json('/package-link', {url: packageURL.value.trim()}));
      linkError.textContent = packageError.textContent || 'Inspection requested. Follow its status in Activity.';
    } finally { linkInstall.disabled = false; }
  }, message => { linkError.textContent = message; });
  const official = element('details', undefined, sourcesView); element('summary', 'Official catalog — optional configuration', official);
  const officialStatus = element('p', 'Not configured. Community catalogs and imported packages work independently.', official); officialStatus.id = 'rr-official-status';
  element('label', 'Official index URL (leave blank until the real URL is available)', official);
  const officialURI = element('input', undefined, official); officialURI.id = 'rr-official-uri';
  const officialSave = element('button', 'Save official catalog setting', official); officialSave.id = 'rr-official-save';
  officialSave.onclick = () => action(async () => {
    officialSave.disabled = true;
    try { showRepositories(await json('/repositories/source', {action:'official', uri:officialURI.value.trim()})); }
    finally { officialSave.disabled = false; }
  }, message => { officialStatus.textContent = message; });
  let repoData = null, repoChoice = null, repoBusy = false, repoTimer = null, repoGeneration = 0;
  function bytes(value) {
    if (value == null) return 'Size unknown';
    if (value < 1000) return `${value} B`;
    if (value < 1e6) return `${(value/1000).toFixed(1)} KB`;
    if (value < 1e9) return `${(value/1e6).toFixed(1)} MB`;
    return `${(value/1e9).toFixed(2)} GB`;
  }
  function humanTime(value) {
    if (!value) return 'Never';
    const age = Math.max(0, (Date.now()-Date.parse(value))/1000);
    if (!Number.isFinite(age)) return 'Unknown';
    if (age < 60) return 'Just now';
    if (age < 3600) return `${Math.floor(age/60)} min ago`;
    if (age < 86400) return `${Math.floor(age/3600)} hr ago`;
    return new Date(value).toLocaleDateString();
  }
  function field(parent, label, value) {
    const row = element('div', undefined, parent); row.className = 'rr-field';
    element('strong', label, row); return element('span', value, row);
  }
  function badge(parent, text, warning = false) {
    const item = element('span', text, parent); item.className = 'rr-badge' + (warning ? ' rr-warning' : '');
  }
  function beginInspection(message, choice = null) {
    selectionGeneration++;
    watching++; clearTimeout(poll); session = null; repoChoice = choice;lastChoice=choice;reviewGeneration++; invalidate(); review.disabled = true;
    packageError.textContent = ''; reviewPhase.hidden = true; packagePhase.hidden = false; completion.hidden = true;
    activeOperation.hidden=true;
    for(const id of ['rr-operation-error','rr-activity-complete','rr-inline-recovery']) document.getElementById(id)?.remove();
    resumeAction=null;pageMetadata(choice || {});directOpen.hidden=true;pageState.textContent='Preparing…';install.hidden=false;install.textContent='Preparing…';pageAbout.hidden=false;repoSelected.textContent = ''; switchView('installation'); setStep('Package'); setStatus(message, 'installation');
  }
  async function chooseRepositoryPackage(item) {
    if (running) { switchView('installation'); return; }
    returnView=currentView==='installed'?'installed':'browse';lastChoice=item;beginInspection(`Preparing ${item.discovery?.title || item.name}…`, item);
    if(repoBusy){pendingChoice=item;return;}
    await action(async () => {
      await watchRepositoryOperation(await json('/repositories/retrieve', {name:item.name, version:item.version, source:item.repository, sha256:item.sha256}));
    }, message => { packageError.textContent = message; });
  }
  function taskTokens(text) {
    const words = text.normalize('NFKC').toLowerCase().replace(/\btouch[\W_]*up\b/g, 'repair').match(/[\p{L}\p{N}]+/gu) || [];
    return new Set(words.filter(w => !(repoData?.search_stop_words || []).includes(w)).map(w => repoData?.search_vocabulary?.[w] || w));
  }
  function taskScore(item, query) {
    const words = taskTokens(query); if (!words.size) return 1;
    const d = item.discovery || {};
    const c=item.capabilities || {};
    const fields = [...(d.tasks || []).map(t => [4,t]), [3,c.purpose || ''], ...(c.processing || []).filter(p=>p.exposed).map(p=>[3,p.label]), ...(c.actions || []).map(a=>[2,workflowActions[a] || '']), [3,(d.tags || []).join(' ')], [2,d.title || ''], [1,d.summary || ''], [1,item.description || ''], [1,item.name]]
      .map(([tier,text]) => [tier,taskTokens(text)]);
    const all = new Set(fields.flatMap(([,terms]) => [...terms]));
    if (![...words].every(w => all.has(w))) { const fallback = item.name + (!item.discovery ? ' ' + (item.description || '') : ''); return fallback.toLowerCase().includes(query.toLowerCase()) ? .1 : 0; }
    return Math.max(.5,...fields.filter(([,terms]) => [...words].every(w => terms.has(w))).map(([tier,terms]) => tier*100+words.size/terms.size));
  }
  function readinessControl(parent, selection) {
    const area = element('div', undefined, parent); area.className = 'rr-readiness';
    const status = element('p', 'Inspect this environment’s requirements on demand. Installation checks these automatically.', area); status.setAttribute('role','status');
    const button = element('button', 'Check local readiness', area);
    const details = element('details', undefined, area); element('summary', 'Readiness evidence', details);
    const evidence = element('div', undefined, details);
    let generation = 0;
    button.onclick = () => action(async () => {
      const current = ++generation;
      button.disabled = true; evidence.replaceChildren(); status.textContent = 'Checking local readiness…';
      try {
        const report = await json('/readiness', selection());
        if (current !== generation) return;
        status.textContent = 'Local readiness: ' + report.state + ' — observed ' + new Date(report.checked_at).toLocaleString();
        evidence.replaceChildren();
        element('p', report.dependency_source + '. Server: ' + report.server, evidence);
        const runtime = report.runtime || {};
        element('p', `Observed runtime: Comfy ${runtime.comfyui_version || 'unknown'} · Python ${(runtime.python_version || 'unknown').split(' ')[0]} · Torch ${runtime.pytorch_version || 'unknown'} · Devices: ${(runtime.device_types || []).join(', ') || 'unknown'}`, evidence);
        for (const check of report.checks) element('p', check.state + ' · ' + check.kind + ': ' + check.detail, evidence);
        element('p', report.limitation, evidence);
        button.textContent = 'Recheck local readiness';
      } finally { button.disabled = false; }
    }, message => { status.textContent = 'Local readiness: Unknown — ' + message; });
    return () => { generation++; status.textContent = 'Local readiness: Unknown — selection changed; check again'; evidence.replaceChildren(); };
  }
  function packageRows(items, parent) {
    parent.replaceChildren();const seen=new Set();
    if(!items.length) element('p','No workflows match. Try another task or add a source in Settings.',parent);
    for(const item of items) {
      const id=item.repository+':'+item.name;if(seen.has(id))continue;seen.add(id);
      const versions=items.filter(p=>p.name===item.name&&p.repository===item.repository);
      const chosen=versions[versions.length-1],d=chosen.discovery || {};
      const row=element('button',undefined,parent);row.className='rr-store-row';row.setAttribute('data-package',chosen.name);row.setAttribute('data-catalog',chosen.repository);
      const icon=element('span','◇',row);icon.className='rr-tile-icon';icon.setAttribute('aria-hidden','true');
      const text=element('span',undefined,row);element('strong',d.title || chosen.name,text);
      element('span',d.summary || chosen.description || '',text).className='rr-row-purpose';
      element('span',inputSummary(chosen.capabilities),text).className='rr-row-purpose';
      if(chosen.capabilities?.processing?.length)element('span',[...new Set(chosen.capabilities.processing.filter(p=>p.exposed).map(p=>p.label))].slice(0,3).join(' · '),text).className='rr-muted';
      if(chosen.target_status)element('span',chosen.target_status+' · performance untested',text).className='rr-muted';
      if(chosen.author)element('span',chosen.author,text).className='rr-muted';
      if(chosen.installed_identity)element('span',chosen.status==='partial'||chosen.installed_status==='partial'?'Needs attention':chosen.workflow_modified?'Edited locally':'Installed',row).className='rr-badge';
      if(repoQuery.value.trim())element('span',taskScore(chosen,repoQuery.value)>=200 && !/face[ -]?swap/i.test(repoQuery.value)?'Task match':'Related match — check capabilities',text).className='rr-muted';
      row.onclick=()=>showWorkflow(chosen);
    }
  }
  function showWorkflow(item) {
    if(running){switchView('installation');return;}
    pendingChoice=null;
    returnView=currentView==='installed'?'installed':'browse';lastChoice=item;repoChoice=item;
    const installed=repoData?.installed?.find(r=>r.name===item.name && r.workflow_available);
    const unfinished=retainedJobs.find(j=>j.name===item.name && (j.worker_active || ['running','interrupted','needs_review'].includes(j.state) || ['failed','cancelled'].includes(j.outcome)));
    if(unfinished && (!installed || item.status==='partial' || item.installed_status==='partial')){switchView('installation');return action(()=>selectJob(unfinished.session));}
    if(installed && (item.installed_identity || !item.repository) && (!item.explicitVersion || item.version===installed.version)) {
      selectionGeneration++;pageMetadata({...item,version:installed.version,_summaryUnavailable:installed.workflow_modified || item.version!==installed.version});const epoch=++pageEpoch;pageState.textContent=installed.workflow_modified?'Installed · Your local edits are kept.':'Installed · Checking requirements…';json('/readiness',{installed:item.name}).then(report=>{if(detached||epoch!==pageEpoch)return;if(report.capabilities)capabilitySections(report.capabilities,document.getElementById('rr-capabilities'),true);pageState.textContent='Installed · Static requirements: '+report.state+(installed.workflow_modified?' · Your edits are kept.':'');}).catch(()=>{if(epoch===pageEpoch)pageState.textContent='Installed · Requirements unknown';});pageResult=null;
      reviewPhase.hidden=true;packagePhase.hidden=true;completion.hidden=true;activeOperation.hidden=true;
      install.hidden=true;open.hidden=true;directOpen.hidden=false;directOpen.textContent='Open workflow';
      directOpen.onclick=()=>action(()=>openInstalled(item.name));
      const details=element('details',undefined,pageAbout);element('summary','Manage this workflow',details);
      readinessControl(details,()=>({installed:item.name}));localTestControl(details,item.name);
      switchView('installation');return;
    }
    if(!item.repository && !item.uri){pageMetadata(item);pageState.textContent='Installation incomplete. Import the original package to prepare the remaining files.';reviewPhase.hidden=true;packagePhase.hidden=true;completion.hidden=true;install.hidden=true;open.hidden=true;directOpen.hidden=false;directOpen.textContent='Import original package';directOpen.onclick=()=>upload.click();switchView('installation');return;}
    return chooseRepositoryPackage(item);
  }
  async function openInstalled(name) {
    const graph = await json('/installed-workflow?name=' + encodeURIComponent(name));
    await loadInstalledGraph(graph); dialog.close(); setStatus('Workflow opened in Comfy. Check inputs before running it.');
  }
  let testListing = null;
  function localTestControl(parent, name) {
    if (!['rr-starter-resize','rr-starter-portrait-crop','rr-starter-overlay'].includes(name)) return;
    const area=element('div',undefined,parent); area.className='rr-local-test';
    const prepare=element('button','Test locally',area);
    const count=element('select',undefined,area); count.setAttribute('aria-label',name+': test runs');
    for (const [value,label] of [['1','First observed run only'],['2','First observed run + recomputed repeat']]) { const option=element('option',label,count); option.value=value; }
    count.value='2';
    const notice=element('p','No local execution result yet. Preparing a review does not submit a workflow.',area); notice.setAttribute('role','status');
    const history=element('select',undefined,area); history.setAttribute('aria-label',name+': recorded tests'); history.hidden=true;
    const records=new Map();
    const review=element('div',undefined,area); review.hidden=true;
    const summary=element('p','',review);
    const start=element('button','Start reviewed test',review);
    const stop=element('button','Stop',area); stop.hidden=true;
    const reconcile=element('button','Reconcile known jobs',area); reconcile.hidden=true;
    const details=element('details',undefined,area); element('summary','Local test evidence',details);
    const evidence=element('pre','',details);
    let token=null, approval=null, generation=0;
    function show(result) {
      if (!result.plan) { notice.textContent='Unresolved: '+(result.error || 'Test evidence unavailable'); return; }
      token=result.id || result.plan.id;
      records.set(token,result);history.replaceChildren();
      for(const r of [...records.values()].sort((a,b)=>(b.created_at || '').localeCompare(a.created_at || ''))){const option=element('option',(r.created_at || r.plan.created_at || r.id)+' · '+r.state,history);option.value=r.id || r.plan.id;}
      history.value=token;history.hidden=records.size<2;
      const shape=result.plan.context.inputs.map(i=>i.dimensions.join('×')+' batch '+i.batch).join(', ');
      const timings=(result.runs || []).map(r=>{
        const e=r.evidence || {},o=r.output || {};
        return `${r.label}: ${r.state.replaceAll('_',' ')} · ${e.computation || 'Computation unverified'} · execution ${e.execution_seconds == null ? 'Unknown' : e.execution_seconds+' s'} · queue ${e.queued_seconds == null ? 'Unknown' : e.queued_seconds+' s'} · total ${r.total_elapsed_seconds == null ? 'pending' : r.total_elapsed_seconds.toFixed(3)+' s'} · output verification ${o.verification_seconds == null ? 'Unknown' : o.verification_seconds.toFixed(3)+' s'}${r.execution_may_continue ? ' · Current operation may continue' : ''}`;
      });
      notice.textContent=`${result.state.replaceAll('_',' ')} · ${shape} · Comfy ${result.plan.context.runtime.system.comfyui_version} · ${result.plan.context.runtime.devices.map(d=>d.type+' '+d.name).join(', ')}. `+timings.join(' | ')+' Process memory: Unknown; whole-device samples are in evidence.'+(result.historical_differences?.length ? (result.historical_comparison==='unknown' ? ' Historical comparison unavailable: ' : ' Historical configuration differs: ')+result.historical_differences.join('; ') : '');
      evidence.textContent=JSON.stringify(result,null,2);
      const live=(result.runs || []).some(r=>r.execution_may_continue) || ['approved','running'].includes(result.state);
      stop.hidden=!live; reconcile.hidden=!!result.worker_active || !live && result.state!=='unresolved';
      prepare.disabled=live; count.disabled=live;history.disabled=live;
    }
    async function poll(current) {
      if (current!==generation) return;
      try {
        const result=await json('/local-tests/'+token); if(current!==generation) return; show(result);
        if(result.worker_active || (result.runs || []).some(r=>r.execution_may_continue)) setTimeout(()=>poll(current),1000);
      } catch(error) { notice.textContent='Unresolved: '+error.message+'. Reopen Installed to reconnect; nothing will be resubmitted.'; }
    }
    prepare.onclick=()=>action(async()=>{
      generation++; prepare.disabled=true;review.hidden=true;
      try {
        const result=await json('/local-tests/plan',{name,runs:Number(count.value),deadline:60});
        token=result.plan.id;approval=result.approval_sha256;const p=result.plan;
        summary.textContent=`Server: ${p.context.server}. Configuration: ${name} ${p.context.package.version}; controls ${JSON.stringify(p.configuration?.controls || {})}, expected output ${(p.configuration?.expected_output_dimensions || []).join('×')} batch 1; inputs ${p.context.inputs.map(i=>i.filename+' '+i.dimensions.join('×')+' batch '+i.batch).join('; ')}. Runs: ${p.runs}. Deadline: ${p.deadline_seconds} s per submission; up to ${p.max_elapsed_before_deadline_reporting_seconds} s including output verification before deadline reporting. Outputs: ${p.expected_output_directory}. ${p.recomputation} ${p.warning} ${p.deadline_limit_note} A deadline is not proof that execution stopped.`;
        evidence.textContent=JSON.stringify(result,null,2);review.hidden=false;start.disabled=false;count.disabled=false;
      } finally { prepare.disabled=false; }
    },message=>{notice.textContent=message;});
    history.onchange=()=>action(async()=>{generation++;review.hidden=true;start.disabled=true;token=history.value;const result=await json('/local-tests/'+token);show(result);if(result.worker_active)poll(generation);});
    count.onchange=()=>{generation++;review.hidden=true;start.disabled=true;notice.textContent='Run count changed. Prepare and review a new test before starting.';};
    start.onclick=()=>action(async()=>{
      start.disabled=true;
      const result=await json('/local-tests/'+token+'/start',{approval_sha256:approval});
      testListing=null; review.hidden=true;show(result);poll(++generation);
    },message=>{notice.textContent=message;});
    stop.onclick=()=>action(async()=>{const result=await json('/local-tests/'+token+'/stop',{});notice.textContent=result.message;poll(++generation);});
    reconcile.onclick=()=>action(async()=>{await json('/local-tests/'+token+'/reconcile',{});poll(++generation);});
    const initial=generation;
    if(!testListing) testListing=json('/local-tests');
    testListing.then(data=>{
      if(initial!==generation) return;
      const previous=(data.tests || []).filter(t=>t.plan?.context.package.name===name).sort((a,b)=>b.created_at.localeCompare(a.created_at));
      for(const r of previous) records.set(r.id,r);
      const latest=previous[0];
      if(latest){show(latest);if(latest.worker_active || ['approved','running'].includes(latest.state) || (latest.runs || []).some(r=>r.execution_may_continue))poll(generation);}
    }).catch(error=>{notice.textContent='Local test history unavailable: '+error.message;});
  }
  function showInstalled() {
    const items=(repoData?.installed || []).map(i=>{
      const catalog=repoData.packages.find(p=>p.name===i.name && p.repository===i.source);
      return {...catalog,...i,repository:catalog?.repository,installed_identity:true};
    });
    packageRows(items,installedResults);
    updateHelp.hidden=!(repoData?.updates || []).length;
  }
  const categories=element('div',undefined,navigation);categories.id='rr-categories';
  navigation.append(categories,tabs.installed,manage);
  function renderCategories() {
    categories.replaceChildren();
    const actions=new Set((repoData?.packages || []).flatMap(actionIds));
    for(const [tag,label] of Object.entries(workflowActions)){if(!actions.has(tag))continue;const b=element('button',label,categories);b.setAttribute('data-action',tag);b.setAttribute('aria-pressed',String(selectedCategory===tag));b.onclick=()=>{selectedCategory=selectedCategory===tag?'':tag;switchView('browse');renderCategories();renderRepositoryPackages();};}
  }
  tabs.browse.onclick=()=>{selectedCategory='';switchView('browse');renderCategories();renderRepositoryPackages();};
  function renderRepositoryPackages() {
    const query = repoQuery.value.trim();
    const ranked = (repoData?.packages || []).filter(item => (!selectedCategory || actionIds(item).includes(selectedCategory)) && taskScore(item, query)).sort((a,b) => taskScore(b,query)-taskScore(a,query));
    packageRows(ranked, repoResults);
    if(/face[ -]?swap/i.test(query)) {if(!ranked.length)repoResults.replaceChildren();element('p','No graph-verified face-swap workflow in this catalog. Edit Image contains related alternatives, not verified dedicated face-swap tools.',repoResults);}
    showInstalled();
  }
  function showRepositoryActivity(operations) {
    repositoryActivity.replaceChildren();
    for (const operation of operations) {
      const row = element('div', undefined, repositoryActivity); row.className = 'rr-card';
      element('h4', operation.kind === 'refresh' ? 'Source refresh' : 'Package review', row);
      field(row, 'Status', operation.state === 'running' ? 'In progress' : operation.state === 'done' ? 'Complete' : 'Failed');
      if (operation.kind === 'refresh') {
        const succeeded = (operation.sources || []).filter(s => s.state === 'success').length;
        const failed = (operation.sources || []).filter(s => s.state === 'failed').length;
        element('p', `${succeeded} succeeded, ${failed} failed${operation.state === 'running' ? ' — refresh in progress' : ''}.`, row);
        if (failed) element('p', 'Failed sources keep their last valid listings, marked stale. See Sources for recovery details.', row);
      } else element('p', operation.progress || '', row);
      field(row, operation.finished_at ? 'Finished' : 'Started', humanTime(operation.finished_at || operation.started_at));
      const timing = element('details', undefined, row); element('summary', 'Exact operation times', timing);
      element('p', `Started: ${operation.started_at || 'Unknown'}\nFinished: ${operation.finished_at || 'Not recorded'}`, timing);
      if (operation.error) { const details = element('details', undefined, row); element('summary', 'Failure details', details); element('pre', operation.error, details); }
      const view = element('button', operation.result ? 'Review changes' : operation.kind === 'refresh' ? 'View Sources' : 'Return to Browse', row);
      view.disabled = operation.result && running;
      view.onclick = () => operation.result ? showPlan(operation.result) : switchView(operation.kind === 'refresh' ? 'sources' : 'browse');
    }
  }
  function showRepositories(data) {
    repoData = data; officialStatus.textContent = data.official_catalog ? 'Configured. Listings refresh independently of community catalogs.' : 'Not configured. No official URL is contacted.'; officialURI.value = data.official_catalog || ''; if (viewStatus.browse === 'Loading your packages…') setStatus('Ready. Browse packages or open an installed workflow.', 'browse'); repoSources.replaceChildren(); showRepositoryActivity(data.operations || []);
    if (!addFormInitialized) {
      addForm.open = !data.sources.length;
      addFormInitialized = true;
    }
    browseNotice.textContent = data.warnings?.length ? 'Some sources are offline or need refresh. Saved packages remain available; see Sources for details.' : '';browseNotice.hidden=!data.warnings?.length;
    if (!data.sources.length) element('li', 'No sources configured. Add a local folder or repository index URL above.', repoSources);
    for (const source of data.sources) {
      const row = element('li', undefined, repoSources); row.className = 'rr-card'; row.setAttribute('data-source', source.name);
      element('h4', source.display_name || source.name, row); badge(row, source.enabled ? 'Enabled' : 'Disabled');
      badge(row, source.cached ? (source.stale ? 'Stale source' : 'Fresh') : 'Not refreshed', source.stale);
      element('p', 'Last refreshed: ' + humanTime(source.refreshed_at), row);
      const notice = element('p', sourceFeedback[source.name]?.message || (source.error ? 'Catalog refresh failed: ' + source.error + '. Correct the location or restore the source, then Refresh.' : ''), row); notice.className = 'rr-status';
      const details = element('details', undefined, row); element('summary', 'Location and refresh details', details);
      element('pre', `${source.uri}\nLast successful refresh: ${source.refreshed_at || 'Never'}\nLast attempt: ${source.checked_at || 'Never'}\n${sourceFeedback[source.name]?.log || source.error || ''}`, details);
      for (const [operation, label] of [[source.enabled ? 'disable' : 'enable', source.enabled ? 'Disable' : 'Enable'], ['remove', 'Remove source (installed packages stay)']]) {
        const button = element('button', label, row);
        button.onclick = () => action(async () => {
          button.disabled = true;
          try { showRepositories(await json('/repositories/source', {action: operation, name: source.name})); }
          finally { button.disabled = false; }
        }, message => { notice.textContent = message; details.open = true; });
      }
      const refreshSource = element('button', 'Refresh', row); refreshSource.disabled = !source.enabled;
      refreshSource.onclick = () => action(() => startRepositoryRefresh(source.name), message => { notice.textContent = message; });
    }
    renderCategories();renderRepositoryPackages();
  }
  async function loadRepositories() { const data = await json('/repositories'); if (!detached) showRepositories(data); return data; }
  async function watchRepositoryOperation(operation) {
    repoBusy = true; repoRefresh.disabled = true; repoAdd.disabled = true;
    const generation = ++repoGeneration, selected=selectionGeneration;
    async function check() {
      if (detached || generation !== repoGeneration) return;
      try {
        const state = await json(`/repositories/operation/${operation.token}`);
        if (detached || generation !== repoGeneration) return;
        showRepositoryActivity([state]);
        for (const source of state.sources || []) sourceFeedback[source.name] = {message: source.state === 'success' ? 'Refresh succeeded.' : source.state === 'failed' ? 'Refresh failed. Saved packages remain available (stale). Refresh again after restoring the source.' : 'Refreshing…', log: source.log || ''};
        repoNotice.textContent = [state.progress, ...(state.sources || []).map(source => `${source.name}: ${source.state}\n${source.log || ''}`), state.error || ''].filter(Boolean).join('\n');
        if (!running) setStatus(state.kind === 'refresh' ? (state.state === 'running' ? 'Refreshing source listings…' : `Refresh finished: ${(state.sources || []).filter(s => s.state === 'success').length} succeeded, ${(state.sources || []).filter(s => s.state === 'failed').length} failed. See Sources.`) : state.error ? 'Package review failed. See Activity for recovery details.' : state.state === 'done' ? 'Package inspected. Review the installation changes.' : 'Retrieving and verifying the package archive…', state.kind === 'refresh' ? 'sources' : 'installation');
        if (state.kind === 'archive' && state.state === 'running') { setStep('Package'); setStatus(state.progress || 'Inspecting package…', 'installation'); }
        if (state.state === 'running') { repoTimer = setTimeout(check, 500); return; }
        repoBusy = false;
        await loadRepositories();
        repoRefresh.disabled = false; repoAdd.disabled = false;
        if(pendingChoice){const next=pendingChoice;pendingChoice=null;await chooseRepositoryPackage(next);return;}
        if(selected!==selectionGeneration)return;
        if (state.state === 'failed') { pageState.textContent='Could not prepare this workflow.';const retry=element('button','Retry preparation',packagePhase);retry.onclick=()=>chooseRepositoryPackage(lastChoice);packageError.textContent = state.error?.includes('sha256 mismatch') ? 'Archive verification failed. Refresh a corrected archive from the source and review again. Nothing was installed.' : `Package inspection failed: ${state.error || 'Unknown error'}. Correct the link/source or import a local .rrpkg, then retry. Nothing was installed.`; recovery.textContent = 'Review failed. Restore the source or choose another package, then request review again. Nothing was installed.'; log.textContent = state.error; }
        if (state.result && !running) {
          watching++; clearTimeout(poll); invalidate();
          showPlan(state.result, currentView === 'installation'); await refreshHistory();
        }
      } catch (error) {
        repoReport(`Repository operation status unavailable: ${error.message}. Reopen the panel to reconnect; do not repeat retrieval while its status is unknown.`);
        repoBusy = false; repoRefresh.disabled = false; repoAdd.disabled = false;
      }
    }
    await check();
  }
  async function startRepositoryRefresh(source = null) {
    repoRefresh.disabled = true; setStatus('Refreshing catalog listings…', 'sources');
    try { await watchRepositoryOperation(await json('/repositories/refresh', {source})); }
    catch (error) { repoRefresh.disabled = false; throw error; }
  }
  repoAdd.onclick = () => action(async () => {
    repoAdd.disabled = true;
    try {
      setStatus('Adding and refreshing catalog…', 'sources');
      repoReport('Adding and refreshing catalog…');
      const data = await json('/repositories/source', {action: 'add', name: repoName.value.trim(), uri: repoURI.value.trim()});
      showRepositories(data);
      if (data.sources.find(s => s.name === data.changed_source)?.error) { repoReport('Catalog refresh failed. Correct its location or restore the source, then Refresh. See its row below.'); return; }
      repoURI.value = ''; repoName.value = ''; addForm.open = false;
      repoReport('Catalog added. Packages are ready in Browse.'); switchView('browse');
    } finally { repoAdd.disabled = false; }
  }, repoReport);
  repoRefresh.onclick = () => action(() => startRepositoryRefresh(), repoReport);
  repoQuery.oninput=()=>{if(currentView!=='browse'){switchView('browse');repoQuery.focus();}renderRepositoryPackages();};
  repoSearch.onclick = () => action(async () => { await loadRepositories(); repoReport(repoData.warnings?.length ? 'Saved packages loaded. Some sources need attention; see Sources.' : 'Package list reloaded. No files were downloaded.'); }, repoReport);
  action(async () => {
    const data = await loadRepositories();
    const pending = data.operations?.find(operation => operation.state === 'running');
    if (pending) await watchRepositoryOperation(pending);
  }, repoReport);
  const uploadLabel = element('label', 'Choose a .rrpkg or Comfy workflow JSON file', browseView);
  const upload = element('input', undefined, browseView); upload.type = 'file'; upload.accept = '.rrpkg,.json,application/json'; upload.id = 'rr-import';
  uploadLabel.htmlFor = upload.id; upload.hidden = true; uploadLabel.hidden = true; importAction.onclick = () => upload.click();
  element('label', 'Additional model/input directories on the Comfy server (one per line)', advanced);
  const directories = element('textarea', undefined, advanced); directories.rows = 2; directories.id = 'rr-directories';
  element('label', 'Manual sources for missing files (FILENAME=URL or FILENAME=server path, one per line)', advanced);
  const sources = element('textarea', undefined, advanced); sources.rows = 2; sources.id = 'rr-sources';
  element('label', 'Choose a local file for a package dependency', advanced);
  const dependency = element('select', undefined, advanced); dependency.id = 'rr-local-dependency';
  element('label', 'Search model directories by part of the filename', advanced);
  const fileQuery = element('input', undefined, advanced); fileQuery.id = 'rr-file-query'; fileQuery.placeholder = 'For example: Qwen or LoRA';
  const fileSearch = element('button', 'Search local files', advanced); fileSearch.id = 'rr-file-search';
  const fileResults = element('select', undefined, advanced); fileResults.id = 'rr-file-results'; fileResults.size = 5;
  const fileNotice = element('p', 'Uses Comfy’s configured model directories plus the additional directories above.', advanced); fileNotice.id = 'rr-file-notice';
  const useFile = element('button', 'Use selected file', advanced); useFile.id = 'rr-use-file'; useFile.disabled = true;
  fileSearch.onclick = () => action(async () => {
    fileSearch.disabled = true; useFile.disabled = true; fileResults.replaceChildren();
    fileNotice.textContent = 'Searching local filenames…';
    try {
      const result = await json('/local-files', {query: fileQuery.value, directories: lines(directories.value)});
      for (const file of result.files) { const option = element('option', file.path, fileResults); option.value = file.path; }
      fileNotice.textContent = `${result.files.length} matches${result.truncated ? ' (search limit reached; narrow the query or directory)' : ''}. Select a file, then click Use selected file.`;
      useFile.disabled = !result.files.length || !dependency.value;
    } finally { fileSearch.disabled = false; }
  }, message => { fileNotice.textContent = message; });
  useFile.onclick = () => {
    if (!dependency.value || !fileResults.value || running) return;
    const entries = lines(sources.value).filter(line => line.split('=')[0] !== dependency.value);
    sources.value = [...entries, `${dependency.value}=${fileResults.value}`].join('\n');
    invalidate(); fileNotice.textContent = 'Local source selected. Review the installation plan again.';
  };
  const pythonLabel = element('label', undefined, advanced);
  const python = element('input', undefined, pythonLabel); python.type = 'checkbox'; python.id = 'rr-python';
  pythonLabel.append(document.createTextNode('Install custom-node Python requirements using the running Comfy Python'));
  const approvalActions = element('div', undefined, installationPanel); approvalActions.id = 'rr-approval-actions'; approvalActions.className = 'rr-approval'; approvalActions.hidden = true;approvalActions.tabIndex=-1;
  const review = element('button', 'Review changes again', approvalActions); review.id = 'rr-review'; review.disabled = true; advanced.append(review);
  const log = element('pre', 'Choose a package to inspect its dependencies.', technical); log.id = 'rr-plan';

  const install = element('button', 'Preparing…', pageActions); install.id = 'rr-install'; install.className = 'rr-primary'; install.disabled = true;
  element('label', 'Download limit (MB/s; 0 = unlimited). One file transfers at a time.', downloadControls);
  const rate = element('input', undefined, downloadControls); rate.id = 'rr-rate'; rate.type = 'number'; rate.min = 0; rate.max = 10000; rate.step = .5; rate.value = 5;
  const pause = element('button', 'Pause downloads', downloadControls); pause.id = 'rr-pause'; pause.disabled = true;
  const cancel = element('button', 'Cancel; keep partial files', downloadControls); cancel.id = 'rr-cancel'; cancel.disabled = true;
  element('p', 'Partial files are retained. Retrying reuses verified files. Pause/cancel apply to downloads, not pip or Git.', downloadControls);
  let downloadAction = 'run';
  const control = next => action(async () => {
    const generation = ++controlGeneration; controlPending = true;
    const value = Number(rate.value);
    if (next === 'pause') { setStatus('Pausing… Waiting for transfer acknowledgement.', 'activity'); transferInfo.textContent = 'Pausing… ' + transferInfo.textContent.split(' · ').filter(part => !/ETA|\/s|Speed/.test(part)).join(' · '); }
    try { await json(`/download-control/${session}`, {action: next, rate_mbps: value}); }
    finally { if (generation === controlGeneration) controlPending = false; }
    if (generation !== controlGeneration) return;
    downloadAction = next; pause.textContent = next === 'pause' ? 'Resume downloads' : 'Pause downloads';
    await refreshHistory();
    if (next === 'cancel') { pause.disabled = true; cancel.disabled = true; setStatus('Cancelling transfer; partial files will be kept.'); }
  });
  pause.onclick = () => control(downloadAction === 'pause' ? 'run' : 'pause');
  cancel.onclick = () => control('cancel');
  rate.onchange = () => { if (running) control(downloadAction); };
  const open = element('button', 'Open workflow', completion); open.id = 'rr-open'; open.className='rr-primary'; open.disabled = true;open.hidden=true;pageActions.append(open);
  const options = () => ({artifact_dirs: lines(directories.value), sources: lines(sources.value), python_dependencies: python.checked});
  const invalidate = () => {reviewIdentity=null;planReady=false;decisionOpen=false;approvalActions.hidden=true;install.disabled=true;install.textContent='Preparing…';open.disabled=true;};
  [directories, sources, python].forEach(input => input.addEventListener('input',()=>{reviewGeneration++;invalidate();}));
  function showReviewSummary(result) {
    summary.hidden=false;summary.replaceChildren();keepInstalled.hidden=true;openKept.hidden=true;
    const value=result.store_scope;
    if(value){
      let text=value.summary;
      if(value.downloads)text+=` Download ${value.downloads} file${value.downloads===1?'':'s'}, ${bytes(value.download_bytes)}${value.unknown_sizes?' known; '+value.unknown_sizes+' size unknown':''}.`;
      element('p',text,summary).className='rr-change-summary';
    }
    const existing=repoData?.installed?.find(i=>i.name===result.name && i.workflow_available);
    if(!result.can_install && existing){openKept.hidden=false;openKept.textContent='Open existing workflow';openKept.onclick=()=>action(()=>openInstalled(result.name));}
    recovery.textContent=result.can_install ? '' : (result.plan?.log || '').split('\n').filter(l=>/^(ERROR:|UNRESOLVED:|UNAVAILABLE)/.test(l)).at(-1)?.slice(0,240) || 'Resolve the missing requirement below, then prepare again.';
  }
  let decisionOpen=false, decisionChecks=[], resumeAction=null;
  function showDecisions() {
    approvalActions.replaceChildren();decisionChecks=[];decisionOpen=true;approvalActions.hidden=false;
    element('h3','Confirm these changes',approvalActions);
    for(const d of pageResult?.store_scope?.decisions || []) {
      const label=element('label',undefined,approvalActions),check=element('input',undefined,label);check.type='checkbox';check.setAttribute('data-decision',d.id);
      label.append(document.createTextNode(d.message));decisionChecks.push([d.id,check]);
    }
    const confirm=install;confirm.textContent='Confirm and install';confirm.disabled=true;
    for(const [,check] of decisionChecks)check.onchange=()=>{confirm.disabled=decisionChecks.some(([,c])=>!c.checked);};
    const cancelDecision=element('button','Keep unchanged',approvalActions);cancelDecision.onclick=()=>{decisionOpen=false;approvalActions.hidden=true;install.textContent='Install';install.disabled=!planReady;};
    approvalActions.focus();
  }
  const requirements = element('div',undefined,reviewPhase); requirements.id='rr-requirements';
  technical.append(advanced); // Questions precede optional administration.
  let reviewGeneration=0;
  async function rereview() {
    const generation=++reviewGeneration; invalidate();
    requirements.querySelectorAll('input, select, button').forEach(control=>control.disabled=true);
    try {const result=await json(`/review/${session}`,options());if(generation===reviewGeneration)showPlan(result);}
    finally {if(generation===reviewGeneration)requirements.querySelectorAll('input, select, button').forEach(control=>control.disabled=false);}
  }
  function requirementControls(result) {
    requirements.replaceChildren();

    const target=result.target_check;
    if(target) {
      const labels={available:'Required nodes available now',resolvable:'Missing nodes can be installed with approval',unknown:'Compatibility unknown — target inventory is incomplete',unavailable:'Target requirements need attention'};
      element('p',labels[target.state] || target.state,requirements).setAttribute('role','status');
      for(const n of target.nodes || []) {
        if(n.state==='unavailable') element('p',`${n.type} is unavailable on this target; this environment cannot install the dependency. No model downloads will start.`,requirements);
        if(n.state==='resolvable') element('p',`${n.type}: install ${n.repository.name} at ${n.repository.revision}`,requirements);
        if(n.state==='needs_source') {
          const area=element('div',undefined,requirements); area.className='rr-consequence';
          element('h4',`Identify the repository for ${n.type}`,area);
          const fields={};
          for(const [key,label] of [['repository','Repository name'],['source','Declared upstream Git URL or server path'],['revision','Exact 40-character Git revision']]) {
            const labelNode=element('label',label,area); const input=element('input',undefined,area); input.id='rr-node-'+n.type+'-'+key; labelNode.htmlFor=input.id; fields[key]=input;
          }
          const use=element('button','Use this repository',area);
          use.onclick=()=>action(async()=>{invalidate();showPlan(await json(`/resolve/${session}`,{kind:'node',name:n.type,...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value]))}));});
        }
      }
      for(const check of target.runtime_blockers || []) element('p',check.detail,requirements);
    }
    const overrides=new Map(lines(sources.value).map(s=>{const i=s.indexOf('=');return [s.slice(0,i),s.slice(i+1)];}));
    for(const artifact of result.manifest?.artifacts || []) {
      const planned=result.plan?.scope?.artifacts?.find(a=>a.filename===artifact.filename);
      if(planned?.operation==='reuse') continue;
      if(result.manifest.inputs?.some(i=>i.mode==='bundled' && i.filename===artifact.filename)) continue;
      const declared=artifact.sources || [];
      const area=element('div',undefined,requirements); area.className='rr-consequence';
      element('h4',artifact.filename,area);
      const selected=overrides.get(artifact.filename);
      element('p',`Size: ${bytes(artifact.size_bytes)}${artifact.sha256 ? ' · verify declared SHA-256' : ' · no declared checksum; identity is unverified'}`,area);

      if(declared.length>1) {
        const label=element('label','Download source',area),choose=element('select',undefined,area); choose.id='rr-source-'+artifact.filename;label.htmlFor=choose.id;
        for(const source of declared){const option=element('option',source.uri,choose);option.value=source.uri;}
        if(selected&&!declared.some(s=>s.uri===selected)){const option=element('option',selected,choose);option.value=selected;}
        choose.value=selected||declared[0].uri;
        choose.onchange=()=>action(async()=>{sources.value=[...lines(sources.value).filter(s=>s.slice(0,s.indexOf('='))!==artifact.filename),artifact.filename+'='+choose.value].join('\n');await rereview();});
      }
      const missing=!declared.length&&!selected;
      const control=missing?area:element('details',undefined,area);
      if(!missing) element('summary','Use another source or existing server file',control);
      if(missing) element('p','Choose an existing server file or provide the missing source. Other resolved requirements are kept.',control);
      const label=element('label','Source URL or path on the Comfy server',control);
      const input=element('input',undefined,control);input.id='rr-resolve-'+artifact.filename;label.htmlFor=input.id;input.value=selected||'';
      const use=element('button','Use source',control);use.onclick=()=>action(async()=>{invalidate();showPlan(await json(`/resolve/${session}`,{kind:'artifact',name:artifact.filename,source:input.value}));});
      const find=element('button','Choose existing server file',control),files=element('select',undefined,control);files.hidden=true;files.setAttribute('aria-label',artifact.filename+': existing server file');
      find.onclick=()=>action(async()=>{const found=await json('/local-files',{query:artifact.filename,directories:lines(directories.value)});files.replaceChildren();for(const file of found.files){const option=element('option',file.path,files);option.value=file.path;}files.hidden=false;selectFile.hidden=!found.files.length;if(!found.files.length)element('p','No matching configured server files. Enter a server path or known source above.',control);});
      files.onchange=()=>{input.value=files.value;};
      const selectFile=element('button','Use selected server file',control);selectFile.hidden=true;files.onchange=()=>{input.value=files.value;selectFile.hidden=false;};selectFile.onclick=()=>action(async()=>{if(!files.value)throw new Error('Search and select a server file first.');invalidate();showPlan(await json(`/resolve/${session}`,{kind:'artifact',name:artifact.filename,source:files.value}));});
      element('p','This selects files on the Comfy server. Browser uploads import the recipe only, not its original filesystem path.',control).className='rr-muted';
    }
  }
  function showPlan(result, navigate = true) {
    if (result.manifest) {
      dependency.replaceChildren();
      for (const artifact of result.manifest.artifacts) { const option = element('option', artifact.filename, dependency); option.value = artifact.filename; }
    }
    if(result.options){directories.value=(result.options.artifact_dirs || []).join('\n');sources.value=(result.options.sources || []).join('\n');python.checked=result.options.python_dependencies===true;}
    resumeAction=null;pageResult=result;decisionOpen=false;approvalActions.hidden=true;pageMetadata(repoChoice || lastChoice || {name:result.name,version:result.version,source:result.source},result.manifest,result.target_check,result.capabilities);pageState.textContent=environment.textContent;directOpen.hidden=true;open.hidden=true;install.hidden=false;install.textContent='Install';session = result.session; reviewIdentity=result.review_id; requirementControls(result); activeOperation.hidden=true; log.textContent = result.plan.log + '\nArchive SHA-256: ' + result.archive_sha256 + '\nFull reviewed changes:\n' + JSON.stringify(result.plan.scope, null, 2);
    planReady = !!result.can_install && !!result.store_scope; showReviewSummary(result); setStep('Review'); packagePhase.hidden = true; reviewPhase.hidden = false; completion.hidden = true; if (navigate) switchView('installation');
    const previewToken=session,previewEpoch=pageEpoch;
    (result.manifest?.inputs || []).forEach((i,index)=>{if(i.mode!=='bundled'||i.kind!=='image')return;request(`/example/${previewToken}/${index}`).then(r=>r.blob()).then(blob=>{if(detached||session!==previewToken||previewEpoch!==pageEpoch)return;const image=element('img',undefined,pageAbout);image.className='rr-example';image.alt='Bundled example: '+i.filename;const url=URL.createObjectURL(blob);previewURLs.push(url);image.src=url;}).catch(()=>{});});
    running = false;
    [upload, directories, sources, python, review, dependency, fileQuery, fileSearch, fileResults].forEach(x => x.disabled = false);
    pause.disabled = true; cancel.disabled = true; meter.hidden = true; open.disabled = true;
    planReady = !!result.can_install && !!result.store_scope; install.disabled = !planReady; approvalActions.hidden = true;
    if(!result.store_scope){install.textContent='Backend update needed';pageState.textContent='This RR backend is older than the store page. Restart this Comfy to load the new backend before installing. Existing workflows can still open.';}
    setStatus(result.can_install?'Ready to install.':'A requirement needs attention.','installation');
  }
  upload.onchange = () => action(async () => {
    if (!upload.files?.[0] || running) return;
    if (repoBusy) { setStatus('Wait for the package inspection to finish before importing another file.'); return; }
    beginInspection('Preparing imported workflow…');
    directories.value='';sources.value='';python.checked=false;
    const result = await json('/preview', upload.files[0], true);
    showPlan(result); review.disabled = false;
    await refreshHistory();
  });
  browseView.addEventListener('dragover',event=>event.preventDefault());
  browseView.addEventListener('drop',event=>{event.preventDefault();if(event.dataTransfer?.files?.length){upload.files=event.dataTransfer.files;upload.onchange();}});
  review.onclick = () => action(async () => { invalidate(); setStatus('Checking installation plan…'); showPlan(await json(`/review/${session}`, options())); });
  install.onclick=()=>action(async()=>{
    if(resumeAction){await resumeAction();return;}
    if(!planReady || running)return;
    if(decisionOpen){if(decisionChecks.every(([,c])=>c.checked))await submitInstall(decisionChecks.map(([id])=>id));return;}
    if(pageResult?.store_scope?.decisions?.length){showDecisions();return;}
    await submitInstall([]);
  });
  async function submitInstall(confirmations) {
    if(!planReady || running)return;
    install.disabled=true;decisionOpen=false;approvalActions.hidden=true;running=true;
    requirements.querySelectorAll('input,select,button').forEach(c=>c.disabled=true);
    const token=session;const identity=reviewIdentity;let result;
    try{result=await json(`/install/${token}`,{approved:true,review_id:identity,...(confirmations.length?{confirmations}:{})});}
    catch(error){running=false;install.disabled=false;requirements.querySelectorAll('input,select,button').forEach(c=>c.disabled=false);throw error;}
    session=token;

    if(result.approval_required || result.confirmation_required){showPlan(result);pageState.textContent='The changes were updated. Check them and click Install again.';await refreshHistory();return;}
    document.getElementById('rr-activity-complete')?.remove();document.getElementById('rr-activity-error')?.remove();
    setStep('Install');reviewPhase.hidden=false;switchView('installation');activeOperation.hidden=false;completion.hidden=true;
    running=true;install.textContent='Installing…';pageState.textContent='Installing…';
    [upload,directories,sources,python,review,install,exporter].forEach(x=>x.disabled=true);
    pause.disabled=false;cancel.disabled=false;downloadAction='run';meter.hidden=false;meter.removeAttribute('value');
    watchInstallation();
  }
  function watchInstallation() {
    resumeAction=null;activeOperation.hidden=false;install.hidden=false;open.hidden=true;install.textContent='Installing…';
    const token = session, generation = ++watching;
    jobFailures.delete(token);
    running = true;
    [upload, directories, sources, python, review, install, exporter, dependency, fileQuery, fileSearch, fileResults, useFile].forEach(x => x.disabled = true);
    requirements.querySelectorAll('input,select,button').forEach(c=>c.disabled=true);
    close.disabled = false; pause.disabled = false; cancel.disabled = false; downloadControls.hidden = false;
    meter.hidden = false;
    async function check() {
      if (detached || generation !== watching) return;
      try {
        const commandGeneration = controlGeneration;
        const result = await json(`/status/${token}`);
        if (detached || generation !== watching) return;
        if (controlPending || commandGeneration !== controlGeneration) { poll = setTimeout(check, 100); return; }
        if (result.progress) log.textContent = result.progress.split('\n').filter(line => !line.startsWith('RR_TRANSFER ')).join('\n');
        if (result.control) {
          downloadAction = result.control.action;
          rate.value = result.control.rate_mbps;
          pause.textContent = downloadAction === 'pause' ? 'Resume downloads' : 'Pause downloads';
        }
        const transfer = result.transfer;
        pageState.textContent=environment.textContent;install.textContent=transferLabel(result)+(transferLabel(result).endsWith('…')?'':'…');
        const label = transferLabel(result), waiting = ['Pausing…', 'Paused', 'Resuming…'].includes(label);
        const measurements = !waiting && (result.state === 'running' || result.worker_active);
        if (transfer) {
          const total = transfer.total_bytes;
          const percent = total > 0 ? Math.min(100, transfer.bytes / total * 100) : null;
          if (percent !== null) meter.value = percent; else meter.removeAttribute('value');
          const amount = `${bytes(transfer.bytes)}${total > 0 ? ` / ${bytes(total)} (${percent.toFixed(1)}%)` : ''}`;
          const speed = transfer.bytes_per_second ? `${(transfer.bytes_per_second / 1e6).toFixed(2)} MB/s` : '';
          const eta = transfer.eta_seconds != null ? ` — about ${Math.ceil(transfer.eta_seconds / 60)} min remaining` : '';
          setStatus(`${transfer.filename}\n${amount}${transfer.complete ? ' — file verified and saved' : ''}\n${waiting ? label : downloadAction === 'cancel' ? 'Cancellation requested' : transfer.state || 'transferring'} ${!waiting && downloadAction === 'run' ? speed + eta : ''}\nElapsed: ${result.elapsed_seconds}s. Keep Comfy running.`, 'activity');
        } else setStatus(`Installing — elapsed ${result.elapsed_seconds}s. Keep Comfy running.`, 'activity');
        transferInfo.textContent = transfer ? `${bytes(transfer.bytes)} / ${bytes(transfer.total_bytes)}${!measurements ? '' : ` · ${transfer.bytes_per_second ? bytes(transfer.bytes_per_second) + '/s' : 'Speed pending'} · ${transfer.eta_seconds != null ? 'ETA ' + Math.ceil(transfer.eta_seconds) + ' sec' : 'ETA pending'}`}` : 'Preparing files or recording the workflow…';
        if (result.state === 'running' || result.worker_active) {
          if (result.state === 'interrupted') setStatus(viewStatus.activity + '\nBackend restarted; the previous worker is still active. Monitoring it; duplicate execution is blocked.', 'activity');
          poll = setTimeout(check, 1000); await refreshHistory(); return;
        }
        meter.hidden = true; downloadControls.hidden = true;
        if (result.outcome === 'completed' || result.result?.exit_code === 0) { setStep('Complete'); packagePhase.hidden = true; reviewPhase.hidden = false; completion.hidden = false; }
        else if (['reviewed', 'needs_review'].includes(result.state)) {showPlan({...result,session:token,name:result.manifest?.name || 'Workflow',version:result.manifest?.version || '',can_install:result.plan?.exit_code===0});}
        pause.disabled = true; cancel.disabled = true;
        running = false; log.textContent = (result.result?.log || result.plan?.log || result.progress || '').split('\n').filter(line => !line.startsWith('RR_TRANSFER ')).join('\n'); open.disabled = !result.workflow_available;
        if (result.workflow_path) log.textContent += `\nWorkflow location: ${result.workflow_path}`;
        [upload, directories, sources, python, review, dependency, fileQuery, fileSearch, fileResults].forEach(x => x.disabled = false);
        planReady = ['reviewed', 'needs_review'].includes(result.state) && result.plan?.exit_code === 0 && !!result.store_scope;
        install.disabled = !planReady; approvalActions.hidden = true;
        close.disabled = false; exporter.disabled = false;
        if (!['reviewed', 'needs_review'].includes(result.state) && result.outcome !== 'completed' && result.result?.exit_code !== 0) recovery.textContent = '';
        setStatus(result.restart_required ? 'Restart Comfy to load the installed nodes, then reopen the workflow from Comfy’s workflow list. Installation details are below.' :
          result.state === 'interrupted' ? 'Interrupted. Resume this installation below; nothing restarts automatically.' :
          ['reviewed', 'needs_review'].includes(result.state) ? 'Review the current plan and approve before installing.' :
          result.result?.exit_code === 0 ? 'Installed. Open the workflow to use it.' : 'Installation needs attention. Resume below or inspect Details.', 'activity');
        if (!['reviewed', 'needs_review'].includes(result.state) && result.outcome !== 'completed' && result.result?.exit_code !== 0) jobFailures.set(token, installationFailure(result));
        else jobFailures.delete(token);
        if (result.outcome === 'completed' || result.result?.exit_code === 0) {
          document.getElementById('rr-activity-complete')?.remove();
          const ready = element('div', undefined, activeOperation); ready.id = 'rr-activity-complete'; element('p', 'Installation complete. Your workflow is ready to open.', ready);
          summary.hidden=true;install.hidden=true;open.hidden=false;pageState.textContent=result.restart_required?'Installed. Restart Comfy to load the new nodes before use.':'Installed';
          completion.hidden=true;transferInfo.textContent='Completed';activeOperation.hidden=true;

        }
        if(!['reviewed','needs_review'].includes(result.state) && result.outcome!=='completed' && result.result?.exit_code!==0) {
          document.getElementById('rr-inline-recovery')?.remove();
          const area=element('div',undefined,activeOperation);area.id='rr-inline-recovery';
          element('p',installationFailure(result),area);
          pageState.textContent='Installation needs attention';install.textContent='Resume installation';install.disabled=false;
          resumeAction=async()=>{install.disabled=true;try{const next=await json(`/resume/${token}`,{});area.remove();resumeAction=null;if(next.approval_required)showPlan(next);else watchInstallation();}catch(error){install.disabled=false;throw error;}};
        }
        renderStatus();await refreshHistory(); await loadRepositories();
      } catch (error) {
        if (error.status === 400) {
          running = false; close.disabled = false; exporter.disabled = false;
          setStatus(`Installation history could not be read: ${error.message}. Other history entries remain available.`, 'activity');
          return;
        }
        setStatus(`Connection interrupted: ${error.message}\nRR may still be installing. Retrying status…`, 'activity');
        poll = setTimeout(check, 2000);
      }
    }
    check();
  }
  open.onclick = () => action(async () => {
    const workflow = await json(`/workflow/${session}`);
    await loadInstalledGraph(workflow); dialog.close();
    setStatus('Workflow loaded into the Comfy editor. Check user inputs and node availability before running.');
  });
  const exportBack = element('button', 'Back to Browse', exportPanel); exportBack.onclick = () => switchView('browse');
  element('h3', 'Export current workflow', exportPanel);
  element('p', 'Export saves the graph and dependency fingerprints in a .rrpkg. Model weights stay outside the package. Fingerprinting reads available model files to calculate SHA-256 hashes; large files can take time. Selected example inputs are bundled.', exportPanel);
  const exportStatus = element('p', '', exportPanel); exportStatus.id = 'rr-export-status'; exportStatus.className = 'rr-status'; exportStatus.setAttribute('role', 'status');
  const exportActivity = element('div', undefined, activityView); exportActivity.id = 'rr-export-activity';
  const exportActivityStatus = element('p', '', exportActivity);
  const exportView = element('button', 'View export', exportActivity); exportView.hidden = true; exportView.onclick = () => switchView('export');
  const setExportStatus = text => { exportStatus.textContent = text; exportActivityStatus.textContent = text; exportView.hidden = false; if (!running && !repoBusy) setStatus(text.length > 180 ? 'Export needs attention. Open the export panel for details.' : text, 'export'); };
  element('label', 'Package name', exportPanel); const name = element('input', undefined, exportPanel); name.value = 'my-workflow'; name.id = 'rr-name';
  element('label', 'Package version', exportPanel); const version = element('input', undefined, exportPanel); version.value = '0.1.0'; version.id = 'rr-version';
  element('label', 'Required custom-node repositories (select only those used by this graph)', exportPanel);
  const nodes = element('select', undefined, exportPanel); nodes.multiple = true; nodes.size = 4; nodes.id = 'rr-nodes';
  const nodeNotice = element('p', 'Checking installed custom-node repositories…', exportPanel); nodeNotice.id = 'rr-node-notice';
  const unavailableNodes = element('ul', undefined, exportPanel); unavailableNodes.id = 'rr-unavailable-nodes';
  nodes.setAttribute('aria-describedby', 'rr-node-notice rr-unavailable-nodes');
  element('label', 'Input filenames to bundle as examples (one per line; leave blank for user-supplied inputs)', exportPanel);
  const inputs = element('textarea', undefined, exportPanel); inputs.rows = 2; inputs.id = 'rr-inputs';
  const discoveryDetails = element('details', undefined, exportPanel); element('summary', 'Help others find this workflow (optional)', discoveryDetails);
  const discoveryInputs = {};
  for (const [key,label] of Object.entries({title:'Human-readable title',summary:'Purpose / summary',tasks:'Task phrases (one per line)',tags:'Task tags (one per line)',required_inputs:'Required inputs (one per line)',examples:'Examples (one per line)',limitations:'Limitations (one per line)'})) {
    element('label',label,discoveryDetails); const input = element('textarea',undefined,discoveryDetails); input.rows = 2; input.id='rr-discovery-'+key; discoveryInputs[key]=input;
  }
  const exporter = element('button', 'Export .rrpkg', exportPanel); exporter.id = 'rr-export';
  const exportLog = element('pre', '', exportPanel); exportLog.id = 'rr-export-log'; exportLog.hidden = true;
  exporter.onclick = () => action(async () => {
    exporter.disabled = true;
    let result;
    try {
      exportLog.hidden = true; exportLog.textContent = '';
      setExportStatus('Reading workflow files and preparing your package…');
      const discovery = Object.fromEntries(Object.entries(discoveryInputs).filter(([,input]) => input.value.trim()).map(([key,input]) => [key, ['title','summary'].includes(key) ? input.value.trim() : lines(input.value)]));
      result = await json('/export', {discovery, workflow: app.graph.serialize(), name: name.value, version: version.value,
        custom_nodes: [...nodes.selectedOptions].filter(x => !x.disabled).map(x => x.value), bundle_inputs: lines(inputs.value)});
      close.disabled = false;
      while (true) {
        let state;
        try { state = await json(`/status/${result.session}`); }
        catch (error) {
          if (error.status === 400 || error.status === 403) throw error;
          setExportStatus(`Export connection interrupted: ${error.message}. Retrying status…`);
          await new Promise(resolve => setTimeout(resolve, 2000)); continue;
        }
        exportLog.textContent = state.progress || ''; exportLog.hidden = !state.progress;
        if (state.state === 'exported' || state.state === 'done') {
          if (state.result.exit_code) throw new Error(state.result.log);
          result.log = state.result.log; break;
        }
        setExportStatus(`Export running (${state.elapsed_seconds || 0}s). Inspecting dependencies and hashing available files…`);
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      const blob = await (await request(`/archive/${result.session}`)).blob();
      const url = URL.createObjectURL(blob); const link = element('a');
      link.href = url; link.download = `${name.value.replace(/[^A-Za-z0-9_.-]/g, '-') || 'workflow'}.rrpkg`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      exportLog.textContent = result.log; exportLog.hidden = false;
      setExportStatus('Package exported. Review unresolved dependency notices below before publishing.');
    } finally {
      exporter.disabled = false; close.disabled = false;
      if (result) await json(`/discard/${result.session}`, {});
    }
  }, setExportStatus);
  close.onclick = () => action(async () => {
    detached = true; watching++; repoGeneration++; clearTimeout(poll); clearTimeout(repoTimer); dialog.close(); dialog.remove();
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close.click();
  });
  dialog.addEventListener('cancel', event => { event.preventDefault(); close.click(); });
  for (const field of dialog.querySelectorAll('input, textarea, select')) {
    if (field.id && field.previousElementSibling?.tagName === 'LABEL') field.previousElementSibling.htmlFor = field.id;
  }
  installationPanel.append(pageAbout,technical);
  switchView('browse');document.body.append(dialog); dialog.showModal();
  action(async () => {
    const initialSelection = watching;
    const config = await json('/config');
    let eligible = 0;
    for (const node of config.custom_nodes) {
      const available = node.exportable === true;
      const label = available ? `${node.name} @ ${node.revision.slice(0, 12)}` : `${node.name} — Unavailable: ${node.unavailable_reason}`;
      const option = element('option', label, nodes); option.value = node.name; option.disabled = !available;
      if (available) eligible++;
      else element('li', `${node.name}: ${node.unavailable_reason}`, unavailableNodes);
    }
    nodeNotice.textContent = config.custom_nodes.length === 0 ? 'No installed custom-node repositories found.' :
      `${eligible} of ${config.custom_nodes.length} installed custom-node entries can be exported. Unavailable entries cannot be selected. Local files are left unchanged.`;
    pythonLabel.title = config.python;
    showHistory(config.installations || []);
    environment.textContent=config.environment?.name || 'This Comfy environment'; environment.title=config.environment?.root || '';
    const active = config.installations?.find(job => job.state === 'running' || job.worker_active) || config.installations?.find(job=>['interrupted','needs_review'].includes(job.state) || job.outcome==='cancelled');
    if (active && !session && watching === initialSelection) { switchView('installation'); setStatus('Reconnecting to installation…', 'installation'); await selectJob(active.session); }
  }, message => { nodeNotice.textContent = `Could not inspect custom-node repositories: ${message}`; });
}

function openStorefront() {
  const existing = document.getElementById('renderrepo-storefront');
  if (existing) { existing.showModal(); return; }
  const dialog = element('dialog'); dialog.id = 'renderrepo-storefront';
  dialog.style.cssText = 'width:98vw;height:96dvh;max-width:none;max-height:none;padding:0;border:1px solid #3a4b5d;border-radius:11px;background:#111820';
  const frame = element('iframe', undefined, dialog);
  frame.title = 'RenderRepo'; frame.src = api.apiURL('/renderrepo/storefront/index.html');
  frame.style.cssText = 'display:block;width:100%;height:100%;border:0';
  const close = () => { window.removeEventListener('message', receive); dialog.close(); dialog.remove(); };
  async function receive(event) {
    if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.type !== 'renderrepo:host') return;
    const {id, action} = event.data;
    try {
      let result = null;
      if (action === 'open') {
        // Same temporary-workflow handoff as the established package UI; never queue a run.
        await loadInstalledGraph(event.data.graph); dialog.close();
      } else if (action === 'graph') result = app.graph.serialize();
      else if (action === 'close') { close(); return; }
      else throw new Error('Unknown RenderRepo host action.');
      frame.contentWindow.postMessage({type:'renderrepo:reply',id,result}, location.origin);
    } catch (error) { frame.contentWindow.postMessage({type:'renderrepo:reply',id,error:error.message}, location.origin); }
  }
  window.addEventListener('message', receive);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  document.body.append(dialog); dialog.showModal();
}

app.registerExtension({
  name: 'RenderRepo.Packages',
  setup() {
    const button = element('button', 'RenderRepo', document.body); button.id = 'renderrepo-launcher';
    button.style.cssText = 'position:fixed;bottom:18px;left:76px;z-index:90;padding:10px 14px;border:1px solid #667;border-radius:6px;background:#303844;color:white;cursor:pointer;font:14px system-ui';
    button.title = 'Browse, install and share workflow packages'; button.onclick = openStorefront;
  }
});

