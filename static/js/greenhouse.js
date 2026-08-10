import uiModule from './ui.js';
import { registerMenuDismiss } from './escMenuStack.js';

const ENDPOINT = '/api/greenhouse/v1/memories?limit=500';
const state = {
  memories: null,
  areas: [],
  selected: null,
  detail: null,
  query: '',
  loading: false,
  error: null,
  captureIds: new Map(),
  navDismiss: null,
};

const el = (id) => document.getElementById(id);
const esc = (value) => uiModule.esc(String(value ?? ''));
const labelsOf = (memory) => memory.labels || {};
const areaKey = (memory) => memory.area_key ?? labelsOf(memory).area_key ?? null;
const areaLabel = (memory) => {
  const labels = labelsOf(memory);
  return labels.area || labels.area_name || areaKey(memory) || '';
};
const contentOf = (memory) => memory.content ?? memory.text ?? memory.presentation ?? '';

function setNavigationDismiss() {
  if (state.navDismiss) state.navDismiss();
  state.navDismiss = null;
  if (state.detail || state.selected != null) {
    state.navDismiss = registerMenuDismiss(() => {
      state.navDismiss = null;
      if (state.detail) state.detail = null;
      else state.selected = null;
      renderCurrent();
    });
  }
}

function labelsFor(memory) {
  const labels = labelsOf(memory);
  return [labels.kind, labels.authority, labels.review_state].filter(Boolean).map(esc).join(' · ');
}

function renderMemory(memory, previousLabels) {
  const metadata = labelsFor(memory);
  const repeated = metadata && metadata === previousLabels;
  const id = memory.id ?? memory.memory_id ?? '';
  return `<article class="greenhouse-memory" data-greenhouse-memory="${esc(id)}" tabindex="0" role="button"><div class="greenhouse-memory-content">${esc(contentOf(memory))}</div>${metadata && !repeated ? `<div class="greenhouse-memory-labels" style="font-size:0.78em;opacity:0.62;">${metadata}</div>` : ''}</article>`;
}

function bindMemoryRows() {
  document.querySelectorAll('[data-greenhouse-memory]').forEach((row) => {
    const open = () => openDetail(row.dataset.greenhouseMemory);
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
  });
}

function renderList(memories, title) {
  const view = el('greenhouse-view');
  if (!view) return;
  let previousLabels = null;
  const rows = memories.map((memory) => {
    const html = renderMemory(memory, previousLabels);
    previousLabels = labelsFor(memory);
    return html;
  }).join('');
  view.innerHTML = `<button type="button" class="greenhouse-back">← All areas</button>` +
    `<div class="greenhouse-view-heading"><strong>${esc(title)}</strong><span style="margin-left:0.35em;">${memories.length}</span></div>` +
    (memories.length ? rows : '<p class="greenhouse-empty">No matching Memories.</p>');
  const backButton = view.querySelector('.greenhouse-back');
  if (backButton) backButton.addEventListener('click', back);
  bindMemoryRows();
}

function renderAreas() {
  const view = el('greenhouse-view');
  if (!view) return;
  const all = state.memories || [];
  const unfiled = all.filter((memory) => areaKey(memory) == null);
  view.innerHTML = `<div class="greenhouse-view-heading"><strong>Areas</strong><span style="margin-left:0.35em;">${state.areas.length}</span></div>` +
    `<button class="greenhouse-area" data-greenhouse-selection="__all__"><span>All</span><b>${all.length}</b></button>` +
    `<button class="greenhouse-area" data-greenhouse-selection="__unfiled__"><span>Unfiled</span><b>${unfiled.length}</b></button>` +
    state.areas.map((area) => `<button class="greenhouse-area" data-greenhouse-selection="${esc(area.key)}"><span>${esc(area.label)}</span><b>${area.count}</b></button>`).join('');
  view.querySelectorAll('[data-greenhouse-selection]').forEach((button) => {
    button.addEventListener('click', () => select(button.dataset.greenhouseSelection));
  });
}

function visibleMemories() {
  const source = state.memories || [];
  let memories = source;
  if (state.selected === '__unfiled__') memories = source.filter((memory) => areaKey(memory) == null);
  else if (state.selected && state.selected !== '__all__') memories = source.filter((memory) => areaKey(memory) === state.selected);
  if (state.query) {
    const query = state.query.toLowerCase();
    memories = memories.filter((memory) => contentOf(memory).toLowerCase().includes(query));
  }
  return memories;
}

function renderCurrent() {
  if (state.detail) return renderDetail(state.detail);
  if (!state.memories) return renderAreas();
  const title = state.selected === '__all__' ? 'All' : state.selected === '__unfiled__' ? 'Unfiled' :
    state.areas.find((area) => area.key === state.selected)?.label || 'Areas';
  if (state.selected == null && !state.query) return renderAreas();
  renderList(visibleMemories(), state.query ? `Search: ${state.query}` : title);
}

function select(selection) {
  state.selected = selection;
  state.detail = null;
  setNavigationDismiss();
  renderCurrent();
}

function back() {
  if (state.detail) state.detail = null;
  else state.selected = null;
  setNavigationDismiss();
  renderCurrent();
}

function showStatus(message, className) {
  const view = el('greenhouse-view');
  if (view) view.innerHTML = `<p class="greenhouse-status ${className || ''}">${esc(message)}</p>`;
}

function detailValue(memory, key, fallback = 'Not available') {
  const evidence = memory.evidence || {};
  return memory[key] ?? evidence[key] ?? labelsOf(memory)[key] ?? fallback;
}

function renderDetail(memory) {
  const view = el('greenhouse-view');
  if (!view) return;
  const ancestry = memory.ancestry || memory.evidence?.ancestry || [];
  const source = detailValue(memory, 'source_block_address');
  const superseded = memory.superseded_by;
  view.innerHTML = `<button type="button" class="greenhouse-back">← Back</button>` +
    `<div class="greenhouse-view-heading"><strong>Memory</strong></div>` +
    `<article class="greenhouse-detail"><div class="greenhouse-detail-content">${esc(contentOf(memory))}</div>` +
    `<dl><dt>Kind</dt><dd>${esc(detailValue(memory, 'kind'))}</dd><dt>Authority</dt><dd>${esc(detailValue(memory, 'authority'))}</dd>` +
    `<dt>Corroboration</dt><dd>${esc(detailValue(memory, 'corroboration_count', 0))}</dd><dt>Source</dt><dd>${esc(source)}</dd>` +
    `<dt>Freshness</dt><dd>${esc(detailValue(memory, 'freshness'))}</dd></dl>` +
    (ancestry.length ? `<div class="greenhouse-ancestry"><strong>Ancestry</strong>${ancestry.map((item) => `<div>${esc(contentOf(item))}</div>`).join('')}</div>` : '') +
    (superseded ? `<p class="greenhouse-status">Superseded by ${esc(superseded.id ?? superseded)}</p>` : '') +
    `</article><div class="greenhouse-detail-actions"><button type="button" class="greenhouse-correct">Correct</button><button type="button" class="greenhouse-archive">Archive</button></div>`;
  view.querySelector('.greenhouse-back').addEventListener('click', back);
  view.querySelector('.greenhouse-correct').addEventListener('click', () => renderCorrection(memory));
  view.querySelector('.greenhouse-archive').addEventListener('click', () => archive(memory));
}

function uuid() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function renderCorrection(memory, failure = '') {
  const view = el('greenhouse-view');
  if (!view) return;
  view.innerHTML = `<button type="button" class="greenhouse-back">← Back</button><h4>Correct Memory</h4>` +
    `${failure ? `<p class="greenhouse-status greenhouse-error">${esc(failure)}</p>` : ''}` +
    `<textarea class="greenhouse-edit" aria-label="Correct memory">${esc(contentOf(memory))}</textarea><div class="greenhouse-detail-actions"><button type="button" class="greenhouse-save-correction">Save correction</button><button type="button" class="greenhouse-cancel-correction">Cancel</button></div>`;
  view.querySelector('.greenhouse-back').addEventListener('click', back);
  view.querySelector('.greenhouse-cancel-correction').addEventListener('click', () => renderDetail(memory));
  view.querySelector('.greenhouse-save-correction').addEventListener('click', () => correct(memory, view.querySelector('.greenhouse-edit').value));
}

async function correct(memory, text) {
  const id = memory.id ?? memory.memory_id;
  const captureId = state.captureIds.get(id) || uuid();
  state.captureIds.set(id, captureId);
  const payload = { text, actor: localStorage.getItem('greenhouse.actor') || 'user', device_id: localStorage.getItem('greenhouse.device_id') || uuid(), client_capture_id: captureId, client_timestamp: new Date().toISOString() };
  localStorage.setItem('greenhouse.device_id', payload.device_id);
  try {
    const response = await globalThis['fetch'](`/api/greenhouse/v1/memories/${encodeURIComponent(id)}/correct`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    state.detail = null;
    state.memories = null;
    state.selected = null;
    setNavigationDismiss();
    await load();
  } catch (error) {
    renderCorrection(memory, `Correction failed: ${error.message}. Nothing was saved.`);
  }
}

async function archive(memory) {
  const id = memory.id ?? memory.memory_id;
  try {
    const response = await globalThis['fetch'](`/api/greenhouse/v1/memories/${encodeURIComponent(id)}/archive`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ actor: localStorage.getItem('greenhouse.actor') || 'user' }) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.memories = null;
    state.detail = null;
    setNavigationDismiss();
    await load();
  } catch (error) { showStatus(`Archive failed: ${error.message}. Nothing changed.`, 'greenhouse-error'); }
}

async function openDetail(id) {
  const memory = (state.memories || []).find((item) => String(item.id ?? item.memory_id) === String(id));
  if (!memory) return;
  state.detail = memory;
  setNavigationDismiss();
  renderDetail(memory);
  try {
    const response = await globalThis['fetch'](`/api/greenhouse/v1/memories/${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detail = await response.json();
    state.detail = detail.memory || detail.detail || detail;
    renderDetail(state.detail);
  } catch (error) { showStatus(`Memory detail unavailable: ${error.message}`, 'greenhouse-error'); }
}

async function load() {
  if (state.memories || state.loading) return;
  state.loading = true;
  state.error = null;
  showStatus('Loading Greenhouse…');
  try {
    const response = await fetch(ENDPOINT);
    if (!response.ok) {
      if (response.status === 404) throw new Error('Greenhouse is not configured.');
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.memories = payload.memories || [];
    const seen = new Map();
    state.memories.forEach((memory) => { const key = areaKey(memory); if (key != null && !seen.has(key)) seen.set(key, { key, label: areaLabel(memory), count: 0 }); });
    state.memories.forEach((memory) => { const area = seen.get(areaKey(memory)); if (area) area.count += 1; });
    state.areas = [...seen.values()];
    renderCurrent();
  } catch (error) {
    state.error = error;
    showStatus(error?.message === 'Failed to fetch' ? 'Unable to reach Greenhouse.' : `Greenhouse unavailable: ${error.message}`, 'greenhouse-error');
  } finally { state.loading = false; }
}

function open() {
  const modal = el('greenhouse-modal');
  if (!modal) return;
  // Open on the Areas list every time. Reopening used to resume wherever the
  // last visit ended, which -- before the list had a back control -- was a
  // state you could not leave by closing and reopening either.
  state.selected = null;
  state.detail = null;
  state.query = '';
  const searchField = el('greenhouse-search');
  if (searchField) searchField.value = '';
  modal.classList.remove('hidden');
  const search = el('greenhouse-search');
  if (search && !search.dataset.bound) { search.dataset.bound = '1'; search.addEventListener('input', () => { state.query = search.value.trim(); renderCurrent(); }); }
  const close = el('close-greenhouse-modal');
  if (close && !close.dataset.bound) { close.dataset.bound = '1'; close.addEventListener('click', () => { if (state.navDismiss) state.navDismiss(); modal.classList.add('hidden'); }); }
  if (state.memories) renderCurrent();
  load();
}

export default { open };