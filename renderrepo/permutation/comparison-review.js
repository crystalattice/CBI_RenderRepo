'use strict';
const cards = [...document.querySelectorAll('[data-review-controls]')].map(controls => ({
  controls, article: controls.closest('[data-job]'), status: controls.querySelector('[data-save-status]'),
  notes: controls.querySelector('textarea'), buttons: [...controls.querySelectorAll('button')]
}));
const busy = (card, value) => card.buttons.forEach(button => { button.disabled = value; });
async function data() {
  const response = await fetch('/data', {cache:'no-store'});
  if (!response.ok) throw new Error('Cannot load reviews. Open the live comparison URL using 127.0.0.1, then reload.');
  return response.json();
}
function candidate(snapshot, card) {
  const row = snapshot.candidates.find(c => c.source_experiment === card.article.dataset.experiment && c.record.id === card.article.dataset.job);
  if (!row || !row.editable) throw new Error('This source candidate is unavailable or read-only. Reload the comparison.');
  return row.record.review;
}
function display(card, review) {
  card.article.querySelector('[data-current-rating]').textContent = review.rating;
  card.article.querySelector('[data-current-notes]').textContent = review.notes;
  for (const button of card.buttons) if (button.dataset.rating) button.setAttribute('aria-pressed', String(button.dataset.rating === review.rating));
}
async function save(card, rating) {
  busy(card, true); card.status.textContent = 'Saving…';
  let saved = false;
  try {
    const snapshot = await data();
    const current = candidate(snapshot, card);
    const desired = rating || current.rating;
    const notes = card.notes.value;
    const response = await fetch('/review', {method:'POST', headers:{'Content-Type':'application/json','X-RR-Review':snapshot.token},
      body:JSON.stringify({experiment:card.article.dataset.experiment,job:card.article.dataset.job,rating:desired,notes})});
    const result = await response.json(); saved = result.saved === true;
    if (!response.ok) throw new Error(result.error || 'Save failed. Reload the comparison and check source-directory permissions.');
    const persisted = candidate(await data(), card);
    if (persisted.rating !== desired || persisted.notes !== notes) throw new Error('Saved value changed before refresh. Reload to inspect the persisted review.');
    display(card, persisted);
    card.status.textContent = 'Saved. Rating and notes verified; JSON/CSV exports refreshed.';
  } catch (error) {
    card.status.textContent = (saved ? 'Rating was saved. ' : 'Not saved. ') + error.message;
  } finally { busy(card, false); }
}
for (const card of cards) {
  for (const button of card.buttons) button.onclick = () => save(card, button.dataset.rating);
}
async function initialize() {
  try {
    const snapshot = await data();
    for (const card of cards) {
      const review = candidate(snapshot, card); display(card, review); card.notes.value = review.notes;
      card.status.textContent = 'Ready to save.'; busy(card, false);
    }
  } catch (error) {
    for (const card of cards) card.status.textContent = error.message;
  }
}
initialize();
