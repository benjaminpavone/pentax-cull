// Viewer / rating. Platform-independent: all data goes through window.api (api.js),
// all texts through t() (i18n.js).
'use strict';

const $ = (id) => document.getElementById(id);
const S = {
  source: null,          // last api.state()
  photos: [], list: [], idx: 0,
  ratings: {},           // ratings.json
  infos: new Map(),      // id -> info (fileRating / datetime / crop)
  choosing: false,       // native folder picker open
};
const cache = new Map(); // id -> Promise<{url, info, ms}>
// browser memory: compressed JPEG blobs only (~0.5 MB each), so ~30 photos is cheap
const PREFETCH_AHEAD = 8, PREFETCH_BEHIND = 3, KEEP = 30;
let navGen = 0;          // bumped on every photo change: cancels stale prefetches
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};

/* ================================================================ settings: language, theme, background */

const BGS = ['theme', 'black', 'grey', 'white'];
const prefs = {
  lang: store.get('lang') || 'auto',
  theme: store.get('theme') || 'dark',
  bg: store.get('bg') || 'theme',
};

function applyPrefs() {
  i18n.set(prefs.lang);
  document.documentElement.dataset.theme = prefs.theme;
  if (prefs.bg === 'theme') delete document.documentElement.dataset.bg;
  else document.documentElement.dataset.bg = prefs.bg;
  $('setLang').value = prefs.lang; $('setTheme').value = prefs.theme; $('setBg').value = prefs.bg;
  refreshTexts();
}

function setPref(k, v) { prefs[k] = v; store.set(k, v); applyPrefs(); }

$('btnSettings').onclick = () => {
  const c = S.source?.camera || {};
  $('setSsid').value = c.cameraSsid || '';
  $('setPass').value = '';
  $('setPass').placeholder = c.hasPassword ? t('passSaved') : '';
  $('dlgSettings').showModal();
  if (!window.AndroidBridge) { refreshCache(); refreshInbox(); }
};

// Mac: inbox folder for the phone's exported ratings (merged by the server, see packages/desktop/inbox.py)
function showInbox(st) {
  $('inboxDir').textContent = st.dir || t('none');
  $('inboxDir').title = st.dir || '';
  const l = st.last;
  $('inboxStatus').textContent = st.error ? t('error', { err: st.error })
    : l ? t('inboxLast', { file: l.file, added: l.added, updated: l.updated, at: l.at.replace('T', ' ') })
    : st.dir ? t('inboxEmpty') : t('inboxHelp');
}
async function refreshInbox() { try { showInbox(await api.inboxStatus()); } catch {} }
$('inboxChange').onclick = async () => {
  const r = await api.chooseFolder(t('inboxTitle'), S.source?.inbox?.dir || null);
  if (r.ok && r.path) { showInbox(await api.inboxSet(r.path)); setRatings(await api.ratings()); renderStars(); }
  else if (r.busy) toast(t('chooserBusy'), true);
};

// Android: export ratings
$('btnExport').onclick = async () => {
  const r = await api.exportRatings();
  if (!r.ok) toast(t('error', { err: r.error }), true);
};
window.onRatingsExported = (ok, name, n) => toast(ok ? t('exported', { name, n }) : t('exportFailed', { err: name }), !ok);
// Android: camera Wi-Fi settings (saved when a field is left)
$('setSsid').onchange = $('setPass').onchange = async () => {
  const r = await api.cameraConfig($('setSsid').value, $('setPass').value);
  if (r.ok) { S.source = r; renderSource(r); $('setPass').value = ''; $('setPass').placeholder = r.camera.hasPassword ? t('passSaved') : ''; toast(t('saved')); }
};

// preview cache on disk (size cap = least recently used previews are deleted)
const fmtSize = (b) => b >= 1024 ** 3 ? (b / 1024 ** 3).toFixed(1) + ' GB' : Math.round(b / 1024 ** 2) + ' MB';
function showCache(c) {
  if (!c || c.bytes === undefined) return;
  $('cacheUsage').textContent = t('cacheUsage', { size: fmtSize(c.bytes), n: c.files, limit: fmtSize(c.limitBytes) });
  const sel = $('setCacheLimit');
  if (![...sel.options].some(o => +o.value === c.limitBytes)) sel.add(new Option(fmtSize(c.limitBytes), c.limitBytes));
  sel.value = String(c.limitBytes);
}
async function refreshCache() { try { showCache(await api.cacheStats()); } catch {} }
$('setCacheLimit').onchange = async (e) => showCache(await api.cacheSetLimit(+e.target.value));
$('cacheClear').onclick = async () => { showCache(await api.cacheClear()); toast(t('cacheCleared')); };
$('setClose').onclick = () => $('dlgSettings').close();
$('setLang').onchange = (e) => setPref('lang', e.target.value);
$('setTheme').onchange = (e) => setPref('theme', e.target.value);
$('setBg').onchange = (e) => setPref('bg', e.target.value);

function cycleBg() {
  setPref('bg', BGS[(BGS.indexOf(prefs.bg) + 1) % BGS.length]);
  toast(t('bgToast', { bg: t({ theme: 'bgTheme', black: 'black', grey: 'grey', white: 'white' }[prefs.bg]) }));
}

// re-renders texts built in JS (static ones are handled by i18n.apply)
function refreshTexts() {
  if (window.AndroidBridge) $('dlReveal').textContent = t('revealAndroid');
  if (S.source) renderSource(S.source);
  renderGroups();
  renderStars();
  if (S.list.length) {
    const info = S.infos.get(S.list[S.idx].id);
    if (info) $('exif').textContent = fmtExif(info);
  }
}

/* ================================================================ source */

async function refreshState() {
  let s;
  try { s = await api.state(); } catch { $('net').textContent = t('serverDown'); return; }
  const prev = S.source;
  S.source = s;
  renderSource(s);
  if (prev && s.ratingsMtime && prev.ratingsMtime && s.ratingsMtime !== prev.ratingsMtime) reloadRatings(s.inbox);
  if (s.type === 'camera') {
    const c = s.camera;
    if (!c.busy && c.error && !c.reachable && !c.onCameraWifi) msg(t('failed', { err: c.error }));
    else if (!c.reachable && !S.list.length && !c.busy) msg(t('clickConnect'));
    // load the list once the camera is reachable, but never reload it (and jump back to photo 1)
    // just because one status check failed while the camera was busy
    const becameReachable = c.reachable && !(prev?.type === 'camera' && prev.camera.reachable);
    if (becameReachable && !S.photos.length) loadList();
  }
  return s;
}

async function reloadRatings(inb) {
  try { setRatings(await api.ratings()); } catch { return; }
  renderStars();
  if (G.on) for (const tile of $('grid').children) renderTile(tile);
  const l = inb?.last;
  if (l && (l.added || l.updated) && l.at !== S.lastImportToast) {
    S.lastImportToast = l.at;
    toast(t('imported', { file: l.file, added: l.added, updated: l.updated }));
  }
}

function renderSource(s) {
  const isCam = s.type === 'camera';
  for (const b of $('srcSeg').children) b.classList.toggle('active', b.dataset.src === s.type);
  $('camBox').classList.toggle('hidden', !isCam);
  $('folderBox').classList.toggle('hidden', isCam);
  document.body.classList.toggle('can-zoom', !isCam);
  if (isCam) {
    const c = s.camera;
    $('dot').classList.toggle('on', !!c.reachable);
    $('net').textContent = c.reachable ? t('connectedTo', { ssid: c.cameraSsid }) : t('wifi', { ssid: c.ssid || t('none') });
    const btn = $('btnConn');
    btn.disabled = !!c.busy;
    btn.textContent = c.busy === 'connect' ? t('connecting') : c.busy === 'disconnect' ? t('disconnecting')
      : c.onCameraWifi ? t('disconnect') : t('connect');
    btn.classList.toggle('primary', !c.busy && !c.onCameraWifi);
    if (c.busy === 'connect' && c.waited > 3) msg(t('waitingCamera', { s: c.waited }), true);
  } else {
    $('folderPath').textContent = s.label;
    $('folderPath').title = s.label;
  }
}

async function setSource(type, path) {
  if (S.choosing) return;
  const choosing = type === 'folder' && !path;
  if (choosing) {
    S.choosing = true;
    setChoosingUi(true);
    msg(t('chooseFolderMsg'));
  }
  let r;
  try { r = await api.setSource(type, path, t('pickFolder')); } finally {
    if (choosing) { S.choosing = false; setChoosingUi(false); }
  }
  if (!r.ok) {
    if (r.busy) toast(t('chooserBusy'), true);
    if (r.error) toast(r.error, true);
    if (choosing) msg(S.list.length ? '' : t('noFolder'));
    return;
  }
  if (type === 'folder') store.set('folder', r.label);
  store.set('source', type);
  resetList();
  await refreshState();
  if (type === 'folder') {
    // on by default in folder mode, except on a memory card / external disk (can be turned on)
    $('writeFile').checked = !S.source.removable;
    if (S.source.removable) toast(t('removableNoWrite'));
  }
  if (type === 'folder') loadList();
}

function setChoosingUi(on) {
  for (const b of $('srcSeg').children) b.disabled = on;
  $('btnFolder').disabled = on;
}

$('srcSeg').onclick = (e) => {
  const src = e.target.closest('button')?.dataset.src;
  if (!src) return;
  if (src === 'folder') {
    if (S.source?.type === 'folder') return;          // already in folder mode: use “Change…”
    setSource('folder', store.get('folder'));         // last folder, or the picker
  } else if (S.source?.type !== 'camera') setSource('camera');
};
$('btnFolder').onclick = () => setSource('folder');   // no path: native picker

$('btnConn').onclick = async () => {
  const goingIn = !S.source.camera.onCameraWifi;
  $('btnConn').disabled = true;
  msg(goingIn ? t('joining') : t('leaving'), true);
  await (goingIn ? api.cameraConnect() : api.cameraDisconnect());
  for (let i = 0; i < 150; i++) {                     // the server works in the background
    const st = await refreshState();
    if (st && !st.camera.busy) break;
    await sleep(1500);
  }
  if (!goingIn) { resetList(); msg(t('disconnected')); }
};

/* ================================================================ photo list */

function resetList() {
  zoomOut();
  dropFull();
  for (const p of cache.values()) p.then(v => URL.revokeObjectURL(v.url)).catch(() => {});
  cache.clear(); S.infos.clear();
  S.photos = []; S.list = []; S.idx = 0; navGen++;
  gridReset();
  $('photo').removeAttribute('src');
  renderGroups();
  for (const id of ['counter', 'fname', 'exif', 'timing']) $(id).textContent = '';
  $('btnDownload').disabled = true;
}

async function loadList() {
  msg(t('listing'), true);
  try { S.photos = await api.photos(); } catch (e) { return msg(t('error', { err: e.message })); }
  renderGroups(store.get('group:' + S.source.type));
  $('btnDownload').disabled = !S.photos.length;
  applyGroup();
}

// Folder selector: camera card folders (100_1809, 101_1909…) or sub-folders.
// Hidden when there is only one folder.
function renderGroups(wanted) {
  const sel = $('groupSel');
  const keep = wanted ?? sel.value;
  const groups = [...new Set(S.photos.map(p => p.group))];
  sel.innerHTML = `<option value="*">${esc(t('allFolders', { n: S.photos.length }))}</option>` +
    groups.map(g => `<option value="${esc(g)}">${esc(g)} (${S.photos.filter(p => p.group === g).length})</option>`).join('');
  sel.value = groups.includes(keep) ? keep : '*';
  sel.classList.toggle('hidden', groups.length < 2);
}

$('groupSel').onchange = () => {
  store.set('group:' + S.source.type, $('groupSel').value);
  $('groupSel').blur();                              // give arrow keys back to the viewer
  applyGroup();
};

function applyGroup() {
  const g = $('groupSel').value;
  S.list = S.photos.filter(p => g === '*' || p.group === g);
  if (G.on) { S.idx = 0; buildGrid(); } else show(0);
}

/* ================================================================ display */

const DEG = { 3: 180, 6: 90, 8: 270 };

// Draws the image cropped (cw x ch from the top-left corner) and rotated (EXIF orientation) into a canvas.
function drawRotated(bmp, deg, cw, ch, canvas) {
  const swap = deg === 90 || deg === 270;
  canvas.width = swap ? ch : cw; canvas.height = swap ? cw : ch;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingQuality = 'high';
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate(deg * Math.PI / 180);
  ctx.drawImage(bmp, 0, 0, cw, ch, -cw / 2, -ch / 2, cw, ch);
  return canvas;
}

// Rotates the preview and removes the white band the K-1 II sometimes leaves on the right/bottom
// (info.cropRight / cropBottom, in full-resolution pixels).
async function prepare(blob, info) {
  const deg = DEG[info.orientation] || 0;
  const cropR = info.cropRight || 0, cropB = info.cropBottom || 0;
  if (!deg && !cropR && !cropB) return URL.createObjectURL(blob);
  const bmp = await createImageBitmap(blob);
  const k = info.fullWidth ? bmp.width / (info.fullWidth + cropR) : 0;   // preview / full-res scale
  const cw = bmp.width - (cropR && k ? Math.ceil(cropR * k) + 2 : 0);    // +2 px: light seam from downscaling
  const ch = bmp.height - (cropB && k ? Math.ceil(cropB * k) + 2 : 0);
  const c = drawRotated(bmp, deg, cw, ch, document.createElement('canvas'));
  bmp.close();
  return new Promise(res => c.toBlob(b => res(URL.createObjectURL(b)), 'image/jpeg', 0.92));
}

function load(photo) {
  if (cache.has(photo.id)) return cache.get(photo.id);
  const p = (async () => {
    const t0 = performance.now();
    // info and preview in parallel: the camera handles 2 requests at once (the server never sends more)
    const [info, blob] = await Promise.all([api.info(photo.id), api.preview(photo.id, 'screen')]);
    S.infos.set(photo.id, info);
    const url = await prepare(blob, info);
    await toneFor(photo.id, url).catch(() => {});
    return { url, info, ms: Math.round(performance.now() - t0) };
  })();
  p.catch(() => cache.delete(photo.id));
  cache.set(photo.id, p);
  return p;
}

async function prefetch(gen) {
  const around = [];
  for (let i = 1; i <= PREFETCH_AHEAD; i++) around.push(S.list[S.idx + i]);
  for (let i = 1; i <= PREFETCH_BEHIND; i++) around.push(S.list[S.idx - i]);
  const pos = new Map(S.list.map((p, i) => [p.id, i]));
  for (const [id, pr] of cache) {
    const i = pos.get(id);
    if (i === undefined || Math.abs(i - S.idx) > KEEP) { pr.then(v => URL.revokeObjectURL(v.url)).catch(() => {}); cache.delete(id); }
  }
  for (const p of around.filter(Boolean)) {
    if (gen !== navGen) return;                     // the user moved on: make room
    await load(p).catch(() => {});
  }
}

async function show(i) {
  if (!S.list.length) return msg(S.photos.length ? t('noPhotosGroup') : t('noPhotos'));
  zoomOut();
  S.idx = Math.max(0, Math.min(i, S.list.length - 1));
  if (FULL.id && FULL.id !== S.list[S.idx].id) dropFull();
  const gen = ++navGen;
  const photo = S.list[S.idx];
  $('counter').textContent = `${S.idx + 1} / ${S.list.length}`;
  $('fname').textContent = photo.id;
  renderStars();
  const wasCached = cache.has(photo.id);
  if (!wasCached) { document.body.classList.add('loading'); msg(t('loading'), true); }
  try {
    const { url, info, ms } = await load(photo);
    if (gen !== navGen) return;                     // already on another photo
    applyTone(photo);
    $('photo').src = url;
    document.body.classList.remove('loading');
    msg('');
    $('exif').textContent = fmtExif(info);
    $('timing').textContent = wasCached ? t('preloaded') : `${ms} ms`;
    renderStars();
  } catch (e) {
    if (gen === navGen) { document.body.classList.remove('loading'); $('photo').removeAttribute('src'); msg(`${photo.id}: ${e.message}`); }
  }
  if (gen === navGen) { prefetch(gen); scheduleFullPreload(photo, gen); }
}

function fmtExif(i) {
  if (!i) return '';
  const [a, b] = String(i.tv || '').split('.');
  const tv = !a ? '' : !b ? `${a}s` : a === '1' ? `1/${b}s` : b === '1' ? `${a}s` : `${a}/${b}s`;
  return [(i.datetime || '').replace('T', ' '), i.av && `f/${i.av}`, tv, i.sv && `ISO ${i.sv}`].filter(Boolean).join(' · ');
}

/* ================================================================ 100% zoom (folder mode) */
// On click: the screen preview is enlarged at once (blurry), then replaced by the full resolution
// once decoded. The full resolution is shown as is and rotated with CSS (GPU),
// and preloaded when staying ~1 s on a photo.

const Z = { on: false, id: null, fx: 0.5, fy: 0.5, W: 0, H: 0 };
const FULL = { id: null, url: null, ready: null };   // a single full-res image in memory
let fullTimer;

function fullFor(photo) {
  if (FULL.id === photo.id && FULL.ready) return FULL.ready;
  dropFull();
  FULL.id = photo.id;
  const ready = (async () => {
    const blob = await api.preview(photo.id, 'full');
    const url = URL.createObjectURL(blob);
    if (FULL.ready !== ready) { URL.revokeObjectURL(url); throw new Error('aborted'); }
    FULL.url = url;
    const img = new Image();
    img.decoding = 'async';
    img.src = url;
    await img.decode();                              // decoded off the main thread
    return { img, w: img.naturalWidth, h: img.naturalHeight };
  })();
  FULL.ready = ready;
  ready.catch(() => { if (FULL.ready === ready) dropFull(); });
  return ready;
}

function dropFull() {
  if (FULL.url) URL.revokeObjectURL(FULL.url);
  FULL.id = FULL.url = FULL.ready = null;
}

function scheduleFullPreload(photo, gen) {
  clearTimeout(fullTimer);
  if (S.source?.type !== 'folder') return;
  fullTimer = setTimeout(() => { if (gen === navGen && !Z.on) fullFor(photo).catch(() => {}); }, 900);
}

$('photo').onclick = async (e) => {
  if (S.source?.type !== 'folder' || !$('photo').src) return;
  const photo = S.list[S.idx];
  const info = S.infos.get(photo.id) || {};
  const deg = DEG[info.orientation] || 0, swap = deg === 90 || deg === 270;
  const dpr = window.devicePixelRatio || 1;          // 100% = one image pixel per screen pixel
  const r = $('photo').getBoundingClientRect();
  Object.assign(Z, { on: true, id: photo.id,
    fx: clamp((e.clientX - r.left) / r.width), fy: clamp((e.clientY - r.top) / r.height) });

  // 1. instant enlargement of the (already rotated) screen preview
  const pw = $('photo').naturalWidth, ph = $('photo').naturalHeight;
  const factor = info.fullWidth ? info.fullWidth / (swap ? ph : pw) : 0;
  setFullEl(null);
  $('zoomLow').src = $('photo').src;
  $('zoom').classList.remove('hidden');
  if (factor > 1) {
    $('zoomLow').style.display = '';
    setZoomSize(pw * factor / dpr, ph * factor / dpr);
  } else {
    $('zoomLow').style.display = 'none';
  }
  // the enlarged screen preview is blurry: say so until the full resolution replaces it
  if (FULL.id !== photo.id || !FULL.url) msg(t('loadingFull'), true);

  // 2. full resolution
  try {
    const full = await fullFor(photo);
    if (!Z.on || Z.id !== photo.id) return;
    // useful area: without the white band the camera sometimes leaves on the right/bottom of the preview
    const cw = (full.w - (info.cropRight || 0)) / dpr, ch = (full.h - (info.cropBottom || 0)) / dpr;
    const W = swap ? ch : cw, H = swap ? cw : ch;
    setZoomSize(W, H);
    const box = document.createElement('div');         // cropping frame, rotated with CSS
    Object.assign(box.style, { width: cw + 'px', height: ch + 'px', overflow: 'hidden',
      left: (W - cw) / 2 + 'px', top: (H - ch) / 2 + 'px', transform: `rotate(${deg}deg)` });
    const img = full.img;
    Object.assign(img.style, { position: 'absolute', left: 0, top: 0, maxWidth: 'none',
      width: full.w / dpr + 'px', height: full.h / dpr + 'px' });
    box.appendChild(img);
    setFullEl(box);
    msg('');
  } catch (err) {
    if (Z.on && Z.id === photo.id) { zoomOut(); toast(t('zoomFailed', { err: err.message }), true); }
  }
};

function setFullEl(el) {
  const inner = $('zoomInner');
  for (const old of inner.querySelectorAll('.full')) if (old !== el) old.remove();
  if (el) { el.className = 'full'; inner.appendChild(el); }
}

function setZoomSize(W, H) {
  Object.assign(Z, { W, H });
  Object.assign($('zoomInner').style, { width: W + 'px', height: H + 'px' });
  panTo(Z.fx, Z.fy);
}

function panTo(fx, fy) {
  const box = $('zoom').getBoundingClientRect();
  const x = Z.W > box.width ? -(Z.W - box.width) * fx : (box.width - Z.W) / 2;
  const y = Z.H > box.height ? -(Z.H - box.height) * fy : (box.height - Z.H) / 2;
  $('zoomInner').style.transform = `translate(${x}px, ${y}px)`;
}

$('zoom').onmousemove = (e) => {
  if (!Z.on || drag0 || window.AndroidBridge) return;   // touch screens: dragged with a finger instead
  const b = $('zoom').getBoundingClientRect();
  Z.fx = clamp((e.clientX - b.left) / b.width);
  Z.fy = clamp((e.clientY - b.top) / b.height);
  panTo(Z.fx, Z.fy);
};
$('zoom').onclick = () => { if (!Z.dragged) zoomOut(); Z.dragged = false; };

// touch: drag the zoomed photo with a finger (a tap without moving zooms out)
let drag0 = null;
$('zoom').addEventListener('touchstart', (e) => {
  if (!Z.on || e.touches.length !== 1) return;
  drag0 = { x: e.touches[0].clientX, y: e.touches[0].clientY, fx: Z.fx, fy: Z.fy };
  Z.dragged = false;
}, { passive: true });
$('zoom').addEventListener('touchmove', (e) => {
  if (!drag0 || !Z.on) return;
  e.preventDefault();                                 // no page scroll / pull-to-refresh
  const box = $('zoom').getBoundingClientRect();
  const dx = e.touches[0].clientX - drag0.x, dy = e.touches[0].clientY - drag0.y;
  if (Math.abs(dx) + Math.abs(dy) > 8) Z.dragged = true;
  Z.fx = Z.W > box.width ? clamp(drag0.fx - dx / (Z.W - box.width)) : 0.5;
  Z.fy = Z.H > box.height ? clamp(drag0.fy - dy / (Z.H - box.height)) : 0.5;
  panTo(Z.fx, Z.fy);
}, { passive: false });
$('zoom').addEventListener('touchend', () => { drag0 = null; }, { passive: true });

function zoomOut() {
  if (!Z.on) return;
  Z.on = false; Z.id = null;
  $('zoom').classList.add('hidden');
  setFullEl(null);                                  // full-res stays cached (instant re-zoom)
  $('zoomLow').removeAttribute('src');
  if ($('msgText').textContent === t('loadingFull')) msg('');
}

/* ================================================================ ratings */

// The same photo may be rated under another key: "100_1511/X.DNG" on the camera / phone,
// "DCIM/100_1511/X.DNG" from the card in a reader. Fallback: same file name AND same shooting date.
let ratingIdx = null;                                  // "name|datetime" -> key, rebuilt when ratings change
function ratingKeyOf(photo) {
  if (S.ratings[photo.key]) return photo.key;
  const dt = S.infos.get(photo.id)?.datetime;
  if (!dt) return null;
  if (!ratingIdx) {
    ratingIdx = new Map();
    for (const [k, v] of Object.entries(S.ratings)) if (v.datetime) ratingIdx.set(`${k.split('/').pop().toLowerCase()}|${v.datetime}`, k);
  }
  return ratingIdx.get(`${photo.name.toLowerCase()}|${dt}`) || null;
}
function setRatings(r) { S.ratings = r; ratingIdx = null; }

function currentRating(photo) {
  const k = ratingKeyOf(photo);
  const r = k && S.ratings[k];
  if (r) return { n: r.rating, fromFile: false };
  const f = S.infos.get(photo.id)?.fileRating;
  return { n: f > 0 ? f : 0, fromFile: f > 0 };
}

function renderStars() {
  const photo = S.list[S.idx];
  const { n, fromFile } = photo ? currentRating(photo) : { n: 0 };
  $('stars').classList.toggle('from-file', !!fromFile);
  $('stars').title = fromFile ? t('ratingFromFile') : '';
  $('stars').innerHTML = [1, 2, 3, 4, 5].map(i => `<span data-n="${i}" class="${i <= n ? 'on' : ''}">★</span>`).join('');
  $('rated').textContent = t('ratedCount', { n: Object.values(S.ratings).filter(v => v && v.rating > 0).length });
}

$('stars').onclick = (e) => { const n = +e.target.dataset.n; if (n) rate(n); };

let saveChain = Promise.resolve();                  // quick successive ratings must reach the server in order

async function rate(n) {
  const photo = S.list[S.idx];
  if (!photo) return;
  if (n && n === currentRating(photo).n) n = 0;      // clicking the same rating clears it
  const datetime = S.infos.get(photo.id)?.datetime || null;
  const other = ratingKeyOf(photo);                 // same photo under another key: replaced (as on the server)
  if (other && other !== photo.key) delete S.ratings[other];
  S.ratings[photo.key] = { rating: n, datetime };   // 0 = cleared: kept, so that an older export cannot bring it back
  ratingIdx = null;
  const info = S.infos.get(photo.id);
  const writeFile = S.source.writable && $('writeFile').checked;
  if (info && writeFile) info.fileRating = n;
  renderStars();
  if (G.on) renderTile($('grid').children[S.list.indexOf(photo)]);
  if (n && $('autoAdv').checked) setTimeout(() => go(S.idx + 1), 150);
  const req = { id: photo.id, key: photo.key, rating: n, datetime, writeFile };
  const r = await (saveChain = saveChain.then(() => api.saveRating(req)).catch(e => ({ ok: false, error: e.message })));
  if (!r.ok) toast(t('saveError', { err: r.error || '?' }), true);
  else if (writeFile && r.fileOk === false) toast(t('fileNotWritten', { name: photo.name, err: r.fileError }), true);
  else if (writeFile) toast(t('fileWritten', { name: photo.name, r: n ? n + '★' : t('cleared') }));
}

function nextUnrated() {
  for (let i = S.idx + 1; i < S.list.length; i++) if (!currentRating(S.list[i]).n) return go(i);
  toast(t('noMoreUnrated'));
}

/* ================================================================ download / copy */

const DEFAULT_DEST = '~/Downloads/pentax-cull';

function dlSelection() {
  const f = $('dlFilter').value;
  if (f === 'cur') return S.list[S.idx] ? [S.list[S.idx]] : [];
  if (f === 'all') return S.list.slice();
  return S.list.filter(p => currentRating(p).n >= +f);
}

function dlRender() {
  const sel = dlSelection();
  const g = $('groupSel').value;
  $('dlScope').textContent = t('inView', { n: S.list.length, scope: g === '*' ? t('allFolders', { n: S.photos.length }) : g });
  $('dlCount').textContent = sel.length ? t('count', { n: sel.length }) : t('dlNothing');
  $('dlGo').disabled = !sel.length;
  $('dlDest').textContent = window.AndroidBridge ? t('androidDest') : store.get('dlDest') || DEFAULT_DEST;
  $('dlDest').title = $('dlDest').textContent;
}

$('btnDownload').onclick = () => {
  const isCam = S.source?.type === 'camera';
  $('dlTitle').textContent = t(isCam ? 'downloadTitle' : 'copyTitle');
  $('dlGo').textContent = t(isCam ? 'start' : 'startCopy');
  $('dlCamNote').classList.toggle('hidden', !isCam);
  const saved = store.get('dlFilter');
  if (saved) $('dlFilter').value = saved;
  dlRender();
  $('dlgDownload').showModal();
};
$('dlFilter').onchange = () => { store.set('dlFilter', $('dlFilter').value); dlRender(); };
$('dlNo').onclick = () => $('dlgDownload').close();
$('dlChange').onclick = async () => {
  const r = await api.chooseFolder(t('dest'), store.get('dlDest') || null);
  if (r.ok && r.path) { store.set('dlDest', r.path); dlRender(); }
  else if (r.busy) toast(t('chooserBusy'), true);
};
$('dlGo').onclick = async () => {
  const items = dlSelection().map(p => ({ id: p.id, rating: currentRating(p).n }));
  const r = await api.download(items, store.get('dlDest') || DEFAULT_DEST, $('dlWrite').checked);
  if (!r.ok) return toast(r.busy ? t('dlBusy') : t('error', { err: r.error }), true);
  $('dlgDownload').close();
  pollDownload();
};

let dlTimer;
async function pollDownload() {
  clearTimeout(dlTimer);
  let st;
  try { st = await api.downloadStatus(); } catch { return; }
  if (!st.total && !st.running) return;
  $('dlBar').classList.remove('hidden');
  if (st.running) {
    const pct = st.size ? Math.round(100 * st.bytes / st.size) : 0;
    $('dlText').textContent = t('dlProgress', { done: st.done + 1, total: st.total, name: st.current || '', pct });
    $('dlProg').value = st.total ? (st.done + (st.size ? st.bytes / st.size : 0)) / st.total : 0;
    $('dlProg').classList.remove('hidden');
    $('dlCancel').classList.remove('hidden');
    $('dlReveal').classList.add('hidden');
    dlTimer = setTimeout(pollDownload, 700);
  } else {
    $('dlText').textContent = st.cancelled ? t('dlCancelled')
      : t('dlDone', { ok: st.downloaded, skip: st.skipped, fail: st.failed.length });
    $('dlText').title = st.failed.map(f => `${f.name}: ${f.error}`).join('\n');
    $('dlProg').classList.add('hidden');
    $('dlCancel').classList.add('hidden');
    $('dlReveal').classList.remove('hidden');
  }
}
$('dlCancel').onclick = () => api.downloadCancel();
$('dlReveal').onclick = () => { api.downloadReveal(); $('dlBar').classList.add('hidden'); };

/* ================================================================ auto tone (display only) */
// Like a simple "Auto" in Lightroom: black/white points from the histogram (0.3 % / 99.8 %, stretch limited to
// ~2x), a gamma that brings the median towards mid-grey. Computed per photo in a few ms on a 256 px copy;
// shown through an SVG filter (photo, zoom) or baked into the grid thumbnails. Files are never changed.

const TONE = { on: store.get('autoTone') === '1', params: new Map() };   // id -> { lo, hi, g, table }
const MIN_RANGE = 128, TARGET_MID = 0.40, SAT = 1.0;

async function toneFor(id, url) {
  if (TONE.params.has(id)) return TONE.params.get(id);
  const img = new Image();
  img.src = url;
  await img.decode();
  const w = 256, h = Math.max(1, Math.round(w * img.naturalHeight / img.naturalWidth));
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data, hist = new Uint32Array(256);
  for (let i = 0; i < d.length; i += 4) hist[(d[i] * 77 + d[i + 1] * 150 + d[i + 2] * 29) >> 8]++;
  const total = d.length / 4, pct = (p) => { let s = 0; for (let v = 0; v < 256; v++) { s += hist[v]; if (s >= p * total) return v; } return 255; };
  let lo = pct(0.003), hi = pct(0.998);
  if (hi - lo < MIN_RANGE) {                     // stretch at most ~2x: dark or flat images stay natural
    const room = MIN_RANGE - (hi - lo), down = Math.min(lo, Math.round(room * lo / (lo + 255 - hi || 1)));
    lo -= down; hi = Math.min(255, hi + room - down);
  }
  const mid = Math.min(0.9, Math.max(0.05, (pct(0.5) - lo) / (hi - lo)));
  const g = Math.min(1.25, Math.max(0.6, Math.log(TARGET_MID) / Math.log(mid)));   // < 1 brightens, > 1 darkens
  const curve = (x) => Math.pow(Math.min(1, Math.max(0, (x * 255 - lo) / (hi - lo))), g);
  const table = Array.from({ length: 33 }, (_, i) => curve(i / 32).toFixed(4)).join(' ');
  const p = { lo, hi, g, table, curve };
  TONE.params.set(id, p);
  if (TONE.params.size > 2000) TONE.params.delete(TONE.params.keys().next().value);
  return p;
}

// photo + zoom: one shared SVG filter, set to the current photo's curve
function applyTone(photo) {
  const p = TONE.on && photo && TONE.params.get(photo.id);
  if (p) for (const id of ['toneR', 'toneG', 'toneB']) $(id).setAttribute('tableValues', p.table);
  const f = p ? 'url(#tone)' : '';
  $('photo').style.filter = f;
  $('zoomInner').style.filter = f;
}

function bakeTone(ctx, w, h, p) {
  const lut = new Uint8ClampedArray(256);
  for (let v = 0; v < 256; v++) lut[v] = Math.round(255 * p.curve(v / 255));
  const im = ctx.getImageData(0, 0, w, h), d = im.data;
  for (let i = 0; i < d.length; i += 4) {
    const r = lut[d[i]], gg = lut[d[i + 1]], b = lut[d[i + 2]], y = 0.299 * r + 0.587 * gg + 0.114 * b;
    d[i] = y + (r - y) * SAT; d[i + 1] = y + (gg - y) * SAT; d[i + 2] = y + (b - y) * SAT;   // same saturation as the filter
  }
  ctx.putImageData(im, 0, 0);
}

function setAutoTone(on) {
  TONE.on = on;
  store.set('autoTone', on ? '1' : '0');
  $('btnAuto').classList.toggle('active', on);
  applyTone(S.list[S.idx]);
  if (G.on) {                                     // thumbnails are baked: rebuild them
    for (const u of G.thumbs.values()) URL.revokeObjectURL(u);
    G.thumbs.clear();
    buildGrid();
  }
  toast(t(on ? 'autoToneOn' : 'autoToneOff'));
}
$('btnAuto').onclick = () => { setAutoTone(!TONE.on); $('btnAuto').blur(); };
$('btnAuto').classList.toggle('active', TONE.on);

/* ================================================================ grid (contact sheet) */
// Thumbnails are made in the page from the screen preview (rotated + cropped by load()), loaded as tiles
// scroll into view. Only the small thumbnails stay in memory.

const G = { on: false, cols: Math.min(10, Math.max(2, +store.get('gridCols') || 5)), thumbs: new Map(),
            queue: [], running: 0, obs: null };
const THUMB_PX = 960;                                // long side: sharp enough for 4+ columns on a Retina screen

// moves within the current view (grid or single photo)
const go = (i) => G.on ? gridSelect(i) : show(i);

function gridToggle(on = !G.on) {
  if (on && !S.list.length) return;
  G.on = on;
  $('grid').classList.toggle('hidden', !on);
  $('btnGrid').classList.toggle('active', on);
  if (on) { zoomOut(); navGen++; document.body.classList.remove('loading'); msg(''); buildGrid(); }
  else { G.obs?.disconnect(); G.queue = []; show(S.idx); }
}

function gridReset() {
  G.obs?.disconnect(); G.queue = [];
  for (const u of G.thumbs.values()) URL.revokeObjectURL(u);
  G.thumbs.clear();
  $('grid').innerHTML = '';
  if (G.on) { G.on = false; $('grid').classList.add('hidden'); $('btnGrid').classList.remove('active'); }
}

function gridCols(n) {
  G.cols = Math.min(10, Math.max(2, n));
  store.set('gridCols', G.cols);
  $('grid').style.setProperty('--cols', G.cols);
  gridSelect(S.idx);
}

function buildGrid() {
  const grid = $('grid');
  G.obs?.disconnect(); G.queue = [];
  grid.style.setProperty('--cols', G.cols);
  grid.innerHTML = S.list.map((p, i) =>
    `<div class="tile" data-i="${i}"><img alt="" decoding="async"><span class="tst"></span><span class="tname">${esc(p.name)}</span></div>`).join('');
  G.obs = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const tile = e.target;
      tile._vis = e.isIntersecting;
      if (e.isIntersecting && !tile._queued) { tile._queued = true; G.queue.push(tile); }
    }
    pumpThumbs();
  }, { root: grid, rootMargin: '600px 0px' });
  for (const tile of grid.children) { renderTile(tile); G.obs.observe(tile); }
  gridSelect(S.idx);
}

function pumpThumbs() {
  const max = S.source?.type === 'camera' ? 1 : 3;    // camera: each photo already uses its 2 request slots
  while (G.on && G.running < max && G.queue.length) {
    const tile = G.queue.shift();
    tile._queued = false;
    if (!tile.isConnected || !tile._vis) continue;    // scrolled away: queued again when visible
    const photo = S.list[+tile.dataset.i];
    if (!photo || G.thumbs.has(photo.id)) { renderTile(tile); continue; }
    G.running++;
    thumbFor(photo).then(() => renderTile(tile), () => tile.classList.add('err'))
      .finally(() => { G.running--; pumpThumbs(); });
  }
}

async function thumbFor(photo) {
  if (G.thumbs.has(photo.id)) return G.thumbs.get(photo.id);
  const toned = TONE.on;
  const wasCached = cache.has(photo.id);
  const { url } = await load(photo);
  const img = new Image();
  img.src = url;
  await img.decode();
  const k = Math.min(1, THUMB_PX / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement('canvas');
  c.width = Math.round(img.naturalWidth * k); c.height = Math.round(img.naturalHeight * k);
  const ctx = c.getContext('2d');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(img, 0, 0, c.width, c.height);
  const tone = TONE.params.get(photo.id);
  if (toned && tone) bakeTone(ctx, c.width, c.height, tone);
  if (toned !== TONE.on) throw new Error('tone changed');   // toggled meanwhile: the grid is being rebuilt
  const thumb = await new Promise(res => c.toBlob(b => res(URL.createObjectURL(b)), 'image/jpeg', 0.85));
  G.thumbs.set(photo.id, thumb);
  const entry = cache.get(photo.id);
  if (!wasCached && entry) {                         // don't keep every big preview in memory...
    entry.then(v => {
      if ($('photo').src === v.url) return;          // ...unless the photo view shows it meanwhile (opened from the grid)
      if (cache.get(photo.id) === entry) cache.delete(photo.id);
      URL.revokeObjectURL(v.url);
    }).catch(() => {});
  }
  return thumb;
}

function renderTile(tile) {
  const photo = tile && S.list[+tile.dataset.i];
  if (!photo) return;
  const u = G.thumbs.get(photo.id), img = tile.firstChild;
  if (u && img.getAttribute('src') !== u) img.src = u;
  const { n, fromFile } = currentRating(photo);
  const st = tile.querySelector('.tst');
  st.textContent = '★'.repeat(n);
  st.classList.toggle('file', !!fromFile);
}

function gridSelect(i) {
  if (!S.list.length) return;
  S.idx = Math.max(0, Math.min(i, S.list.length - 1));
  const grid = $('grid');
  grid.querySelector('.tile.sel')?.classList.remove('sel');
  const tile = grid.children[S.idx];
  if (tile) { tile.classList.add('sel'); tile.scrollIntoView({ block: 'nearest' }); }
  const photo = S.list[S.idx];
  $('counter').textContent = `${S.idx + 1} / ${S.list.length}`;
  $('fname').textContent = photo.id;
  $('exif').textContent = fmtExif(S.infos.get(photo.id));
  $('timing').textContent = '';
  renderStars();
}

$('btnGrid').onclick = () => { gridToggle(); $('btnGrid').blur(); };
$('grid').onclick = (e) => { const tile = e.target.closest('.tile'); if (tile) gridSelect(+tile.dataset.i); };
$('grid').ondblclick = (e) => {
  const tile = e.target.closest('.tile');
  if (tile) { gridSelect(+tile.dataset.i); gridToggle(false); }
};

/* ================================================================ misc */

$('prev').onclick = () => show(S.idx - 1);
$('next').onclick = () => show(S.idx + 1);
document.addEventListener('keydown', (e) => {
  if (document.querySelector('dialog[open]')) return;          // dialogs handle their own keys
  if (['SELECT', 'INPUT'].includes(e.target.tagName) && e.target.type !== 'checkbox') return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'Escape') return Z.on ? zoomOut() : G.on && gridToggle(false);
  if (e.key.toLowerCase() === 'b') { cycleBg(); return e.preventDefault(); }
  if (e.key.toLowerCase() === 'a') { setAutoTone(!TONE.on); return e.preventDefault(); }
  if (!S.list.length) return;
  if (e.key.toLowerCase() === 'g') gridToggle();
  else if (G.on && e.key === 'ArrowDown') gridSelect(S.idx + G.cols);
  else if (G.on && e.key === 'ArrowUp') gridSelect(S.idx - G.cols);
  else if (G.on && e.key === 'Enter') gridToggle(false);
  else if (G.on && ['+', '='].includes(e.key)) gridCols(G.cols - 1);
  else if (G.on && ['-', '_'].includes(e.key)) gridCols(G.cols + 1);
  else if (e.key === 'ArrowRight') go(S.idx + 1);
  else if (e.key === 'ArrowLeft') go(S.idx - 1);
  else if (/^[0-5]$/.test(e.key)) { if (!e.repeat) rate(+e.key); }   // held key: no toggle / multi-rating
  else if (e.key.toLowerCase() === 'u') nextUnrated();
  else return;
  e.preventDefault();
});

// touch: swipe left / right on the photo (or the grid) = next / previous
let touch0 = null;
$('photo').parentElement.addEventListener('touchstart', (e) => {
  touch0 = e.touches.length === 1 && !Z.on ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
}, { passive: true });
$('photo').parentElement.addEventListener('touchend', (e) => {
  if (!touch0 || G.on || !S.list.length) return;
  const dx = e.changedTouches[0].clientX - touch0.x, dy = e.changedTouches[0].clientY - touch0.y;
  touch0 = null;
  if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) go(S.idx + (dx < 0 ? 1 : -1));
}, { passive: true });
if (window.AndroidBridge) document.body.classList.add('android');

// Android back button: same as Escape. Returns true when something was closed (otherwise the app exits).
window.androidBack = () => {
  const d = document.querySelector('dialog[open]');
  if (d) { d.close(); return true; }
  if (Z.on) { zoomOut(); return true; }
  if (G.on) { gridToggle(false); return true; }
  return false;
};

// busy = true: shows a loading indicator
function msg(text, busy = false) {
  $('msgText').textContent = text;
  $('msg').classList.toggle('busy', busy);
  $('msg').style.display = text ? 'flex' : 'none';
}
let toastTimer;
function toast(text, bad = false) {
  const el = $('toast');
  el.textContent = text; el.classList.toggle('bad', bad); el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), bad ? 5000 : 1800);
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const clamp = (v) => Math.max(0, Math.min(1, v));
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

(async () => {
  applyPrefs();
  try { setRatings(await api.ratings()); } catch {}
  renderStars();
  // resume the last source
  const last = store.get('source');
  if (last === 'folder' && store.get('folder')) await setSource('folder', store.get('folder'));
  else await refreshState();
  // camera: connection status; folder: picks up ratings merged from the phone (inbox)
  setInterval(() => { if (S.source?.type === 'camera' || !document.hidden) refreshState(); }, 5000);
  pollDownload();                                   // a download may still be running (page reloaded)
})();
