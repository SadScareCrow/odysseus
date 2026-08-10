import uiModule from './ui.js';

const ENDPOINT = '/api/greenhouse/v1/memories?limit=500';

const state = {
  memories: null,
  areas: [],
  selected: null,
  query: '',
  loading: false,
  error: null,
};

const el = (id) => document.getElementById(id);
const esc = (value) => uiModule.esc(String(value ?? ''));

function labelsOf(memory) {
  return memory.labels || {};
}

function areaKey(memory) {
  return memory.area_key ?? labelsOf(memory).area_key ?? null;
}

function areaLabel(memory) {
  const labels = labelsOf(memory);
  return labels.area || labels.area_name || areaKey(memory) || '';
}

function renderMemory(memory) {
  const labels = labelsOf(memory);
  const content = memory.content ?? memory.text ?? memory.presentation ?? '';
  const metadata = [labels.kind, labels.authority, labels.review_state]
    .filter(Boolean).map(esc).join(' · ');
  return `<article class="greenhouse-memory"><div class="greenhouse-memory-content">${esc(content)}</div>${metadata ? `<div class="greenhouse-memory-labels">${metadata}</div>` : ''}</article>`;
}

function renderList(memories, title) {
  const view = el('greenhouse-view');
  if (!view) return;
  view.innerHTML = `<div class="greenhouse-view-heading"><strong>${esc(title)}</strong><span>${memories.length}</span></div>` +
    (memories.length ? memories.map(renderMemory).join('') : '<p class="greenhouse-empty">No matching Memories.</p>');
}

function renderAreas() {
  const view = el('greenhouse-view');
  if (!view) return;
  const all = state.memories || [];
  const unfiled = all.filter((memory) => areaKey(memory) == null);
  view.innerHTML = `<div class="greenhouse-view-heading"><strong>Areas</strong><span>${state.areas.length}</span></div>` +
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
    memories = memories.filter((memory) => JSON.stringify(memory).toLowerCase().includes(query));
  }
  return memories;
}

function renderCurrent() {
  if (!state.memories) return renderAreas();
  const title = state.selected === '__all__' ? 'All' : state.selected === '__unfiled__' ? 'Unfiled' :
    state.areas.find((area) => area.key === state.selected)?.label || 'Areas';
  if (state.selected == null && !state.query) return renderAreas();
  renderList(visibleMemories(), state.query ? `Search: ${state.query}` : title);
}

function select(selection) {
  state.selected = selection;
  renderCurrent();
}

function showStatus(message, className) {
  const view = el('greenhouse-view');
  if (view) view.innerHTML = `<p class="greenhouse-status ${className || ''}">${esc(message)}</p>`;
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
    state.memories.forEach((memory) => {
      const key = areaKey(memory);
      if (key == null || seen.has(key)) return;
      seen.set(key, { key, label: areaLabel(memory), count: 0 });
    });
    state.memories.forEach((memory) => { const area = seen.get(areaKey(memory)); if (area) area.count += 1; });
    state.areas = [...seen.values()];
    renderCurrent();
  } catch (error) {
    state.error = error;
    showStatus(error?.message === 'Failed to fetch' ? 'Unable to reach Greenhouse.' : `Greenhouse unavailable: ${error.message}`, 'greenhouse-error');
  } finally {
    state.loading = false;
  }
}

function open() {
  const modal = el('greenhouse-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  const search = el('greenhouse-search');
  if (search && !search.dataset.bound) {
    search.dataset.bound = '1';
    search.addEventListener('input', () => { state.query = search.value.trim(); renderCurrent(); });
  }
  const close = el('close-greenhouse-modal');
  if (close && !close.dataset.bound) { close.dataset.bound = '1'; close.addEventListener('click', () => modal.classList.add('hidden')); }
  load();
}

export default { open };

