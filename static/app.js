// Reproductor, grupos y lo propio de sellos.
let ALL = [], current = -1, pollTimer = null, LABEL = null;
let QUEUE = [], qi = -1, CUR = null, ytPlayer = null, navDir = 1, playToken = 0;
let SOURCES = [], GROUPS = [], SRC = null, loadToken = 0, lastFinished = null;
const COLLAPSED = new Set(JSON.parse(localStorage.getItem('sellos-collapsed') || '[]'));
const RTYPES = ['Album', 'EP', 'Single', 'Compilation', 'Live album', 'Remix', 'Soundtrack', 'DJ Mix', 'Mixtape', 'Demo', 'Interview', 'Unknown'];
const RTYPE_LABEL = {
  'Album': 'Álbumes', 'EP': 'EPs', 'Single': 'Singles', 'Compilation': 'Recopilatorios', 'Live album': 'Directos',
  'Remix': 'Remixes', 'Soundtrack': 'Bandas sonoras', 'DJ Mix': 'DJ Mixes', 'Mixtape': 'Mixtapes', 'Demo': 'Demos',
  'Interview': 'Entrevistas', 'Unknown': 'Otros',
};
const RTYPE_SHORT = {'Album': 'álb.', 'EP': 'EP', 'Single': 'single', 'Compilation': 'recop.'};

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
// Solo enlaces http(s): lo que viene de las API no se mete tal cual en un href
function safeUrl(u) { return /^https?:\/\//i.test(u || '') ? u : ''; }
function extLink(u, text) {
  const s = safeUrl(u);
  return s ? `<a class="d-ext-link" href="${esc(s)}" target="_blank" rel="noopener">${esc(text)}</a>` : '';
}
async function api(ep, body) {
  const opts = body !== undefined
    ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}
    : {method: 'GET'};
  return (await fetch(ep, opts)).json();
}
function fmt(n) {
  if (!n) return '0';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'K';
  return String(n);
}
function fmtDur(s) {
  if (!s) return '';
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

// ── Barra lateral ─────────────────────────────────────────────────────────────
async function loadList(sel) {
  [SOURCES, GROUPS] = await Promise.all([api('/api/sources'), api('/api/groups')]);
  renderSidebar();
  if (sel) loadSource(sel);
}

function renderSidebar() {
  const el = document.getElementById('sb-list');
  el.innerHTML = '';
  const byKey = Object.fromEntries(SOURCES.map(x => [x.key, x]));
  const grouped = new Set();
  for (const g of GROUPS) {
    const members = g.members.map(k => byKey[k]).filter(Boolean).sort((x, y) => x.name.localeCompare(y.name));
    members.forEach(m => grouped.add(m.key));
    const closed = COLLAPSED.has(g.id);
    const head = document.createElement('div');
    head.className = 'grp-head';
    head.innerHTML = `<span class="grp-arrow">${closed ? '▸' : '▾'}</span>`
      + `<span class="grp-name">${esc(g.name)}</span><span class="grp-n">${members.length}</span>`
      + `<button class="grp-ren" title="Renombrar">✎</button><button class="grp-del" title="Borrar grupo">✕</button>`;
    head.addEventListener('click', () => {
      if (COLLAPSED.has(g.id)) COLLAPSED.delete(g.id); else COLLAPSED.add(g.id);
      localStorage.setItem('sellos-collapsed', JSON.stringify([...COLLAPSED]));
      renderSidebar();
    });
    head.querySelector('.grp-ren').addEventListener('click', e => { e.stopPropagation(); renameGroup(g); });
    head.querySelector('.grp-del').addEventListener('click', e => { e.stopPropagation(); deleteGroup(g); });
    el.appendChild(head);
    if (closed) continue;
    if (!members.length) el.insertAdjacentHTML('beforeend', '<div class="grp-empty">Vacío: añade sellos con ⋯</div>');
    members.forEach(m => el.appendChild(sourceItem(m, true)));
  }
  const loose = SOURCES.filter(x => !grouped.has(x.key));
  if (GROUPS.length && loose.length) el.insertAdjacentHTML('beforeend', '<div class="sb-sep"></div>');
  loose.forEach(m => el.appendChild(sourceItem(m, false)));
}

function sourceItem(src, inGroup) {
  const div = document.createElement('div');
  div.className = 'col-item' + (inGroup ? ' in-group' : '') + (SRC === src.key ? ' active' : '');
  div.dataset.key = src.key;
  const types = RTYPES.filter(t => src.types[t]).slice(0, 3)
    .map(t => `${src.types[t]} ${RTYPE_SHORT[t] || t.toLowerCase()}`).join(' · ') || 'sin datos aún';
  const state = {queued: ' · en cola', running: ' · actualizando', youtube: ' · vídeos pendientes', error: ' · ✗ error', partial: ' · ⚠ incompleto'}[src.status] || '';
  div.innerHTML = `<div class="col-main"><span class="col-name">${esc(src.name)}</span>`
    + `<span class="col-count" title="${esc(src.error || '')}">${esc(types + state)}</span></div>`
    + `<button class="col-refresh" title="Actualizar catálogo, Last.fm y vídeos que falten">↻</button>`
    + `<button class="col-menu" title="Grupos">⋯</button>`
    + `<button class="col-menu col-del" title="Quitar este sello">✕</button>`;
  div.addEventListener('click', () => loadSource(src.key));
  div.querySelector('.col-refresh').addEventListener('click', e => { e.stopPropagation(); runFetch({slug: src.key}); });
  div.querySelector('.col-menu').addEventListener('click', e => { e.stopPropagation(); openGroupMenu(src, e.currentTarget); });
  div.querySelector('.col-del').addEventListener('click', async e => {
    e.stopPropagation();
    if (!confirm(`¿Quitar «${src.name}» de la lista?`)) return;
    const r = await api('/api/delete', {slug: src.key});
    if (r.error) { alert(r.error); return; }
    if (SRC === src.key) { SRC = null; location.reload(); return; }
    loadList();
  });
  return div;
}

// ── Grupos ────────────────────────────────────────────────────────────────────
async function saveGroups() {
  const r = await api('/api/groups', {groups: GROUPS});
  if (r && r.groups) GROUPS = r.groups;
  renderSidebar();
}

function newGroup(firstMember) {
  const name = (prompt('Nombre del grupo') || '').trim();
  if (!name) return null;
  const g = {id: 'g' + Date.now().toString(36), name, members: firstMember ? [firstMember] : []};
  GROUPS.push(g);
  saveGroups();
  return g;
}

function renameGroup(g) {
  const name = (prompt('Nuevo nombre', g.name) || '').trim();
  if (!name || name === g.name) return;
  g.name = name;
  saveGroups();
}

function deleteGroup(g) {
  if (!confirm(`¿Borrar el grupo «${g.name}»? Sus entradas no se borran, vuelven a la lista.`)) return;
  GROUPS = GROUPS.filter(x => x !== g);
  saveGroups();
}

const grpMenu = document.getElementById('grp-menu');
function openGroupMenu(src, anchor) {
  grpMenu.innerHTML = `<div class="gm-title">Grupos de «${esc(src.name)}»</div>`
    + GROUPS.map((g, i) => `<label><input type="checkbox" data-i="${i}"${g.members.includes(src.key) ? ' checked' : ''}> ${esc(g.name)}</label>`).join('')
    + `<div class="gm-new">+ Nuevo grupo con esta entrada…</div>`;
  grpMenu.querySelectorAll('input').forEach(cb => cb.addEventListener('change', () => {
    const g = GROUPS[+cb.dataset.i];
    g.members = cb.checked ? [...g.members, src.key] : g.members.filter(k => k !== src.key);
    saveGroups();
  }));
  grpMenu.querySelector('.gm-new').addEventListener('click', () => { grpMenu.hidden = true; newGroup(src.key); });
  grpMenu.hidden = false;
  const r = anchor.getBoundingClientRect();
  grpMenu.style.left = Math.min(r.left, innerWidth - grpMenu.offsetWidth - 8) + 'px';
  grpMenu.style.top  = Math.min(r.bottom + 4, innerHeight - grpMenu.offsetHeight - 8) + 'px';
}
document.addEventListener('click', e => { if (!grpMenu.hidden && !grpMenu.contains(e.target)) grpMenu.hidden = true; });
document.getElementById('grp-add').addEventListener('click', () => newGroup());

// ── YouTube ───────────────────────────────────────────────────────────────────
function ytParse(url) {
  let u;
  try { u = new URL((url || '').trim()); } catch (e) { return null; }
  const host = u.hostname.replace(/^(www\.|m\.|music\.)/, '');
  if (!['youtube.com', 'youtube-nocookie.com', 'youtu.be'].includes(host)) return null;
  const list = u.searchParams.get('list');
  let id = host === 'youtu.be' ? u.pathname.slice(1).split('/')[0] : u.searchParams.get('v');
  const m = u.pathname.match(/^\/(?:embed|shorts|live|v)\/([\w-]{11})/);
  if (!id && m && m[1] !== 'videoseries') id = m[1];
  if (id && !/^[\w-]{11}$/.test(id)) id = null;
  if (list) return {list, id};
  return id ? {id} : null;
}

function addToQueue(url, title, src) {
  const v = ytParse(url);
  if (!v) return false;
  const key = v.list || v.id;
  if (QUEUE.some(q => (q.list || q.id) === key)) return false;
  QUEUE.push({...v, url, title: title || '', src});
  return true;
}

let _ytReady = null;
function ytApi() {
  if (!_ytReady) _ytReady = new Promise(res => {
    window.onYouTubeIframeAPIReady = res;
    const s = document.createElement('script');
    s.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(s);
  });
  return _ytReady;
}

function showNoVideo(msg) {
  const wrap = document.getElementById('d-player-wrap'), nv = document.getElementById('d-no-video');
  if (!wrap) return;
  if (ytPlayer) { try { ytPlayer.stopVideo(); } catch (e) {} }
  wrap.hidden = true;
  nv.hidden = false;
  nv.textContent = msg;
}

async function playIndex(i) {
  if (i < 0 || i >= QUEUE.length) return;
  qi = i;
  const q = QUEUE[i], token = playToken;
  renderQueue();
  document.getElementById('d-no-video').hidden = true;
  document.getElementById('d-player-wrap').hidden = false;
  await ytApi();
  if (token !== playToken || qi !== i) return;
  const load = p => q.list
    ? p.loadPlaylist({list: q.list, listType: 'playlist', index: 0})
    : p.loadVideoById(q.id);
  if (ytPlayer) { load(ytPlayer); return; }
  ytPlayer = new YT.Player('d-player', {
    width: '100%',
    playerVars: {rel: 0, autoplay: 1, playsinline: 1},
    events: {
      onReady: e => load(e.target),
      onStateChange: onYtState,
      onError: onYtError,
    },
  });
}

function inPlaylistMiddle() {
  // ¿El vídeo actual es una lista de YouTube y quedan canciones en ella?
  if (!ytPlayer || qi < 0 || !QUEUE[qi] || !QUEUE[qi].list) return false;
  const pl = ytPlayer.getPlaylist() || [];
  return ytPlayer.getPlaylistIndex() < pl.length - 1;
}

function onYtState(e) {
  if (e.data === YT.PlayerState.ENDED && !inPlaylistMiddle()) nextTrack();
}

function onYtError() {
  // 100/101/150: borrado o no se puede incrustar → se marca y se sigue
  if (qi >= 0 && QUEUE[qi]) QUEUE[qi].bad = true;
  renderQueue();
  nextTrack();
}

function nextTrack() {
  if (inPlaylistMiddle()) { ytPlayer.nextVideo(); return; }
  for (let i = qi + 1; i < QUEUE.length; i++) if (!QUEUE[i].bad) { playIndex(i); return; }
  stepAlbum(1);
}

function prevTrack() {
  if (ytPlayer && qi >= 0 && QUEUE[qi] && QUEUE[qi].list && ytPlayer.getPlaylistIndex() > 0) {
    ytPlayer.previousVideo(); return;
  }
  for (let i = qi - 1; i >= 0; i--) if (!QUEUE[i].bad) { playIndex(i); return; }
  stepAlbum(-1);
}

// Siguiente/anterior disco en el orden del grid (respeta búsqueda y filtros)
function stepAlbum(dir) {
  const cards = [...document.querySelectorAll('.card')];
  if (!cards.length) return;
  const idx = cards.findIndex(c => +c.dataset.uid === current);
  const n = idx < 0 ? (dir > 0 ? 0 : cards.length - 1) : idx + dir;
  if (n < 0 || n >= cards.length) { showNoVideo('Fin de la lista'); return; }
  const card = cards[n], item = ALL[+card.dataset.uid];
  if (item) select(item, card, dir);
}

// ── Cargar un sello ───────────────────────────────────────────────────────────
async function fetchLabel(key) {
  const d = await api('/api/data/' + encodeURIComponent(key));
  return d.error ? null : d;
}

async function loadSource(key) {
  SRC = key;
  document.querySelectorAll('.col-item').forEach(e => e.classList.toggle('active', e.dataset.key === key));
  const token = ++loadToken;
  const d = await fetchLabel(key);
  if (token !== loadToken || !d) return;
  LABEL = d.label;
  ALL = d.albums.map((a, uid) => ({...a, uid}));
  current = -1;
  document.getElementById('col-title').textContent = LABEL.name;
  document.getElementById('toolbar').style.display = '';
  document.getElementById('grid-wrap').style.display = '';
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('search').value = '';
  document.getElementById('tag-filter').value = '';
  resetDetail();
  renderLabelInfo();
  renderTagFilter();
  applyFilter();
}

// Mientras se rellena: se actualiza lo que hay sin cambiar los uid ni cortar el vídeo
async function mergeLabel() {
  const key = SRC, d = key && await fetchLabel(key);
  if (!d || key !== SRC) return;
  LABEL = d.label;
  // Una sección desmarcada desaparece; lo que suena sigue sonando
  const keep = new Set(d.albums.map(a => a.key));
  if (ALL.some(a => !keep.has(a.key))) {
    ALL = ALL.filter(a => keep.has(a.key));
    ALL.forEach((a, i) => { a.uid = i; });
    current = CUR && ALL.includes(CUR) ? CUR.uid : -1;
  }
  const byKey = new Map(ALL.map(a => [a.key, a]));
  for (const a of d.albums) {
    const prev = byKey.get(a.key);
    if (prev) Object.assign(prev, a); else ALL.push({...a, uid: ALL.length});
  }
  renderLabelInfo();
  renderTagFilter();
  applyFilter();
}

function renderLabelInfo() {
  const el = document.getElementById('label-info'), i = (LABEL && LABEL.info) || {};
  const meta = [i.type, i.country, i.begin && 'desde ' + i.begin.slice(0, 4), i.end && 'hasta ' + i.end.slice(0, 4)].filter(Boolean);
  const totalL = ALL.reduce((n, a) => n + (a.listeners || 0), 0);
  const totalP = ALL.reduce((n, a) => n + (a.playcount || 0), 0);
  if (totalP) meta.push(`${fmt(totalL)} oyentes y ${fmt(totalP)} escuchas sumando sus discos`);
  const links = [extLink(i.mb_url, 'MusicBrainz'), extLink(i.discogs_url, 'Discogs'),
    ...(i.urls || []).slice(0, 3).map(u => { try { return extLink(u, new URL(u).hostname.replace('www.', '')); } catch (e) { return ''; } })].filter(Boolean);
  el.hidden = !(meta.length || links.length || (LABEL && LABEL.error));
  el.innerHTML = (LABEL && LABEL.error ? `<div class="li-err">⚠ ${esc(LABEL.error)}</div>` : '')
    + `<span>${esc(meta.join(' · '))}</span> ${links.join(' ')}`;
}

function renderTagFilter() {
  const sel = document.getElementById('tag-filter'), value = sel.value, counts = {};
  ALL.forEach(a => (a.tags || []).forEach(t => { const k = t.toLowerCase(); counts[k] = (counts[k] || 0) + 1; }));
  const tags = Object.entries(counts).sort((x, y) => y[1] - x[1]).slice(0, 60);
  sel.innerHTML = '<option value="">Todas las etiquetas</option>'
    + tags.map(([t, n]) => `<option value="${esc(t)}">${esc(t)} (${n})</option>`).join('');
  sel.value = counts[value] ? value : '';
}

const SORTS = {
  'listeners': (x, y) => (y.listeners || 0) - (x.listeners || 0),
  'playcount': (x, y) => (y.playcount || 0) - (x.playcount || 0),
  'year-desc': (x, y) => (y.year || 0) - (x.year || 0),
  'year-asc': (x, y) => (x.year || 9999) - (y.year || 9999),
  'artist': (x, y) => x.artist.localeCompare(y.artist) || x.album.localeCompare(y.album),
};

function applyFilter() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const tag = document.getElementById('tag-filter').value;
  const items = ALL.filter(a => (!q || a.artist.toLowerCase().includes(q) || a.album.toLowerCase().includes(q)
      || (a.catno || '').toLowerCase().includes(q))
    && (!tag || (a.tags || []).some(t => t.toLowerCase() === tag)));
  render(items.sort(SORTS[document.getElementById('sort').value]));
}

// Si una portada falla se prueba la siguiente; si no queda ninguna, se oculta
function nextCover(img, keepSpace) {
  const rest = (img.dataset.alt || '').split(' ').filter(Boolean);
  if (rest.length) { img.dataset.alt = rest.slice(1).join(' '); img.src = rest[0]; return; }
  if (keepSpace) img.style.opacity = 0; else img.style.display = 'none';
}
function coverImg(a, attrs, keepSpace) {
  const c = (a.covers || []).map(safeUrl).filter(Boolean);
  return c.length ? `<img ${attrs} src="${esc(c[0])}" alt="" data-alt="${esc(c.slice(1).join(' '))}" onerror="nextCover(this${keepSpace ? ', true' : ''})">` : '';
}

function render(data) {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  const withVideo = ALL.filter(a => (a.videos && a.videos.length) || a.custom.length).length;
  const pending = ALL.filter(a => a.videos === null).length;
  document.getElementById('grid-count').textContent = `${data.length} discos · ${withVideo} con vídeo`
    + (pending ? ` · ${pending} por buscar` : '');
  for (const t of RTYPES) {
    const items = data.filter(a => a.rtype === t);
    if (!items.length) continue;
    const sec = document.createElement('div');
    sec.className = 'sec';
    sec.innerHTML = `<div class="sec-head"><span>${esc(RTYPE_LABEL[t])}</span><span class="sec-n">${items.length}</span></div>`;
    const g = document.createElement('div');
    g.className = 'sec-grid';
    items.forEach(a => g.appendChild(card(a)));
    sec.appendChild(g);
    grid.appendChild(sec);
  }
}

function card(a) {
  const el = document.createElement('div');
  el.className = 'card' + (a.uid === current ? ' active' : '');
  el.dataset.uid = a.uid;
  el.dataset.ownedArtist = a.artist;
  el.dataset.ownedAlbum = a.album;
  const stats = a.listeners ? `<div class="hi-stats">${fmt(a.listeners)} oyentes · ${fmt(a.playcount)} esc.</div>` : '';
  el.innerHTML = (coverImg(a, 'loading="lazy"') || '<div class="no-cover">🎵</div>')
    + `<div class="badges" data-owned-slot></div>`
    + (a.year ? `<div class="rank">${a.year}</div>` : '')
    + (a.videos && !a.videos.length && !a.custom.length ? '<div class="no-vid" title="yt-dlp no ha encontrado vídeo">✕▶</div>' : '')
    + `<div class="hover-info"><div class="hi-artist">${esc(a.artist)}</div><div class="hi-album">${esc(a.album)}</div>${stats}</div>`;
  el.addEventListener('click', () => select(a, el));
  return el;
}

// ── Detalle ───────────────────────────────────────────────────────────────────
function resetDetail() {
  if (ytPlayer) { try { ytPlayer.destroy(); } catch (e) {} ytPlayer = null; }
  QUEUE = []; qi = -1; CUR = null;
  document.getElementById('grid').classList.remove('has-active');
  document.getElementById('detail').innerHTML = '<div id="d-placeholder">← Selecciona un álbum</div>';
}

// El reproductor vive fuera de lo que se repinta: un único iframe que ya ha
// sonado puede seguir sonando sin que el navegador lo bloquee.
function ensureDetailSkeleton() {
  if (document.getElementById('d-player-wrap')) return;
  document.getElementById('detail').innerHTML = `<div id="d-head"></div>`
    + `<div id="d-player-wrap" hidden><div id="d-player"></div></div>`
    + `<div id="d-no-video">Buscando vídeos…</div><div id="d-queue"></div><div id="d-extra"></div>`;
}

function select(a, cardEl, dir) {
  navDir = dir || 0;   // 0 = elegido a mano; ±1 = avance automático o N/B
  current = a.uid;
  CUR = a;
  document.querySelectorAll('.card').forEach(c => c.classList.remove('active'));
  document.getElementById('grid').classList.add('has-active');
  if (!cardEl) cardEl = document.querySelector(`.card[data-uid="${current}"]`);
  if (cardEl) { cardEl.classList.add('active'); cardEl.scrollIntoView({block: 'nearest'}); }
  ensureDetailSkeleton();

  const ytUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(a.artist + ' ' + a.album)}`;
  document.getElementById('d-head').innerHTML =
    `<div id="d-cover-wrap">${coverImg(a, 'id="d-cover"', true) || '<div id="d-no-cover">🎵</div>'}</div>`
    + `<div id="d-info"><div id="d-rank">${esc([RTYPE_LABEL[a.rtype], a.date || a.year, a.catno].filter(Boolean).join(' · '))}</div>`
    + `<div id="d-artist">${esc(a.artist)}</div>`
    + `<div id="d-album" data-owned-artist="${esc(a.artist)}" data-owned-album="${esc(a.album)}">${esc(a.album)}</div></div>`
    + `<div id="d-link"><a class="d-btn d-btn-yt" href="${esc(ytUrl)}" target="_blank" rel="noopener">▶ Buscar en YouTube</a></div>`;
  document.getElementById('d-extra').innerHTML = '<div class="d-loading">Cargando info…</div>';

  QUEUE = []; qi = -1;
  fillQueue(a);
  renderQueue();
  const token = ++playToken;
  if (QUEUE.length) playIndex(0);
  else showNoVideo('Buscando vídeos…');
  loadDetail(a, token);
}

// Los enlaces propios sustituyen a lo encontrado por yt-dlp
function fillQueue(a) {
  if (a.custom && a.custom.length) { a.custom.forEach(u => addToQueue(u, '', 'propio')); return; }
  (a.videos || []).forEach(v => addToQueue(`https://www.youtube.com/watch?v=${v.id}`,
    v.title + (v.duration ? ` (${fmtDur(v.duration)})` : ''), 'yt-dlp'));
}

async function loadDetail(a, token, refresh) {
  let d;
  try {
    d = await api('/api/album?' + new URLSearchParams({key: a.key, rtype: a.rtype, label: LABEL ? LABEL.name : '', ...(refresh ? {refresh: 1} : {})}));
  } catch (e) { d = {errors: ['No se ha podido cargar']}; }
  if (token !== playToken || CUR !== a) return;
  if (d.videos) a.videos = d.videos;
  fillQueue(a);
  renderQueue();
  if (d.lastfm) {
    a.listeners = d.lastfm.listeners; a.playcount = d.lastfm.playcount; a.tags = d.lastfm.tags || [];
    if (d.lastfm.image && a.covers[0] !== d.lastfm.image) a.covers = [d.lastfm.image, ...a.covers];
  }
  renderExtra(a, d);
  if (qi >= 0) return;
  const first = QUEUE.findIndex(q => !q.bad);
  if (first >= 0) { playIndex(first); return; }
  if (navDir) { stepAlbum(navDir); return; }   // en marcha: se salta el disco sin vídeos
  showNoVideo((d.errors || []).find(e => e.startsWith('YouTube')) || 'Sin vídeo disponible');
  openLinkForm();
}

function section(title, body) {
  return `<div class="d-section"><div class="d-section-title">${esc(title)}</div>${body}</div>`;
}
function tagsHtml(list) {
  return `<div class="d-tags">${list.map(t => `<span class="d-tag">${esc(t)}</span>`).join('')}</div>`;
}
let textId = 0;
function longText(summary, full) {
  const id = 'd-text-' + (++textId), hasMore = full && full.length > (summary || '').length + 20;
  return `<div class="d-text" id="${id}" data-full="${esc(full || '')}" data-short="${esc(summary || full)}">${esc(summary || full)}</div>`
    + `<button class="d-expand" onclick="toggleExpand('${id}',this)">${hasMore ? 'Leer todo ▾' : 'Leer más ▾'}</button>`;
}
function toggleExpand(id, btn) {
  const el = document.getElementById(id);
  const open = el.classList.toggle('expanded');
  el.textContent = open ? (el.dataset.full || el.dataset.short) : el.dataset.short;
  btn.textContent = open ? 'Leer menos ▴' : 'Leer todo ▾';
}

function renderExtra(a, d) {
  const el = document.getElementById('d-extra');
  const lf = d.lastfm || {}, ar = d.artist || {}, parts = [];
  if (lf.listeners || lf.playcount) {
    const you = lf.userplaycount ? `<div class="d-stat"><b>${fmt(lf.userplaycount)}</b>tuyas</div>` : '';
    parts.push(`<div class="d-statrow"><div class="d-stat"><b>${fmt(lf.listeners)}</b>oyentes</div>`
      + `<div class="d-stat"><b>${fmt(lf.playcount)}</b>escuchas</div>${you}</div>`);
  }
  if (lf.tags && lf.tags.length) parts.push(section('Etiquetas', tagsHtml(lf.tags)));
  if (lf.wiki || lf.wiki_summary) parts.push(section('Sobre el disco', longText(lf.wiki_summary, lf.wiki)));
  if (lf.tracks && lf.tracks.length) {
    const total = lf.tracks.reduce((n, t) => n + (t.duration || 0), 0);
    parts.push(section(`Canciones (${lf.tracks.length}${total ? ' · ' + fmtDur(total) : ''})`,
      `<ol class="d-tracks">${lf.tracks.map(t => `<li><span>${esc(t.name)}</span><span class="d-dur">${fmtDur(t.duration)}</span></li>`).join('')}</ol>`));
  }
  if (ar.name) {
    const stats = ar.listeners ? `<div class="d-stats">${fmt(ar.listeners)} oyentes · ${fmt(ar.playcount)} escuchas</div>` : '';
    parts.push(section('Artista: ' + ar.name, stats + (ar.tags && ar.tags.length ? tagsHtml(ar.tags) : '')
      + (ar.bio || ar.bio_summary ? longText(ar.bio_summary, ar.bio) : '')));
    if (ar.similar && ar.similar.length) parts.push(section('Artistas similares', tagsHtml(ar.similar.map(s => s.name))));
  }
  const links = [extLink(lf.url, 'Disco en Last.fm'), extLink(ar.url, 'Artista en Last.fm'),
    extLink(a.links.discogs, 'Discogs'), extLink(a.links.musicbrainz, 'MusicBrainz')].filter(Boolean);
  if (links.length) parts.push(`<div class="d-section" style="gap:5px">${links.join('')}</div>`);
  const errs = (d.errors || []).map(e => `<div class="d-err">⚠ ${esc(e)}</div>`).join('');
  el.innerHTML = errs + (parts.join('') || '<div class="d-loading">Last.fm no tiene datos de este disco</div>');
}

// ── Cola y enlaces propios ────────────────────────────────────────────────────
function renderQueue() {
  const el = document.getElementById('d-queue');
  if (!el || !CUR) return;
  const own = CUR.custom && CUR.custom.length;
  const items = QUEUE.map((q, i) => {
    const label = q.title || (q.list ? 'Lista de YouTube' : q.url);
    return `<div class="q-item${i === qi ? ' playing' : ''}${q.bad ? ' bad' : ''}" data-i="${i}" title="${esc(q.url)}">${i === qi ? '▶' : (i + 1) + '.'} ${esc(label)}</div>`;
  }).join('');
  el.innerHTML = `<div id="d-queue-head"><span>Vídeos${QUEUE.length ? ' (' + QUEUE.length + ')' : ''}${own ? ' · enlaces propios' : ''}</span>`
    + `<button class="q-btn" id="q-refresh" title="Volver a buscar con yt-dlp y a consultar Last.fm">↻</button>`
    + `<button class="q-btn" id="q-edit" title="Aportar otro enlace">✎ Otro enlace</button></div>`
    + `<div id="q-list">${items}</div><div id="q-form" hidden>`
    + `<textarea id="q-urls" placeholder="Enlaces de YouTube (vídeo o lista), uno por línea"></textarea>`
    + `<div id="q-form-row"><span id="q-msg"></span>`
    + (own ? `<button class="q-btn" id="q-reset" title="Volver a los vídeos de yt-dlp">Quitar</button>` : '')
    + `<button class="q-btn" id="q-cancel">Cancelar</button><button class="q-btn" id="q-save">Guardar</button></div>`
    + `<div id="q-hint">Sustituyen a los vídeos encontrados para este disco.</div></div>`
    + `<div class="q-keys">N siguiente · B anterior · +/− ±10 s</div>`;
  el.querySelectorAll('.q-item').forEach(d => d.addEventListener('click', () => playIndex(+d.dataset.i)));
  document.getElementById('q-edit').addEventListener('click', () => openLinkForm(true));
  document.getElementById('q-refresh').addEventListener('click', e => {
    e.currentTarget.textContent = '…';
    e.currentTarget.disabled = true;
    loadDetail(CUR, playToken, true);
  });
  document.getElementById('q-cancel').addEventListener('click', () => { document.getElementById('q-form').hidden = true; });
  document.getElementById('q-save').addEventListener('click', saveLinks);
  if (own) document.getElementById('q-reset').addEventListener('click', () => storeLinks([]));
}

function openLinkForm(focus) {
  const form = document.getElementById('q-form');
  if (!form) return;
  form.hidden = false;
  const ta = document.getElementById('q-urls');
  ta.value = (CUR.custom || []).join('\n');
  if (focus) ta.focus();
}

function saveLinks() {
  const lines = document.getElementById('q-urls').value.split(/\s+/).filter(Boolean);
  const bad = lines.find(u => !ytParse(u)), msg = document.getElementById('q-msg');
  if (!lines.length) { msg.className = 'err'; msg.textContent = 'Pega al menos un enlace'; return; }
  if (bad) { msg.className = 'err'; msg.textContent = 'No es de YouTube: ' + bad; return; }
  storeLinks(lines);
}

async function storeLinks(urls) {
  const a = CUR, r = await api('/api/links', {key: a.key, urls});
  if (r.error) {
    const msg = document.getElementById('q-msg');
    if (msg) { msg.className = 'err'; msg.textContent = r.error; }
    return;
  }
  a.custom = r.urls;
  if (CUR === a) select(a, null, 0);
}

// ── Controles ─────────────────────────────────────────────────────────────────
['search', 'tag-filter', 'sort'].forEach(id =>
  document.getElementById(id).addEventListener(id === 'search' ? 'input' : 'change', applyFilter));

document.addEventListener('keydown', e => {
  const t = document.activeElement;
  if (t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable)) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if ((k === 'n' || k === 'b') && CUR) { e.preventDefault(); if (k === 'n') nextTrack(); else prevTrack(); return; }
  if ((e.key === '+' || e.key === '-') && ytPlayer && ytPlayer.getCurrentTime) {
    e.preventDefault();
    ytPlayer.seekTo(Math.max(0, ytPlayer.getCurrentTime() + (e.key === '+' ? 10 : -10)), true);
    return;
  }
  if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
  e.preventDefault();
  const cards = [...document.querySelectorAll('.card')];
  if (!cards.length) return;
  const idx = cards.findIndex(c => +c.dataset.uid === current);
  const next = (e.key === 'ArrowRight' || e.key === 'ArrowDown') ? Math.min(idx + 1, cards.length - 1) : Math.max(idx - 1, 0);
  const item = ALL[+cards[next].dataset.uid];
  if (item) select(item, cards[next]);
});

// Un clic en el vídeo se lleva el foco al iframe; se devuelve para que N/B sigan funcionando
window.addEventListener('blur', () => setTimeout(() => {
  const f = document.activeElement;
  if (f && f.tagName === 'IFRAME' && f.closest('#d-player-wrap')) f.blur();
}, 0));

// ── Selector de secciones ─────────────────────────────────────────────────────
// null = todas. Las nuevas búsquedas parten de la última elección.
function typePicker(selected) {
  return `<div class="t-quick"><a data-all="1">todas</a> · <a data-all="0">ninguna</a></div><div class="t-picker">`
    + RTYPES.map(t => `<label class="t-opt"><input type="checkbox" value="${esc(t)}"${!selected || selected.includes(t) ? ' checked' : ''}> ${esc(RTYPE_LABEL[t])}</label>`).join('')
    + `</div>`;
}
function bindPicker(root) {
  root.querySelectorAll('.t-quick a').forEach(a => a.addEventListener('click', () =>
    root.querySelectorAll('.t-opt input').forEach(i => { i.checked = a.dataset.all === '1'; })));
}
function pickedTypes(root) {
  const v = [...root.querySelectorAll('.t-opt input:checked')].map(i => i.value);
  return {none: !v.length, types: v.length === RTYPES.length ? null : v};
}
function lastTypes() {
  try { return JSON.parse(localStorage.getItem('sellos-types')); } catch (e) { return null; }
}

document.getElementById('sec-edit').addEventListener('click', e => {
  e.stopPropagation();
  grpMenu.innerHTML = `<div class="gm-title">Secciones de «${esc(LABEL.name)}»</div>` + typePicker(LABEL.types)
    + `<div class="t-actions"><span class="t-msg"></span><button class="q-btn t-apply">Aplicar</button></div>`;
  bindPicker(grpMenu);
  grpMenu.querySelector('.t-apply').addEventListener('click', async () => {
    const p = pickedTypes(grpMenu);
    if (p.none) { grpMenu.querySelector('.t-msg').textContent = 'Elige al menos una'; return; }
    grpMenu.hidden = true;
    const r = await runFetch({slug: LABEL.slug, types: p.types});
    if (r) LABEL.types = p.types;
  });
  grpMenu.hidden = false;
  const r = e.currentTarget.getBoundingClientRect();
  grpMenu.style.left = Math.min(r.left, innerWidth - grpMenu.offsetWidth - 8) + 'px';
  grpMenu.style.top = (r.bottom + 4) + 'px';
});

// ── Añadir un sello: buscar, elegir y lanzar ──────────────────────────────────
const fetchBtn = document.getElementById('fetch-btn'), fetchInput = document.getElementById('fetch-id'), cands = document.getElementById('cands');
const SOURCE_NAME = {mb: 'MusicBrainz', discogs: 'Discogs'};

async function searchLabel() {
  const q = fetchInput.value.trim();
  if (!q) { fetchInput.focus(); return; }
  fetchBtn.disabled = true;
  cands.hidden = false;
  cands.innerHTML = '<div class="c-msg">Buscando…</div>';
  let r;
  try { r = await api('/api/search?' + new URLSearchParams({q})); }
  catch (e) { r = {candidates: [], errors: ['Sin respuesta del servidor']}; }
  fetchBtn.disabled = false;
  renderCandidates(q, r);
}

function renderCandidates(q, r) {
  const list = r.candidates || [];
  const errs = [...(r.errors || []), ...(r.error ? [r.error] : [])].map(e => `<div class="c-msg c-err">⚠ ${esc(e)}</div>`).join('');
  if (!list.length) {
    cands.innerHTML = errs + '<div class="c-msg">Ningún sello encontrado</div>';
    return;
  }
  const bySource = ['mb', 'discogs'].map(s => {
    const items = list.map((c, i) => [c, i]).filter(([c]) => c.source === s);
    if (!items.length) return '';
    return `<div class="c-src">${SOURCE_NAME[s]}</div>` + items.map(([c, i]) =>
      `<label class="c-item"><input type="checkbox" data-i="${i}"${c.preselected ? ' checked' : ''}>`
      + `<span><b>${esc(c.name)}</b>${c.detail ? `<small>${esc(c.detail)}</small>` : ''}</span>`
      + (safeUrl(c.url) ? `<a href="${esc(c.url)}" target="_blank" rel="noopener" title="Ver ficha">↗</a>` : '') + `</label>`).join('');
  }).join('');
  cands.innerHTML = errs + `<div class="c-msg">Marca el sello en cada fuente; los homónimos son sellos distintos.</div>${bySource}`
    + `<div class="c-src">Secciones a añadir</div>${typePicker(lastTypes())}<div class="c-msg c-err" id="c-types-msg"></div>`
    + `<div class="c-actions"><button class="q-btn" id="c-cancel">Cancelar</button><button id="c-add">Añadir</button></div>`;
  bindPicker(cands);
  document.getElementById('c-cancel').addEventListener('click', () => { cands.hidden = true; });
  document.getElementById('c-add').addEventListener('click', async () => {
    const chosen = [...cands.querySelectorAll('.c-item input:checked')].map(i => list[+i.dataset.i]);
    const picked = pickedTypes(cands), warn = document.getElementById('c-types-msg');
    if (!chosen.length) { warn.textContent = 'Marca al menos un sello'; return; }
    if (picked.none) { warn.textContent = 'Marca al menos una sección'; return; }
    localStorage.setItem('sellos-types', JSON.stringify(picked.types));
    const body = {name: (chosen.find(c => c.source === 'mb') || chosen[0]).name.replace(/\s*\(\d+\)$/, ''),
      mb: chosen.filter(c => c.source === 'mb').map(c => c.id),
      discogs: chosen.filter(c => c.source === 'discogs').map(c => c.id), types: picked.types};
    cands.hidden = true;
    fetchInput.value = '';
    const res = await runFetch(body);
    if (res && res.slug) { await loadList(); loadSource(res.slug); }
  });
}

fetchBtn.addEventListener('click', searchLabel);
fetchInput.addEventListener('keydown', e => { if (e.key === 'Enter') searchLabel(); });

// ── Progreso ──────────────────────────────────────────────────────────────────
const jobFloat = document.getElementById('job-float');
let floatDismissed = false, floatHideTimer = null, pollCount = 0;
document.getElementById('jf-close').addEventListener('click', () => { floatDismissed = true; jobFloat.hidden = true; });

function renderStage(el, title, s, names) {
  const pct = s.total ? s.done / s.total : null;
  const q = s.queue.length ? `En cola (${s.queue.length}): ` + s.queue.map(c => names[c] || c).join(' · ') : '';
  const paused = s.paused_until ? ` · reintento a las ${new Date(s.paused_until * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}` : '';
  const problem = s.error || s.warning;
  const fill = !s.running ? (problem ? 'err' : 'done') : s.paused_until ? 'err' : '';
  el.innerHTML = `<div class="jf-stage-title">${title}</div>`
    + (s.name ? `<div class="jf-name"></div>
       <div class="jf-phase"><span>${esc(s.phase || '')}${s.total ? `: ${s.done} / ${s.total}` : ''}${paused}</span><span>${pct != null ? Math.round(pct * 100) + '%' : ''}</span></div>
       <div class="jf-bar"><div class="jf-fill ${fill}" style="width:${(!s.running && !problem ? 1 : pct || 0) * 100}%"></div></div>
       <div class="jf-current"></div>` : '<div class="jf-phase">En espera</div>')
    + (q ? '<div class="jf-queue"></div>' : '');
  if (s.name) {
    el.querySelector('.jf-name').textContent = s.name;
    el.querySelector('.jf-current').textContent = problem || (s.current.length ? '♪ ' + s.current.join(' · ') : '');
    el.querySelector('.jf-current').classList.toggle('problem', !!problem);
  }
  if (q) el.querySelector('.jf-queue').textContent = q;
}

function updateFloat(s) {
  if (floatDismissed) return;
  clearTimeout(floatHideTimer);
  jobFloat.hidden = false;
  const stages = [s.data, s.youtube];
  const idle = !s.running, bad = idle && stages.some(x => x.error || x.warning);
  const title = document.getElementById('jf-title');
  title.className = idle ? (bad ? 'err' : 'done') : '';
  title.textContent = !idle ? (s.youtube.paused_until ? '⏸ YouTube en pausa' : '⏳ Añadiendo…')
    : bad ? '✗ ' + (stages.some(x => x.error) ? 'Error' : 'Incompleto') : '✓ Completado';
  const names = Object.fromEntries(SOURCES.map(x => [x.key, x.name]));
  renderStage(document.getElementById('jf-data'), 'Catálogo y Last.fm', s.data, names);
  renderStage(document.getElementById('jf-youtube'), `YouTube (${s.workers} a la vez)`, s.youtube, names);
  document.getElementById('jf-log').textContent = (s.log || '').split('\n').slice(-6).join('\n');
  if (idle && !bad) floatHideTimer = setTimeout(() => { jobFloat.hidden = true; }, 8000);
}

async function runFetch(body) {
  const r = await api('/api/fetch', body);
  if (r.error) { alert(r.error); return null; }
  floatDismissed = false;
  pollJob(0);
  return r;
}

function pollJob(delay = 1500) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    const s = await api('/api/job');
    if (s.data.name || s.youtube.name) updateFloat(s);
    const pending = s.running;
    const changed = lastFinished !== null && s.finished !== lastFinished;
    // Cada 6 s se refresca lo que se está rellenando, y siempre al terminar
    pollCount++;
    if (changed || (pending && pollCount % 4 === 0)) {
      [SOURCES] = await Promise.all([api('/api/sources')]);
      renderSidebar();
      if (SRC && (changed || SRC === s.data.slug || SRC === s.youtube.slug)) await mergeLabel();
    }
    lastFinished = s.finished;
    if (pending) pollJob();
  }, delay);
}

// ── Arranque ──────────────────────────────────────────────────────────────────
loadList();
api('/api/job').then(s => { lastFinished = s.finished; if (s.running) pollJob(); });
