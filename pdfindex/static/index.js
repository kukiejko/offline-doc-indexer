const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const name = p => p.split(/[\\/]/).pop();
let searchSeq = 0, selHref = null, fsel = new Set(), fmode = 'only', allDocs = null;

async function doSearch() {
  const q = $('#q').value.trim();
  const seq = ++searchSeq;
  if (!q) { $('#results').innerHTML = ''; $('#count').textContent = ''; return; }
  $('#count').textContent = 'Searching…';
  const src = [...document.querySelectorAll('.src:checked')].map(c => c.value).join(',');
  const params = {q, source: src, folder: $('#folder').value};
  const tsel = [...document.querySelectorAll('.ftype:checked')].map(c => c.value);
  if (!tsel.length) { $('#count').textContent = ''; $('#results').innerHTML = '<p class="muted">Select at least one file type.</p>'; return; }
  if (tsel.length < document.querySelectorAll('.ftype').length) params.types = tsel.join(',');
  if (fsel.size) { params.docs = [...fsel].join(','); params.mode = fmode; }
  let d;
  try {
    const r = await fetch('/api/search?' + new URLSearchParams(params));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  } catch (e) {
    if (seq !== searchSeq) return;
    $('#count').textContent = '';
    $('#results').innerHTML = '<div class="err" role="alert">Search failed. Is the app still running? Try again.</div>';
    return;
  }
  if (seq !== searchSeq) return; // a newer search is in flight
  $('#count').textContent = d.count ? `${d.count} result${d.count === 1 ? '' : 's'} in ${d.groups.length} file${d.groups.length === 1 ? '' : 's'}` : '';
  if (q !== foldQ) { opened.clear(); showAll.clear(); foldQ = q; }
  lastData = d; lastQ = q;
  render();
}
const SORTS = {
  most: (a, b) => b.hits.length - a.hits.length || byName(a, b),
  fewest: (a, b) => a.hits.length - b.hits.length || byName(a, b),
  az: (a, b) => byName(a, b),
  za: (a, b) => byName(b, a),
};
const TYPE_LABEL = {pdf: 'PDF', docx: 'Word', xlsx: 'Excel', pptx: 'PowerPoint', txt: 'Text', md: 'Markdown'};
const UNIT_LABEL = {pdf: 'p.', docx: 'section ', xlsx: 'sheet ', pptx: 'slide ', txt: 'part ', md: 'part '};
const byName = (a, b) => name(a.path).localeCompare(name(b.path), undefined, {numeric: true, sensitivity: 'base'}) || a.doc_id - b.doc_id;
function render() {
  const d = lastData, q = lastQ;
  if (!d) return;
  $('#foldbar').hidden = !d.count;
  const groups = d.groups.slice().sort(SORTS[sortKey]);
  const SRC = {text: 0, image: 1, note: 2};
  const hitsOf = g => hitOrder === 'page' ? g.hits.slice().sort((a, b) => a.page - b.page || (SRC[a.source] ?? 3) - (SRC[b.source] ?? 3)) : g.hits;
  $('#results').innerHTML = d.count ? groups.map(g => {
    const n = g.hits.length, id = g.doc_id, fn = esc(name(g.path));
    const open = opened.has(id), all = showAll.has(id) || n <= PREVIEW_HITS;
    return `<div class="doc" data-doc="${id}"><h3 title="${esc(g.path)}">` +
      `<button type="button" class="fold" data-doc="${id}" aria-expanded="${open}" aria-controls="body-${id}"><span class="chev" aria-hidden="true">${open ? '▾' : '▸'}</span> ${fn} <span class="ftbadge t-${esc(g.type)}">${esc(TYPE_LABEL[g.type] || g.type)}</span> · ${n} hit${n === 1 ? '' : 's'}</button>` +
      `<button type="button" class="exclude" data-doc="${id}" aria-label="Exclude ${fn} from results">Exclude file</button></h3>` +
      `<div class="body" id="body-${id}"${open ? '' : ' hidden'}>` +
      hitsOf(g).map((h, i) => `<a class="hit" href="/view/${h.doc_id}?page=${h.page}&q=${encodeURIComponent(q)}&src=${encodeURIComponent(h.source)}"${all || i < PREVIEW_HITS ? '' : ' hidden'}>` +
        `<span class="badge ${esc(h.badge)}">${esc(h.badge)}</span><b>${UNIT_LABEL[g.type] || 'p.'}${h.page}</b> ${h.snippet}</a>`).join('') +
      (all ? '' : `<button type="button" class="more" data-doc="${id}">Show all ${n} hits</button>`) + '</div></div>';
  }).join('')
    : '<p class="muted">No results.</p>';
  markSelected();
}
const PREVIEW_HITS = 5;
let opened = new Set(), showAll = new Set(), foldQ = null, lastData = null, lastQ = '', sortKey = 'most', hitOrder = 'page';
function setFold(id, fold) {
  const card = document.querySelector(`#results .doc[data-doc="${id}"]`);
  if (!card) return;
  const b = card.querySelector('button.fold');
  card.querySelector('.body').hidden = fold;
  b.setAttribute('aria-expanded', String(!fold));
  b.querySelector('.chev').textContent = fold ? '▸' : '▾';
  if (fold) opened.delete(id); else opened.add(id);
}
function expandAllHits(id) {
  showAll.add(id);
  const card = document.querySelector(`#results .doc[data-doc="${id}"]`);
  if (!card) return;
  card.querySelectorAll('a.hit').forEach(a => { a.hidden = false; });
  card.querySelector('button.more')?.remove();
}
const allIds = () => [...document.querySelectorAll('#results .doc[data-doc]')].map(c => +c.dataset.doc);
$('#hitorder').onchange = e => { hitOrder = e.target.value === 'relevance' ? 'relevance' : 'page'; render(); saveState(); };
$('#sort').onchange = e => { sortKey = SORTS[e.target.value] ? e.target.value : 'most'; render(); saveState(); };
$('#foldall').onclick = () => { allIds().forEach(id => setFold(id, true)); saveState(); };
$('#unfoldall').onclick = () => { allIds().forEach(id => setFold(id, false)); saveState(); };
$('#results').addEventListener('click', e => {
  const f = e.target.closest('button.fold'), m = e.target.closest('button.more');
  if (f) { setFold(+f.dataset.doc, f.getAttribute('aria-expanded') === 'true'); saveState(); }
  if (m) { expandAllHits(+m.dataset.doc); saveState(); }
});
function markSelected() {
  document.querySelectorAll('.hit').forEach(a => {
    const on = selHref !== null && a.getAttribute('href') === selHref;
    a.classList.toggle('sel', on);
    if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
  });
}
const KEY = 'pdfindex.lastSearch';
const saveState = () => {
  try {
    localStorage.setItem(KEY, JSON.stringify({
      q: $('#q').value, folder: $('#folder').value,
      src: [...document.querySelectorAll('.src')].filter(c => c.checked).map(c => c.value),
      types: [...document.querySelectorAll('.ftype')].filter(c => c.checked).map(c => c.value),
      scroll: top_.scrollTop, sel: selHref, docs: [...fsel], mode: fmode, foldQ, opened: [...opened], showAll: [...showAll], sort: sortKey, hitOrder}));
  } catch (e) {}
};
let t;
$('#q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(doSearch, 250); });
document.querySelectorAll('.src,.ftype,#folder').forEach(e => e.addEventListener('change', doSearch));
$('#q').addEventListener('input', saveState);
$('#folder').addEventListener('input', saveState);
document.querySelectorAll('.src,.ftype').forEach(e => e.addEventListener('change', saveState));
$('#results').addEventListener('click', saveState);
window.addEventListener('pagehide', saveState);

(async function restore() {
  let s;
  try { s = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) {}
  if (!s || !s.q) return;
  fsel = new Set((s.docs || []).filter(Number.isInteger));
  fmode = s.mode === 'exclude' ? 'exclude' : 'only';
  document.querySelectorAll('input[name=fmode]').forEach(r => { r.checked = r.value === fmode; });
  renderChip();
  if (s.foldQ === s.q.trim()) {
    opened = new Set((s.opened || []).filter(Number.isInteger));
    showAll = new Set((s.showAll || []).filter(Number.isInteger));
    foldQ = s.q.trim();
  }
  if (s.hitOrder === 'relevance') { hitOrder = 'relevance'; $('#hitorder').value = hitOrder; }
  if (SORTS[s.sort]) { sortKey = s.sort; $('#sort').value = sortKey; }
  $('#q').value = s.q;
  $('#folder').value = s.folder || '';
  document.querySelectorAll('.src').forEach(c => { c.checked = (s.src || []).includes(c.value); });
  if (Array.isArray(s.types)) document.querySelectorAll('.ftype').forEach(c => { c.checked = s.types.includes(c.value); });
  await doSearch();
  top_.scrollTop = s.scroll || 0;
  if (s.sel) {
    selHref = s.sel;
    markSelected();
    showFrame(s.sel);
  }
})();

// ---- file filter ----
function renderChip() {
  const n = fsel.size;
  $('#chip').hidden = !n;
  $('#chiptext').textContent = fmode === 'only' ? `${n} file${n === 1 ? '' : 's'} selected` : `${n} file${n === 1 ? '' : 's'} excluded`;
  const total = allDocs ? ` of ${allDocs.length}` : '';
  const sum = !n ? 'all files' : fmode === 'only' ? `only ${n}${total} selected` : `excluding ${n}`;
  $('#filesbtn').textContent = `${$('#files').hidden ? '▸' : '▾'} File filter${n ? ' •' : ':'} ${sum}`;
  $('#filesbtn').classList.toggle('active', n > 0);
  $('#filesbtn').title = n ? `File filter is ON: ${sum}. Click to ${$('#files').hidden ? 'open' : 'close'} the file list.` : `No file filter: all files are searched. Click to ${$('#files').hidden ? 'open' : 'close'} the file list.`;
}
function filterChanged() {
  renderChip();
  renderFileList();
  saveState();
  doSearch();
}
const dirOf = p => p.replace(/[\\/][^\\/]*$/, '');
function renderFileList() {
  if (!allDocs) return;
  const f = $('#ffind').value.trim().toLowerCase();
  const vis = allDocs.filter(d => !f || d.path.toLowerCase().includes(f));
  const groups = new Map();
  vis.forEach(d => { const k = dirOf(d.path); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(d); });
  $('#flist').innerHTML = [...groups].map(([dir, ds]) => `<h4>${esc(dir)}</h4>` +
    ds.map(d => `<label><input type="checkbox" data-doc="${d.id}"${fsel.has(d.id) ? ' checked' : ''}> ${esc(name(d.path))}</label>`).join('')).join('')
    || '<p class="muted">No files match.</p>';
  $('#fcount').textContent = `${vis.length} of ${allDocs.length} files shown, ${fsel.size} selected`;
}
const visibleIds = () => [...$('#flist').querySelectorAll('input[data-doc]')].map(c => +c.dataset.doc);
async function openFiles() {
  const open = $('#files').hidden;
  $('#files').hidden = !open;
  $('#filesbtn').setAttribute('aria-expanded', open);
  try { localStorage.setItem('pdfindex.filesOpen', open ? '1' : '0'); } catch (e) { /* ignore */ }
  renderChip();
  if (open && !allDocs) {
    $('#flist').innerHTML = '<p class="muted">Loading…</p>';
    try {
      const r = await fetch('/api/docs');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      allDocs = await r.json();
    } catch (e) {
      $('#flist').innerHTML = '<div class="err" role="alert">Could not load the file list.</div>';
      return;
    }
  }
  if (open) { renderFileList(); renderChip(); if (!restoring) $('#ffind').focus(); }
}
let restoring = false;
$('#filesbtn').onclick = openFiles;
try {
  if (localStorage.getItem('pdfindex.filesOpen') === '1') { restoring = true; openFiles().finally(() => { restoring = false; }); }
} catch (e) { /* ignore */ }
$('#ffind').addEventListener('input', renderFileList);
$('#flist').addEventListener('change', e => {
  const id = +e.target.dataset.doc;
  if (e.target.checked) fsel.add(id); else fsel.delete(id);
  filterChanged();
});
$('#fall').onclick = () => { visibleIds().forEach(id => fsel.add(id)); filterChanged(); };
$('#fnone').onclick = () => { visibleIds().forEach(id => fsel.delete(id)); filterChanged(); };
$('#finv').onclick = () => { visibleIds().forEach(id => fsel.has(id) ? fsel.delete(id) : fsel.add(id)); filterChanged(); };
document.querySelectorAll('input[name=fmode]').forEach(r => r.addEventListener('change', () => { fmode = r.value; filterChanged(); }));
$('#chipclear').onclick = () => { fsel.clear(); filterChanged(); $('#filesbtn').focus(); };
$('#results').addEventListener('click', e => {
  const b = e.target.closest('button.exclude');
  if (!b) return;
  const id = +b.dataset.doc;
  if (fmode === 'only' && fsel.size) fsel.delete(id);
  else { fmode = 'exclude'; document.querySelectorAll('input[name=fmode]').forEach(r => { r.checked = r.value === 'exclude'; }); fsel.add(id); }
  filterChanged();
});

const panel = $('#scanpanel');
function showPanel(on) {
  panel.hidden = !on;
  $('#rescan').setAttribute('aria-expanded', String(on));
}
$('#rescan').onclick = () => showPanel(panel.hidden);

$('#scanstart').onclick = async () => {
  const mode = document.querySelector('input[name=smode]:checked').value;
  if (mode === 'full' && !confirm('Re-index every PDF, including OCR? This can take a long time.')) return;
  try {
    const r = await fetch('/api/rescan?mode=' + mode, {method: 'POST'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (!(await r.json()).started) $('#scanmsg').textContent = 'An indexing run is already in progress.';
  } catch (e) {
    $('#scanmsg').innerHTML = '<span class="err">Could not start indexing. Try again.</span>';
    return;
  }
  poll();
};

$('#scancancel').onclick = async () => {
  $('#scancancel').disabled = true;
  try { await fetch('/api/scan/cancel', {method: 'POST'}); } catch (e) {}
  poll();
};

function fmtDur(sec) {
  if (sec == null) return '–';
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `${m}m ${s}s` : `${s}s`;
}

function renderScan(s) {
  const prog = $('#prog'), det = $('#scandet'), msg = $('#scanmsg');
  $('#scanstart').disabled = s.running;
  $('#scanmode').disabled = s.running;
  $('#scancancel').hidden = !s.running;
  $('#scancancel').disabled = !!s.cancelling;
  prog.hidden = det.hidden = !s.running;
  if (s.running) {
    showPanel(true);
    prog.max = s.pages_total || 1;
    prog.value = s.pages_done;
    const ph = s.phase === 'ocr' ? 'OCR of images' : s.phase === 'hashing' ? 'Checking checksum' : 'Extracting text';
    msg.textContent = s.cancelling ? 'Cancelling… finishing the current page.'
      : `${s.mode === 'full' ? 'Full re-index' : s.mode === 'verify' ? 'Checksum verification' : 'Indexing'} running: ${ph}`;
    const rows = [
      ['Files', `${s.files_done} of ${s.files_total}`],
      ['Pages', `${s.pages_done} of ${s.pages_total}`],
      ['Current', s.current ? `${name(s.current)}${s.pages ? ` – page ${s.page} of ${s.pages}` : ''}` : '–'],
      ['Phase', ph],
      ['Elapsed', fmtDur(s.elapsed)],
      ['Remaining (est.)', fmtDur(s.eta)],
      ['OCR images', s.ocr_items],
      ['Errors / issues', `${s.errors} / ${s.issues}`],
    ];
    det.innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(String(v))}</dd></div>`).join('');
  } else if (s.error) {
    msg.innerHTML = `<span class="err">Indexing failed: ${esc(s.error)}</span>`;
  } else if (s.last && s.last.duration != null) {
    const l = s.last;
    if (!renderScan.seen) showPanel(true);
    msg.textContent = `${l.cancelled ? 'Cancelled' : 'Finished'} in ${fmtDur(l.duration)}: ` +
      `${l.indexed} indexed, ${l.skipped} skipped, ${l.error} failed, ${l.modified || 0} modified, ${l.moved || 0} moved, ${l.removed || 0} missing; ${l.pages} pages, ` +
      `${l.ocr_items} OCR images, ${l.issues} issues.`;
    if ((l.modified_files || []).length) {
      msg.insertAdjacentHTML('beforeend', '<details><summary>Modified files (re-indexed)</summary><ul>' +
        l.modified_files.map(f => `<li>${esc(f)}</li>`).join('') + '</ul></details>');
    }
  }
  renderScan.seen = true;
}

let pollTimer;
async function poll() {
  clearTimeout(pollTimer);
  let s;
  try {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    s = await r.json();
  } catch (e) {
    $('#status').innerHTML = '<div class="err">Cannot reach the app. Retrying…</div>';
    pollTimer = setTimeout(poll, 3000);
    return;
  }
  $('#status').innerHTML = (s.ocr_available ? '' : '<div class="warn">Tesseract not found: text inside images is not indexed. See README.</div>') +
    `${s.documents} PDFs indexed.`;
  renderScan(s);
  if (s.running) pollTimer = setTimeout(poll, 1000);
}

async function loadList(url, el, render, empty) {
  el.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const n = await r.json();
    el.innerHTML = n.length ? n.map(render).join('') : `<p class="muted">${empty}</p>`;
  } catch (e) {
    el.innerHTML = '<div class="err" role="alert">Could not load. Try again.</div>';
  }
}
const noteRow = x => `<div class="doc"><h3>${esc(name(x.path))}, p.${x.page}</h3>${x.stale_at ? `<div class="warn">⚠ Document changed ${esc(new Date(x.stale_at * 1000).toLocaleString())} since this note was written. Open the page to check and dismiss.</div>` : ''}<a class="hit" href="/view/${x.doc_id}?page=${x.page}">${esc(x.text)}</a></div>`;

async function loadNotes() {
  const el = $('#notes');
  await loadList('/api/notes', el, noteRow, 'No notes yet.');
  let orphans = [], docs = [];
  try {
    orphans = await (await fetch('/api/orphans')).json();
    if (orphans.length) docs = await (await fetch('/api/docs')).json();
  } catch (e) { return; }
  if (!orphans.length) return;
  const opts = docs.map(d => `<option value="${d.id}">${esc(d.path)}</option>`).join('');
  el.insertAdjacentHTML('beforeend', '<h2>Orphaned notes</h2><p class="muted">These notes belong to PDFs that are no longer in the folders (deleted, or moved and changed). Relink them to another file to keep them.</p>' +
    orphans.map(o => `<div class="doc orphan" data-id="${o.doc_id}"><b>${esc(o.path)}</b>` +
      o.notes.map(n => `<p><span class="muted">p.${n.page}</span> ${esc(n.text)}</p>`).join('') +
      `<label>Relink to: <select aria-label="Relink notes of ${esc(name(o.path))} to">${opts}</select></label> <button type="button" class="relink">Relink</button> <span class="muted relmsg" role="status"></span></div>`).join(''));
}
$('#notes').addEventListener('click', async e => {
  const b = e.target.closest('.relink');
  if (!b) return;
  const box = b.closest('.orphan'), msg = box.querySelector('.relmsg');
  try {
    const r = await fetch(`/api/orphans/${box.dataset.id}/relink`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({target_id: Number(box.querySelector('select').value)})});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    loadNotes();
  } catch (err) { msg.textContent = 'Relink failed.'; }
});
const loadIssues = () => loadList('/api/issues', $('#issues'), x => `<div class="doc"><b>${esc(name(x.path))}</b>${x.page ? ' p.' + x.page : ''}<br><span class="muted">${esc(x.message)}</span></div>`, 'No issues.');

const tabs = [...document.querySelectorAll('[role=tab]')];
function selectTab(tab) {
  tabs.forEach(x => { const on = x === tab; x.setAttribute('aria-selected', on); x.tabIndex = on ? 0 : -1; $('#' + x.dataset.t).hidden = !on; });
  tab.focus();
  if (tab.dataset.t === 'notes') loadNotes();
  if (tab.dataset.t === 'issues') loadIssues();
}
tabs.forEach((tab, i) => {
  tab.onclick = () => selectTab(tab);
  tab.onkeydown = e => {
    const k = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1}[e.key];
    if (k === undefined) return;
    e.preventDefault();
    selectTab(tabs[(k + tabs.length) % tabs.length]);
  };
});

// ---- split view: load hits into the bottom pane ----
const frame = $('#pdfview'), top_ = $('#top'), splitter = $('#split'), panes = $('#panes');
let loadTimer;
function showFrame(href) {
  $('#placeholder').hidden = true;
  frame.hidden = false;
  frame.src = href + (href.includes('?') ? '&' : '?') + 'embed=1';
}
function openHit(a) {
  document.querySelectorAll('.hit.sel').forEach(x => { x.classList.remove('sel'); x.removeAttribute('aria-current'); });
  a.classList.add('sel');
  a.setAttribute('aria-current', 'true');
  selHref = a.getAttribute('href');
  clearTimeout(loadTimer);
  loadTimer = setTimeout(() => showFrame(selHref), 120);
  saveState();
}
top_.addEventListener('click', e => {
  const a = e.target.closest('a.hit');
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
  e.preventDefault();
  openHit(a);
});
top_.addEventListener('keydown', e => {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const hits = [...top_.querySelectorAll('[role=tabpanel]:not([hidden]) a.hit')].filter(a => !a.hidden && !a.closest('[hidden]'));
  const i = hits.indexOf(document.activeElement);
  if (!hits.length || i < 0) return;
  e.preventDefault();
  const n = hits[Math.max(0, Math.min(hits.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
  n.focus();
  n.scrollIntoView({block: 'nearest'});
  openHit(n);
});
$('#q').addEventListener('keydown', e => {
  if (e.key !== 'ArrowDown') return;
  const first = $('#results a.hit:not([hidden])');
  if (first) { e.preventDefault(); first.focus(); openHit(first); }
});

// ---- resizable splitter (remembered) ----
const SPLIT_KEY = 'pdfindex.split';
function setSplit(frac) {
  frac = Math.max(0.1, Math.min(0.85, frac));
  top_.style.height = (frac * 100) + '%';
  splitter.setAttribute('aria-valuenow', Math.round(frac * 100));
  try { localStorage.setItem(SPLIT_KEY, String(frac)); } catch (e) {}
}
splitter.setAttribute('aria-valuemin', 10);
splitter.setAttribute('aria-valuemax', 85);
let savedFrac = null;
try { savedFrac = parseFloat(localStorage.getItem(SPLIT_KEY)); } catch (e) {}
setSplit(savedFrac || 0.45);
const curFrac = () => top_.getBoundingClientRect().height / panes.getBoundingClientRect().height;
splitter.addEventListener('pointerdown', e => {
  splitter.setPointerCapture(e.pointerId);
  splitter.classList.add('drag');
  frame.style.pointerEvents = 'none';
});
splitter.addEventListener('pointermove', e => {
  if (!splitter.classList.contains('drag')) return;
  const r = panes.getBoundingClientRect();
  setSplit((e.clientY - r.top) / r.height);
});
const endDrag = () => { splitter.classList.remove('drag'); frame.style.pointerEvents = ''; };
splitter.addEventListener('pointerup', endDrag);
splitter.addEventListener('pointercancel', endDrag);
splitter.addEventListener('keydown', e => {
  const step = e.shiftKey ? 0.1 : 0.03;
  const f = {ArrowUp: curFrac() - step, ArrowDown: curFrac() + step, Home: 0.1, End: 0.85}[e.key];
  if (f === undefined) return;
  e.preventDefault();
  setSplit(f);
});

poll();

// ---- help / release info ----
const helpdlg = $('#helpdlg');
$('#helpbtn').onclick = async () => {
  helpdlg.showModal();
  try {
    const v = await (await fetch('/api/version')).json();
    $('#verinfo').textContent = `${v.name} version ${v.version} · released ${new Date(v.released).toLocaleString()} · Designed by ${v.designer}`;
    $('#changelog').textContent = await (await fetch('/api/changelog')).text();
  } catch (e) {
    $('#verinfo').textContent = 'Could not load release info.';
  }
};
$('#helpclose').onclick = () => helpdlg.close();
helpdlg.addEventListener('click', e => { if (e.target === helpdlg) helpdlg.close(); });

