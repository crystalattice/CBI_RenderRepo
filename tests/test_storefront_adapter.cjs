const {test}=require('node:test');
const assert=require('node:assert/strict');
const RR=require('../renderrepo/storefront/adapter.js');
test('unknown capability evidence stays unknown and identity includes digest/source/version',()=>{
 const a=RR.summary({name:'x',version:'1',repository:'one',sha256:'a'});
 assert.equal(a.images.required,null);assert.equal(a.output,'Output unknown');assert.deepEqual(a.actions,[]);
 assert.notEqual(a.id,RR.summary({name:'x',version:'1',repository:'two',sha256:'a'}).id);
 assert.notEqual(a.id,RR.summary({name:'x',version:'1',repository:'one',sha256:'b'}).id);
 const c=RR.summary({name:'x',capabilities:{actions:['edit-image'],outputs:['image'],input_counts:{image:{min:0,max:null}}}});
 assert.equal(c.images.required,null);assert.equal(c.output,'image');assert.deepEqual(c.actions,['edit']);
});
test('pause/cancel are requests until backend transfer acknowledgment; complete requires success',()=>{
 assert.equal(RR.operationLabel({state:'running',control:{action:'pause'}}),'Pausing…');
 assert.equal(RR.operationLabel({state:'running',control:{action:'pause'},transfer:{state:'paused'}}),'Paused');
 assert.equal(RR.operationLabel({state:'running',control:{action:'run'},transfer:{state:'paused'}}),'Resuming…');
 assert.equal(RR.operationLabel({state:'running',control:{action:'cancel'}}),'Cancelling…');
 assert.notEqual(RR.operationLabel({state:'done',result:{exit_code:2}}),'Complete');
 assert.equal(RR.operationLabel({state:'done',result:{exit_code:0}}),'Complete');
});
test('incomplete review cannot authorize an installation',()=>{
 assert.throws(()=>RR.validateReview({session:'x',review_id:'id'}),/Incomplete/);
});
test('adapter sends exact existing approval/control contracts and upload filename header',async()=>{
 const calls=[];global.fetch=async(url,options)=>{calls.push({url,...options});return {ok:true,json:async()=>({})};};
 await RR.json('/install/token',{approved:true,review_id:'scope-hash',confirmations:['code']});
 assert.deepEqual(JSON.parse(calls[0].body),{approved:true,review_id:'scope-hash',confirmations:['code']});
 assert.equal(calls[0].headers['X-RenderRepo-Request'],'1');
 await RR.json('/download-control/token',{action:'pause',rate_mbps:2});
 assert.deepEqual(JSON.parse(calls[1].body),{action:'pause',rate_mbps:2});
 const file={name:'workflow (1).json'};await RR.json('/preview',file,true);
 assert.equal(calls[2].headers['X-RenderRepo-Filename'],encodeURIComponent(file.name));assert.equal(calls[2].body,file);
});
test('detail fields and nodes retain graph facts without requiring an active import review',()=>{
 const w=RR.summary({name:'graph',capabilities:{nodes:['CLIPTextEncode','SaveImage'],
  prompts:[{label:'Positive prompt'}],input_counts:{image:{min:0,max:0}},
  controls:[{label:'Output width',value:1024},{label:'Steps',value:8}],
  output_details:[{label:'Image saved through SaveImage (prefix: demo)'}],
  processing:[{label:'Text encoding'},{label:'Image saving'}]}});
 assert.deepEqual(w.provide,['Positive prompt','No input image']);
 assert.deepEqual(w.controls,['Output width: 1024','Steps: 8']);
 assert.match(w.output,/prefix: demo/);assert.match(w.automatic,/Text encoding/);
 assert.deepEqual(RR.nodeEvidence(w,null,null),[
  {type:'CLIPTextEncode',state:'unknown'},{type:'SaveImage',state:'unknown'}]);
 const evidence=RR.nodeEvidence(w,{nodes:[{type:'SaveImage',state:'unknown'}]},
  {checks:[{kind:'node',detail:'CLIPTextEncode',state:'ready'},{kind:'node',detail:'SaveImage',state:'ready'}]});
 assert.ok(evidence.every(n=>n.state==='available'));
});
