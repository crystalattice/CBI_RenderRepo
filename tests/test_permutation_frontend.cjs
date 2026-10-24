const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.value='';}
  append(...els){this.children.push(...els);}
  replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}
}
const all=e=>[e,...e.children.flatMap(all)];
async function tick(){for(let i=0;i<30;i++)await Promise.resolve();}
function harness(){
  const nodes=new Map(['h1','#summary','#state','#group','#filter','#results','#error','#pause','#stop'].map(k=>[k,new Element('div')]));
  nodes.get('#group').value='configuration';nodes.get('#filter').value='all';
  const rows=[['one','keeper'],['two','unreviewed'],['three','toss']].map(([id,rating],i)=>({id,state:'completed',configuration:'cfg'+i,settings:{seed:42},outputs:[{url:'/image/'+id+'/output.png'}],review:{rating,notes:''}}));
  rows.push({id:'four',state:'failed',configuration:'cfg3',settings:{seed:42},review:{rating:'unreviewed',notes:''}});
  const data={name:'<script>unsafe</script>',token:'opaque',control:{action:'pause'},execution_state:'pause_requested',summary:{planned:4,completed:3,execution_failures:1,review_coverage:2/3},rows};
  const requests=[],timers=[];
  const document={querySelector:k=>nodes.get(k),createElement:tag=>new Element(tag),activeElement:null};
  const fetch=async(url,options)=>{requests.push({url,options});if(options){const body=JSON.parse(options.body);if(url==='/review'){const r=rows.find(r=>r.id===body.job);r.review={rating:body.rating,notes:body.notes};}return {ok:true};}return {ok:true,json:async()=>JSON.parse(JSON.stringify(data))};};
  vm.runInNewContext(fs.readFileSync('renderrepo/permutation/review.js','utf8'),{document,fetch,setInterval:fn=>timers.push(fn)});
  return {nodes,document,requests,timers,data,els:()=>all(nodes.get('#results'))};
}
test('comparison groups exact configurations or shared seeds and retains settings',async()=>{
  const h=harness();await tick();assert.equal(h.els().filter(x=>x.tagName==='H2').length,4);
  assert.ok(h.els().some(x=>x.tagName==='PRE'&&x.textContent.includes('"seed": 42')));
  assert.equal(h.nodes.get('h1').textContent,'<script>unsafe</script>'); // text, never injected HTML
  h.nodes.get('#group').value='seed';h.nodes.get('#group').onchange();
  assert.equal(h.els().filter(x=>x.tagName==='H2').length,1);
});
test('unreviewed outputs remain separate from Toss and execution failures',async()=>{
  const h=harness();await tick();h.nodes.get('#filter').value='unreviewed';h.nodes.get('#filter').onchange();
  assert.equal(h.els().filter(x=>x.tagName==='ARTICLE').length,1);
  assert.ok(h.els().some(x=>x.tagName==='H3'&&x.textContent.includes('two')));
  h.nodes.get('#filter').value='toss';h.nodes.get('#filter').onchange();
  assert.equal(h.els().filter(x=>x.tagName==='ARTICLE').length,1);
  assert.ok(h.els().some(x=>x.tagName==='H3'&&x.textContent.includes('three')));
});
test('one-click ratings and bounded notes persist through authenticated local request',async()=>{
  const h=harness();await tick();h.nodes.get('#filter').value='unreviewed';h.nodes.get('#filter').onchange();
  const notes=h.els().find(x=>x.tagName==='TEXTAREA');assert.equal(notes.maxLength,1000);notes.value='needs adjustment';
  await h.els().find(x=>x.tagName==='BUTTON'&&x.textContent==='Modify').onclick();await tick();
  const request=h.requests.find(x=>x.url==='/review');assert.deepEqual(JSON.parse(request.options.body),{job:'two',rating:'modify',notes:'needs adjustment'});
  assert.equal(request.options.headers['X-RR-Review'],'opaque');
});
test('pause requested is explicit, controls do not run or submit renders',async()=>{
  const h=harness();await tick();assert.match(h.nodes.get('#state').textContent,/pause_requested/);
  await h.nodes.get('#stop').onclick();
  assert.deepEqual(JSON.parse(h.requests.find(x=>x.url==='/control').options.body),{action:'stop'});
  assert.ok(h.requests.every(x=>['/data','/control'].includes(x.url)));
});
test('polling does not discard notes during editing',async()=>{
  const h=harness();await tick();h.document.activeElement={tagName:'TEXTAREA'};
  const count=h.requests.length;await h.timers[0]();assert.equal(h.requests.length,count);
});

test('unsaved notes survive polling after the editor loses focus',async()=>{
  const h=harness();await tick();const notes=h.els().find(x=>x.tagName==='TEXTAREA');
  notes.value='draft note';notes.oninput();h.document.activeElement=null;await h.timers[0]();
  assert.equal(h.els().find(x=>x.tagName==='TEXTAREA').value,'draft note');
});
