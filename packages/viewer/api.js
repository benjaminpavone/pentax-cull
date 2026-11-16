// Data access for the viewer. THIS IS THE ONLY FILE TO ADAPT for another platform
// (e.g. Android: same functions, implemented by calling the camera directly + local storage).
// Full HTTP contract: docs/api.md
'use strict';

window.api = (() => {
  const getJson = async (url) => {
    const r = await fetch(url);
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  };
  const postJson = async (url, body = {}) => {
    // Android app: a WebView cannot read POST bodies, so POSTs go through the native bridge
    if (window.AndroidBridge) return JSON.parse(window.AndroidBridge.post(url, JSON.stringify(body)));
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return r.json();
  };
  const q = (id) => encodeURIComponent(id);

  return {
    // { type: 'camera'|'folder', label, writable, camera: { ssid, cameraSsid, onCameraWifi, reachable, busy, error } }
    state: () => getJson('/api/state'),
    // type 'folder' without path -> opens the native folder picker (prompt = its title)
    setSource: (type, path, prompt) => postJson('/api/source', { type, path, prompt }),
    // native folder picker -> { ok, path, cancelled?, busy? }
    chooseFolder: (prompt, def) => postJson('/api/choose-folder', { prompt, default: def }),
    cameraConnect: () => postJson('/api/camera/connect'),
    // Android only: camera Wi-Fi name / password, kept on the phone (password never sent back)
    cameraConfig: (ssid, password) => postJson('/api/camera/config', { ssid, password }),
    cameraDisconnect: () => postJson('/api/camera/disconnect'),
    // [{ id, key, group, name }]
    photos: async () => (await getJson('/api/photos')).photos,
    // { datetime, av, tv, sv, orientation, cameraModel, fileRating?, fullWidth?, cropRight?, cropBottom? }
    info: (id) => getJson(`/api/photo/info?id=${q(id)}`),
    // JPEG Blob (NOT rotated: apply info.orientation). size: 'screen' (fast) or 'full' (zoom)
    preview: async (id, size = 'screen') => {
      const r = await fetch(`/api/photo/preview?id=${q(id)}&size=${size}`);
      if (!r.ok || !(r.headers.get('Content-Type') || '').startsWith('image/')) throw new Error('preview unavailable');
      return r.blob();
    },
    // { key: { rating, datetime, source, updated } }
    ratings: () => getJson('/api/ratings'),
    // -> { ok, count, fileOk?, fileMode?, fileError? }
    saveRating: (r) => postJson('/api/ratings', r),
    // items: [{ id, rating }] -> { ok, busy?, ...status }
    download: (items, dest, writeRating) => postJson('/api/download', { items, dest, writeRating }),
    // { running, dest, total, done, downloaded, skipped, failed: [{name, error}], current, bytes, size, cancelled }
    downloadStatus: () => getJson('/api/download'),
    downloadCancel: () => postJson('/api/download/cancel'),
    downloadReveal: () => postJson('/api/download/reveal'),
    // Mac: folder watched for rating files exported by the phone -> { dir, last: {file, added, updated, at}, error }
    inboxStatus: () => getJson('/api/inbox'),
    inboxSet: (dir) => postJson('/api/inbox', { dir }),
    // Android: saves a dated copy of ratings.json with the system file picker (Dropbox, Drive…);
    // the result arrives in window.onRatingsExported(ok, name, count)
    exportRatings: () => postJson('/api/ratings/export'),
    // preview cache on disk: { bytes, files, limitBytes, dir }. Android: its own app cache.
    cacheStats: () => getJson('/api/cache'),
    cacheClear: () => postJson('/api/cache', { clear: true }),
    cacheSetLimit: (limitBytes) => postJson('/api/cache', { limitBytes }),
  };
})();
