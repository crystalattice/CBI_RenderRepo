const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const code=fs.readFileSync('renderrepo/storefront/app.js','utf8');const renderer=code.slice(code.indexOf('const capsuleExports='),code.indexOf('function renderInstalled()'));
test('import review opens original without install and saves/inspects the original-byte capsule',async()=>{
 const output=[],requests=[],opened=[];const result={name:'capsule-1.rrpkg',byte_identical:true,source_sha256:'a'.repeat(64),workflow_sha256:'a'.repeat(64),archive_sha256:'b'.repeat(64),original_json:'{\r\n  "nodes": []\r\n}\r\n',manifest:{name:'new-capsule',version:'1'},contents:[{path:'workflow.json',size_bytes:25}],capsule:{format:'s4-capsule',profile:'renderrepo',schema_version:1,sha256:'c'.repeat(64),payload_sha256:'d'.repeat(64)}};
 const node=(tag,text,parent,attrs={})=>{const n={tag,text,attrs,value:''};output.push(n);return n;};
 const button=(text,parent,fn)=>{const n=node('button',text,parent);n.click=fn;return n;};
 const context={node,button,reviews:new Map([['w',{session:'token',imported_workflow:{}}]]),RR:{json:async(p,v)=>{requests.push([p,v]);return p.startsWith('/original/')?{nodes:[]}:result;}},hostRequest:async(a,v)=>opened.push([a,v]),act:fn=>fn(),render:()=>{},$:()=>null};vm.createContext(context);vm.runInContext(renderer+';renderOriginalCapsule({id:"w",packageName:"imported"},{});',context);
 await output.find(n=>n.text==='Open original in ComfyUI').click();assert.equal(requests[0][0],'/original/token');assert.equal(opened[0][0],'open');assert.ok(!requests.some(([p])=>p.includes('install')));
 for(const n of output.filter(n=>n.attrs['data-capsule-field']))n.value=({'name':'new-capsule','version':'1','repository':'https://github.com/Comfy-Org/workflow_templates','revision':'f'.repeat(40),'path':'templates/base.json'})[n.attrs['data-capsule-field']];
 await output.find(n=>n.text==='Save original as RR capsule').click();assert.equal(requests[1][0],'/capsule/token');assert.equal(requests[1][1].name,'new-capsule');assert.equal(requests[1][1].revision,'f'.repeat(40));
 vm.runInContext('renderOriginalCapsule({id:"w",packageName:"imported"},{});',context);
 assert.ok(output.some(n=>n.text==='Byte-for-byte identical workflow payload'));assert.equal(output.find(n=>n.attrs.class==='capsule-source').text,result.original_json);assert.ok(output.some(n=>n.text==='Capsule workflow SHA-256: '+result.source_sha256));assert.ok(output.some(n=>n.text==='Original upstream JSON'));assert.ok(output.some(n=>n.text==='Capsule archive contents'));
 assert.ok(output.some(n=>typeof n.text==='string'&&n.text.includes('does not authenticate')));
});

test('late initial source refresh preserves a selected raw JSON file',async()=>{
 const refresh=code.slice(code.indexOf('function renderRefreshed(){'),code.indexOf('function isSameInstalled('));let renders=0;const input={files:[{name:'upstream.json'}]};
 const sandbox={RR:{json:async p=>p==='/config'?{installations:[]}:{packages:[],installed:[]},summary:x=>x},configuration:null,repositories:null,history:[],rows:[],reviews:new Map(),operations:new Map(),state:{destination:'manage',manage:'import'},$:()=>input,render:()=>renders++};vm.createContext(sandbox);
 await vm.runInContext(refresh+';refresh();',sandbox);assert.equal(renders,0);assert.equal(input.files[0].name,'upstream.json');assert.ok(sandbox.configuration);
 let initial; sandbox.act=fn=>{initial=fn();};vm.runInContext(code.slice(code.lastIndexOf('render();act(async()=>')),sandbox);await initial;assert.equal(renders,1); // Initial paint only; neither post-refresh paint clears the selected file.
 input.files=[];await vm.runInContext('refresh();',sandbox);assert.equal(renders,2);
});

test('raw import picker waits until initial sources and history have loaded',()=>{
 const render=code.slice(code.indexOf('function renderImport('),code.indexOf('async function restore('));const out=[];const node=(tag,text,parent,attrs={})=>{const e={tag,text,attrs,disabled:false};out.push(e);return e;};const ctx={initialReady:false,importType:'json',configuration:null,node,button:(text,p,fn,attrs)=>node('button',text,p,attrs),manageCard:()=>({}),Set};vm.createContext(ctx);vm.runInContext(render+';renderImport({});',ctx);assert.equal(out.find(e=>e.attrs.id==='import-file').disabled,true);assert.equal(out.find(e=>e.attrs.id==='prepare-import').disabled,true);ctx.initialReady=true;out.length=0;vm.runInContext('renderImport({});',ctx);assert.equal(out.find(e=>e.attrs.id==='import-file').disabled,false);assert.equal(out.find(e=>e.attrs.id==='prepare-import').disabled,false);
});
