'use strict';
let snapshot;
const drafts = new Map();
const element = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
async function post(path, body) {
    const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-RR-Review':snapshot.token}, body:JSON.stringify(body)});
    if (!response.ok) throw new Error('Could not save; reload and check the record.');
}
function render() {
    document.querySelector('h1').textContent = snapshot.name;
    document.querySelector('#summary').textContent = JSON.stringify(snapshot.summary, null, 2);
    document.querySelector('#state').textContent = `${snapshot.summary.completed}/${snapshot.summary.planned} completed; ${snapshot.summary.execution_failures} failed. Execution: ${snapshot.execution_state}`;
    const group = document.querySelector('#group').value;
    const filter = document.querySelector('#filter').value;
    const root = document.querySelector('#results'); root.replaceChildren();
    const buckets = new Map();
    for (const row of snapshot.rows) {
        if (filter !== 'all' && (row.state !== 'completed' || row.review.rating !== filter)) continue;
        const key = group === 'seed' ? String(row.settings.seed) : row.configuration;
        if (!buckets.has(key)) buckets.set(key, []); buckets.get(key).push(row);
    }
    for (const [key, rows] of buckets) {
        root.append(element('h2', `${group}: ${key}`));
        for (const row of rows) {
            const card = element('article'); card.append(element('h3', `${row.id.slice(0,12)} — ${row.state} — ${row.review.rating}`));
            for (const output of row.outputs || []) { const img = element('img'); img.src = output.url; img.alt = `Result ${row.id}`; card.append(img); }
            const details = element('details'); details.append(element('summary','Exact settings and provenance'), element('pre', JSON.stringify(row,null,2))); card.append(details);
            if (row.state === 'completed') {
                const notes = element('textarea'); notes.maxLength = 1000; notes.value = drafts.get(row.id) ?? row.review.notes; notes.oninput = () => drafts.set(row.id, notes.value); notes.setAttribute('aria-label', 'Review notes'); card.append(notes);
                const save = async rating => { try { await post('/review',{job:row.id,rating,notes:notes.value}); drafts.delete(row.id); await refresh(); } catch (e) { document.querySelector('#error').textContent=e.message; } };
                for (const rating of ['keeper','modify','toss','unreviewed']) { const b = element('button',rating[0].toUpperCase()+rating.slice(1)); b.setAttribute('aria-pressed',String(row.review.rating === rating)); b.onclick=()=>save(rating); card.append(b); }
                const saveNotes = element('button','Save notes'); saveNotes.onclick=()=>save(row.review.rating); card.append(saveNotes);
            }
            root.append(card);
        }
    }
}
async function refresh() { const response=await fetch('/data'); if (!response.ok) throw new Error('Unable to read experiment'); snapshot=await response.json(); render(); }
document.querySelector('#group').onchange=render; document.querySelector('#filter').onchange=render;
for (const action of ['pause','stop']) document.querySelector('#'+action).onclick=async()=>{try{await post('/control',{action}); await refresh();}catch(e){document.querySelector('#error').textContent=e.message;}};
refresh().catch(e=>document.querySelector('#error').textContent=e.message);
// Poll only status text while a notes field is being edited; never discard unsaved notes.
setInterval(async()=>{ if(document.activeElement?.tagName==='TEXTAREA') return; try{await refresh();}catch(e){document.querySelector('#error').textContent=e.message;} },5000);
