let pdfjs, isPdf = true, total = 0;

const $ = s => document.querySelector(s);
const docId = location.pathname.split('/').pop();
const params = new URLSearchParams(location.search);
if (params.get('embed')) document.body.classList.add('embed');
const q = params.get('q') || '';
const SCALE = 1.4;
let page = parseInt(params.get('page') || '1'), pdf, info, renderSeq = 0;
if (q) $('#searched').textContent = `Searched: “${q}”`;
$('#back').addEventListener('click', e => {
  if (history.length > 1 && document.referrer.startsWith(location.origin)) { e.preventDefault(); history.back(); }
});
const terms = [...q.matchAll(/\w+/g)].map(m => m[0].toLowerCase());
const stem = w => w.replace(/(ing|ed|es|s)$/, '');
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Unsaved edit, tagged with the page it belongs to so it is never saved to another page.
let pending = null, saveTimer = null, retryTimer = null;

function markNote(p, has) {
  info.note_pages = info.note_pages.filter(x => x !== p);
  if (has) info.note_pages.push(p);
  if (p === page) {
    $('#notemark').textContent = has ? '📝' : '';
    $('#notemark').setAttribute('aria-label', has ? 'This page has a note' : '');
  }
}

async function flush() {
  clearTimeout(saveTimer);
  if (!pending) return;
  const edit = pending;
  pending = null;
  try {
    const r = await fetch(`/api/notes/${docId}/${edit.page}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: edit.text}),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    markNote(edit.page, edit.text.trim() !== '');
    if (!pending) $('#saved').textContent = 'Saved';
  } catch (e) {
    if (!pending) pending = edit; // keep unsaved text for a retry unless newer text exists
    $('#saved').textContent = 'Save failed. Retrying…';
    clearTimeout(retryTimer);
    retryTimer = setTimeout(flush, 3000);
  }
}

function flushOnExit() {
  if (!pending) return;
  fetch(`/api/notes/${docId}/${pending.page}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: pending.text}), keepalive: true,
  });
  pending = null;
}
addEventListener('pagehide', flushOnExit);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushOnExit(); });

function showStale(ts, p) {
  const box = $('#stale');
  box.hidden = !ts;
  if (!ts) return;
  $('#staletext').textContent = `Document changed since this note was written (changed ${new Date(ts * 1000).toLocaleString()}). Check that the note still matches this page.`;
  $('#staledismiss').onclick = async () => {
    try { await fetch(`/api/notes/${docId}/${p}/dismiss`, {method: 'POST'}); box.hidden = true; } catch (e) {}
  };
}

$('#note').oninput = () => {
  $('#stale').hidden = true;
  pending = {page, text: $('#note').value};
  $('#saved').textContent = 'Saving…';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flush, 600);
};


// Continuous scrolling: every page has a fixed-size placeholder; canvases and text layers are
// rendered lazily near the viewport and released when far away, so large PDFs stay light.
const scroller = $('#scroll'), pagesEl = $('#pages');
let boxes = [], lockUntil = 0, noteSeq = 0, tokenSeq = 0;
const ocrCache = new Map();
const getOcr = p => {
  if (!ocrCache.has(p)) ocrCache.set(p, fetch(`/api/ocr/${docId}/${p}`).then(r => r.json()).catch(() => []));
  return ocrCache.get(p);
};
const ocrMatches = async p => {
  const ocr = await getOcr(p);
  return terms.length ? ocr.filter(o => terms.some(t => o.content.toLowerCase().includes(stem(t)))) : [];
};

async function renderPage(box) {
  if (box._tok) return;
  const tok = box._tok = ++tokenSeq;
  const p = +box.dataset.p;
  const pg = await pdf.getPage(p);
  const vp = pg.getViewport({scale: SCALE});
  const cv = box.querySelector('canvas');
  cv.width = vp.width; cv.height = vp.height;
  await pg.render({canvasContext: cv.getContext('2d'), viewport: vp}).promise;
  if (box._tok !== tok) return;
  const tl = box.querySelector('.tl');
  const tc = await pg.getTextContent();
  if (box._tok !== tok) return;
  const frag = document.createDocumentFragment();
  for (const it of tc.items) {
    const [, , c, d, e, f] = pdfjs.Util.transform(vp.transform, it.transform);
    const h = Math.hypot(c, d);
    const s = document.createElement('span');
    s.textContent = it.str;
    s.style.cssText = `left:${e}px;top:${f - h}px;font-size:${h}px;width:${it.width * vp.scale}px;height:${h}px`;
    const low = it.str.toLowerCase();
    if (terms.some(t => low.includes(stem(t)))) s.classList.add('hl');
    frag.appendChild(s);
  }
  tl.appendChild(frag);
  const withBox = (await ocrMatches(p)).find(o => o.bbox);
  if (withBox && box._tok === tok) {
    const [x0, y0, x1, y1] = JSON.parse(withBox.bbox);
    const b = box.querySelector('.box');
    Object.assign(b.style, {display: 'block', left: x0 * SCALE + 'px', top: y0 * SCALE + 'px', width: (x1 - x0) * SCALE + 'px', height: (y1 - y0) * SCALE + 'px'});
  }
}
function release(box) {
  if (!box._tok) return;
  box._tok = 0;
  const cv = box.querySelector('canvas');
  cv.width = cv.height = 0;
  box.querySelector('.tl').textContent = '';
  box.querySelector('.box').style.display = 'none';
}
const observer = new IntersectionObserver(entries => {
  for (const e of entries) { if (e.isIntersecting) renderPage(e.target); else release(e.target); }
}, {root: scroller, rootMargin: '100% 0px'});

async function showPage() {
  const seq = ++noteSeq, p = page;
  $('#pg').value = p;
  history.replaceState(null, '', `?page=${p}&q=${encodeURIComponent(q)}${params.get('embed') ? '&embed=1' : ''}`);
  if (!(pending && pending.page === p)) {
    try {
      const n = await (await fetch(`/api/notes/${docId}/${p}`)).json();
      if (seq === noteSeq) { $('#note').value = n.text; $('#saved').textContent = ''; showStale(n.stale_at, p); }
    } catch (e) { $('#saved').textContent = 'Could not load the note.'; }
  }
  markNote(p, info.note_pages.includes(p));
  const bn = $('#banner');
  bn.style.display = 'none';
  const m = isPdf ? await ocrMatches(p) : [];
  if (seq !== noteSeq) return;
  if (m.length) {
    bn.style.display = 'block';
    bn.innerHTML = '<b>Match found in text recognised from an image on this page:</b><br>' + m.map(o => esc(o.content)).join('<hr>');
  }
}
function setPage(p) {
  if (p === page) return;
  flush();
  page = p;
  showPage();
}

let ticking = false;
scroller.addEventListener('scroll', () => {
  if (ticking || performance.now() < lockUntil) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    const y = scroller.scrollTop + scroller.clientHeight / 3;
    let lo = 0, hi = boxes.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (boxes[mid].offsetTop <= y) lo = mid; else hi = mid - 1;
    }
    setPage(lo + 1);
  });
});

function go(p) {
  if (!(p >= 1 && p <= total)) { $('#pg').value = page; return; }
  lockUntil = performance.now() + 300;
  scroller.scrollTop = boxes[p - 1].offsetTop - 10;
  setPage(p);
}
$('#prev').onclick = () => go(page - 1);
$('#next').onclick = () => go(page + 1);
$('#pg').onchange = e => go(parseInt(e.target.value));

info = await (await fetch(`/api/docs/${docId}`)).json();
$('#title').textContent = info.name;
document.title = info.name;
isPdf = info.type === 'pdf';
const unit = (info.unit_name || 'Page').toLowerCase();
$('#pglabel').textContent = info.unit_name || 'Page';
$('#prev').setAttribute('aria-label', `Previous ${unit}`);
$('#next').setAttribute('aria-label', `Next ${unit}`);
$('#notelabel').textContent = `Notes for this ${unit}`;
$('#orig').href = `/api/file/${docId}`;
$('#orig').hidden = false;
$('#typebadge').textContent = info.type_label || '';

const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const hlRe = terms.length ? new RegExp('(' + terms.map(t => reEsc(stem(t))).join('|') + ')', 'gi') : null;
const highlight = text => hlRe ? text.split(hlRe).map((part, i) => i % 2 ? `<mark>${esc(part)}</mark>` : esc(part)).join('') : esc(text);

async function buildTextUnits() {
  const rows = await (await fetch(`/api/units/${docId}`)).json();
  const byUnit = new Map();
  for (const r of rows) { if (!byUnit.has(r.page)) byUnit.set(r.page, []); byUnit.get(r.page).push(r); }
  total = info.pages;
  for (let i = 1; i <= total; i++) {
    const box = document.createElement('section');
    box.className = 'pgbox ut';
    box.dataset.p = i;
    const parts = byUnit.get(i) || [];
    const txt = parts.filter(r => r.source === 'text').map(r => r.content).join('\n');
    const img = parts.filter(r => r.source !== 'text').map(r => r.content);
    box.innerHTML = `<h4>${esc(info.unit_name)} ${i} of ${total}</h4><pre>${highlight(txt)}</pre>` +
      img.map(t => `<div class="imgtxt"><span class="badge Image">Image</span> Text recognised from an image<pre>${highlight(t)}</pre></div>`).join('');
    pagesEl.appendChild(box);
    boxes.push(box);
  }
}

if (isPdf) {
  pdfjs = await import('/static/vendor/pdf.min.mjs');
  pdfjs.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.mjs';
  pdf = await pdfjs.getDocument({url: `/api/pdf/${docId}`, isEvalSupported: false}).promise;
  total = pdf.numPages;
  const sizes = await Promise.all(Array.from({length: total}, async (_, i) => {
    const vp = (await pdf.getPage(i + 1)).getViewport({scale: SCALE});
    return [vp.width, vp.height];
  }));
  sizes.forEach(([w, h], i) => {
    const box = document.createElement('div');
    box.className = 'pgbox';
    box.dataset.p = i + 1;
    box.style.cssText = `width:${w}px;height:${h}px`;
    box.innerHTML = '<canvas></canvas><div class="tl" aria-hidden="true"></div><div class="box"></div>';
    pagesEl.appendChild(box);
    boxes.push(box);
  });
  boxes.forEach(b => observer.observe(b));
} else {
  await buildTextUnits();
}
$('#tot').textContent = total;
$('#pg').max = total;
page = Math.min(Math.max(page, 1), Math.max(total, 1));
lockUntil = performance.now() + 300;
if (boxes.length) scroller.scrollTop = boxes[page - 1].offsetTop - 10;
if (!isPdf) {
  const first = document.querySelector('.pgbox mark');
  if (first && !params.get('page')) first.scrollIntoView({block: 'center'});
}
showPage();