// Exercise the real analyzer -> adapter -> detail renderer with unchanged JSON.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const RR=require('../renderrepo/storefront/adapter.js');
const root=path.resolve(__dirname,'..');
const fixture=fs.readFileSync(path.join(__dirname,'fixtures/image_z_image_turbo.json'));
const app=fs.readFileSync(path.join(root,'renderrepo/storefront/app.js'),'utf8');
const renderer=app.slice(app.indexOf('function renderWorkflowPrompts('),app.indexOf('function renderDetail('));
function render(graph){
 const capabilities=JSON.parse(execFileSync('python',['-c','import json,sys;from renderrepo.capabilities import analyze;print(json.dumps(analyze(json.load(sys.stdin))))'],{cwd:root,input:JSON.stringify(graph),encoding:'utf8',timeout:10000}));
 const output=[];
 const node=(tag,text,parent,attrs={})=>{const item={tag,text,attrs};output.push(item);return item;};
 vm.runInNewContext(renderer+';renderWorkflowPrompts(w,{});',{RR,node,w:RR.summary({name:'fixture',capabilities})});
 return {output,capabilities};
}
test('detail displays verbatim outer prompt including punctuation and whitespace, not conflicting inner default',()=>{
 const graph=JSON.parse(fixture),outer=graph.nodes.find(n=>graph.definitions.subgraphs.some(s=>s.id===n.type)).widgets_values_named.text;
 let result=render(graph);
 assert.equal(result.output.find(n=>n.attrs.class==='workflow-prompt-value').text,outer);
 const inner=graph.definitions.subgraphs[0].nodes.find(n=>n.type==='CLIPTextEncode');
 inner.widgets_values=['A different inner prompt.'];inner.widgets_values_named.text='A different inner prompt.';
 result=render(graph);
 assert.equal(result.output.find(n=>n.attrs.class==='workflow-prompt-value').text,outer);
 assert.ok(!result.output.some(n=>n.text==='A different inner prompt.'));
 assert.equal(result.capabilities.prompts[0].role,'positive');
 assert.ok(RR.summary({name:'fixture',capabilities:result.capabilities}).provide.includes('Positive prompt'));
 assert.equal(result.output.find(n=>n.tag==='h2').text,'Current workflow prompt');
 assert.equal(fs.readFileSync(path.join(__dirname,'fixtures/image_z_image_turbo.json')).compare(fixture),0);
});
test('detail explicitly reports absent embedded prompt values',()=>{
 const graph=JSON.parse(fixture);graph.nodes.find(n=>graph.definitions.subgraphs.some(s=>s.id===n.type)).widgets_values_named.text='';
 const result=render(graph);
 assert.ok(result.output.some(n=>n.text==='No prompt value is embedded.'));
 assert.ok(!result.output.some(n=>n.attrs.class==='workflow-prompt-value'));
 assert.ok(render({nodes:[{id:1,type:'SaveImage'}],links:[]}).output.some(n=>n.text==='No prompt value is embedded.'));
});
