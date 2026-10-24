const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const script=fs.readFileSync('renderrepo/permutation/comparison-review.js','utf8');
async function tick(){for(let i=0;i<40;i++)await Promise.resolve();}
function dom(candidates){
  const cards=candidates.filter(c=>c.editable).map(c=>{
    const status={},notes={value:c.record.review.notes},rating={},shownNotes={};
    const buttons=['keeper','modify','toss','unreviewed',undefined].map(r=>({dataset:r?{rating:r}:{},disabled:true,setAttribute(k,v){this[k]=v;}}));
    const article={dataset:{experiment:c.source_experiment,job:c.record.id},querySelector:k=>k==='[data-current-rating]'?rating:shownNotes};
    const controls={closest:()=>article,querySelector:k=>k==='textarea'?notes:status,querySelectorAll:()=>buttons};
    return {controls,article,status,notes,rating,shownNotes,buttons};
  });
  return {cards,document:{querySelectorAll:()=>cards.map(c=>c.controls)}};
}
function harness(options={}){
  const candidates=[0,1].map(i=>({source_experiment:'experiment',editable:true,record:{id:'job'+i,review:{rating:'unreviewed',notes:''}}}));
  candidates.push({editable:false,source_experiment:'baseline',record:{id:'old',review:{rating:'toss',notes:'preserve'}}});
  const ui=dom(candidates),requests=[];
  const fetch=async(url,config)=>{
    requests.push({url,config});
    if(!config||!config.method)return {ok:true,json:async()=>({token:'fresh-session',candidates:JSON.parse(JSON.stringify(candidates))})};
    const body=JSON.parse(config.body);
    if(options.failure)return {ok:false,json:async()=>({error:'Source directory is read-only; fix permissions.',saved:!!options.saved})};
    candidates.find(c=>c.record.id===body.job).record.review={rating:body.rating,notes:body.notes};
    return {ok:true,json:async()=>({saved:true})};
  };
  vm.runInNewContext(script,{document:ui.document,fetch});
  return {...ui,requests,candidates};
}
test('click authenticates, persists, refreshes display, and shows success beside each candidate',async()=>{
  const h=harness();await tick();
  assert.equal(h.cards.length,2);
  for(const card of h.cards){
    assert.ok(card.buttons.every(b=>!b.disabled));card.notes.value='change shoes\n✓';
    await card.buttons[1].onclick();
    assert.equal(card.rating.textContent,'modify');assert.equal(card.shownNotes.textContent,card.notes.value);
    assert.match(card.status.textContent,/Saved.*verified/);
  }
  const posts=h.requests.filter(r=>r.url==='/review');assert.equal(posts.length,2);
  for(const p of posts){assert.equal(p.config.headers['X-RR-Review'],'fresh-session');assert.equal(JSON.parse(p.config.body).experiment,'experiment');}
  assert.equal(h.candidates[2].record.review.rating,'toss');
});
test('save notes retains current rating; save errors stay beside controls and preserve draft',async()=>{
  const h=harness();await tick();const c=h.cards[0];await c.buttons[0].onclick();c.notes.value='notes only';await c.buttons[4].onclick();
  assert.equal(c.rating.textContent,'keeper');assert.equal(c.shownNotes.textContent,'notes only');
  const failed=harness({failure:true});await tick();const card=failed.cards[0];card.notes.value='draft';await card.buttons[2].onclick();
  assert.match(card.status.textContent,/Not saved.*permissions/);assert.equal(card.notes.value,'draft');assert.ok(card.buttons.every(b=>!b.disabled));
  const partial=harness({failure:true,saved:true});await tick();await partial.cards[0].buttons[0].onclick();assert.match(partial.cards[0].status.textContent,/Rating was saved/);
});
test('actual click-to-HTTP-to-persisted-display roundtrip on disposable records',{skip:!process.env.RR_COMPARISON_URL},async()=>{
  const base=process.env.RR_COMPARISON_URL;
  const fetchLocal=(path,options={})=>fetch(base+path,{...options,headers:{...options.headers,...(options.method?{Origin:base}:{})}});
  const snapshot=await (await fetchLocal('/data')).json();
  const ui=dom(snapshot.candidates);
  vm.runInNewContext(script,{document:ui.document,fetch:fetchLocal});
  for(let i=0;i<100&&ui.cards.some(c=>c.buttons[0].disabled);i++)await new Promise(r=>setTimeout(r,10));
  for(const [index,card] of ui.cards.entries()){
    for(const rating of ['keeper','modify','toss']){
      card.notes.value=`${index}: ${rating}\n=literal ✓`;
      await card.buttons.find(b=>b.dataset.rating===rating).onclick();
      assert.match(card.status.textContent,/Saved/);assert.equal(card.rating.textContent,rating);
    }
    card.notes.value=`final notes ${index}`;await card.buttons[4].onclick();assert.equal(card.rating.textContent,'toss');
  }
  const reopened=dom((await (await fetchLocal('/data')).json()).candidates);
  vm.runInNewContext(script,{document:reopened.document,fetch:fetchLocal});
  for(let i=0;i<100&&reopened.cards.some(c=>c.buttons[0].disabled);i++)await new Promise(r=>setTimeout(r,10));
  reopened.cards.forEach((c,i)=>{assert.equal(c.rating.textContent,'toss');assert.equal(c.notes.value,`final notes ${i}`);});
});
