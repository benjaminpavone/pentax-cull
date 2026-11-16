# Viewer API

The viewer (`packages/viewer`) only talks to `window.api` (`packages/viewer/api.js`), which calls the HTTP API below.
- On the Mac it is served by the local server (`packages/desktop/server.py`).
- In the Android app (`packages/android`) the same routes are answered in Java (`Api.java`): GET requests are
  intercepted by the WebView, POST requests go through `window.AndroidBridge.post()` because a WebView cannot read
  POST bodies. `api.js` switches automatically.

| `window.api` | HTTP | Returns |
|---|---|---|
| `state()` | `GET /api/state` | `{type, label, writable, removable, ratingsMtime, inbox, camera:{ssid, cameraSsid, onCameraWifi, reachable, busy, error, waited}}` |
| `setSource(type, path?)` | `POST /api/source {type, path}` | in `folder` mode without `path`: opens the native folder picker (Android: the system folder picker; with a `path`, the last chosen folder is reopened, its access permission is kept) |
| `cameraConnect()` / `cameraDisconnect()` | `POST /api/camera/connect` / `disconnect` | runs in the background; follow `state().camera.busy` |
| `cameraConfig(ssid, password)` | `POST /api/camera/config` | Android: camera Wi-Fi name / password, kept on the phone (the password is never sent back) |
| `photos()` | `GET /api/photos` | `[{id, key, group, name}]` (`key` = key in ratings.json) |
| `info(id)` | `GET /api/photo/info?id=` | `{datetime, av, tv, sv, orientation, cameraModel, fileRating?, fullWidth?, cropRight?, cropBottom?}` (`tv` in the camera's format: `"1.800"` = 1/800 s) |
| `preview(id, size)` | `GET /api/photo/preview?id=&size=screen\|full` | JPEG, **not rotated**: rotate according to `orientation` (3, 6, 8) |
| `ratings()` | `GET /api/ratings` | content of ratings.json |
| `saveRating({id, key, rating, datetime, writeFile})` | `POST /api/ratings` | `{ok, count, fileOk?, fileMode?, fileError?}` |
| `chooseFolder(prompt, default)` | `POST /api/choose-folder` | native macOS dialog: `{ok, path, cancelled?, busy?}` |
| `download(items, dest, writeRating)` | `POST /api/download {items:[{id, rating}], dest, writeRating}` | starts the download / copy in the background |
| `downloadStatus()` | `GET /api/download` | `{running, dest, total, done, downloaded, skipped, failed:[{name,error}], current, bytes, size, cancelled}` |
| `downloadCancel()` / `downloadReveal()` | `POST /api/download/cancel` / `reveal` | cancel / show the folder in the Finder (Android: open the last photo) |
| `inboxStatus()` / `inboxSet(dir)` | `GET /api/inbox` / `POST /api/inbox {dir}` | Mac: inbox folder for the phone's exports `{dir, last:{file, added, updated, at}, error}` |
| `exportRatings()` | `POST /api/ratings/export` | Android: saves a dated copy of the ratings with the system file picker; result in `window.onRatingsExported(ok, name, n)` |
| `cacheStats()` | `GET /api/cache` | `{bytes, files, limitBytes, dir}` (disk cache of the screen previews) |
| `cacheClear()` / `cacheSetLimit(n)` | `POST /api/cache {clear}` / `{limitBytes}` | returns the new stats |

## Sources
- **camera**: the Pentax Wi-Fi API (see below). 720x480 preview. Read-only: `writable=false`.
- **folder** (Mac): DNG and JPEG files of a folder, sub-folders included. Preview = the full-resolution JPEG
  embedded in the DNG. `writable=true`: with `writeFile`, the rating is also written into the DNG
  (`xmp:Rating`, see `packages/desktop/dng.py`). `removable=true` for a folder under `/Volumes/` (memory card,
  external disk): the page then unticks `writeFile` by default.

## Ratings from the phone to the Mac
- The phone exports **a new dated file** each time (`pentax-cull-ratings-<model>-<date>.json`, same format as
  ratings.json) into a folder of the user's choice (Dropbox, Drive…): a synced folder never gets two versions of
  the same file.
- The Mac server watches the inbox folder (set in ⚙ Settings, kept in `settings.json`) at start, then every 30 s,
  **only while it runs**, and merges each new or changed file once (`packages/desktop/inbox.py`): same photo = same
  key, or same file name AND same shooting date; the most recent rating (`updated`) wins; nothing is removed.
- A rating set on the Mac replaces the entry of the same photo stored under another name (one entry per photo).
- The page also finds a rating by name + date, and reloads the ratings when ratings.json changes (`state().ratingsMtime`).

## Preview cache (Mac)
- Screen previews (1840 px) in `~/Library/Caches/pentax-cull/previews`, prepared for the whole folder as soon as it
  is opened (3 workers), kept between sessions.
- Default cap 2 GB (`settings.json` next to it), adjustable in ⚙: above it, the least recently used previews are
  deleted down to 90 % of the cap (`packages/desktop/cache.py`).
- Browser: 8 photos preloaded ahead, 3 behind, up to 30 kept in memory.

## Downloads
- Camera: original file via `GET /v1/photos/:path?size=full` (about 45 MB per DNG), streamed at about 0.85 MB/s,
  i.e. about 1 min per DNG. A download uses 1 of the 2 request slots: previews use the other one, so browsing goes on.
  The camera does **not** support `Range`.
- Folder: plain copy.
- Android: saved into `Pictures/Pentax Cull` (MediaStore), from the camera or from a folder / memory card in a USB-C
  reader; runs in a foreground service (notification with Cancel), so the phone can be locked.
- A file already at the destination is skipped, but its rating is updated if `writeRating`. A partial transfer never
  gets the final name (`.part`).
- `writeRating`: `xmp:Rating` is written into the downloaded DNGs, so Lightroom shows the stars at import.

## Measured camera limits (K-1 II, `packages/tools/camera_bench.py`, 2026-11-15)
| Measurement | Result |
|---|---|
| `size=view` previews one at a time | 0.32 s/photo |
| 2 simultaneous requests | 0.16 s/photo, no errors → **limit used: 2** (`MAX_CONCURRENT`) |
| 3 simultaneous requests | all time out, the camera stays stuck for about 30 s |
| `Range: bytes=…` | ignored: 200 response with the whole file (despite `Accept-Ranges: bytes`) |
| `size=full` throughput | about 0.85 MB/s (45 MB in 53 s) |
| Other sizes (`thumb`, `xs`, `1080x720`) | error: only `view` (720x480 JPEG, quality ~70, 4:2:2) and `full` exist |
| Full-resolution JPEG inside a camera DNG | at the END of the file (41–45.5 MB of 45.5): cannot be fetched without the whole file |

## Android folder source
- Folder chosen with the system picker (`ACTION_OPEN_DOCUMENT_TREE`): a memory card in a USB-C reader or a phone
  folder. Read / write access is kept (persisted URI permission), only for the last chosen folder.
- Same ids, keys and info as the Mac folder source (`FolderSource.java`, `Dng.java`). `removable` is true outside the
  phone's internal storage (card, USB drive): ratings are then not written into the files unless the box is ticked.
- `size=screen`: embedded JPEG decoded at 1/4 (1840 px), JPEG 85, cached in the app's cache folder (1 GB cap, least
  recently used deleted first); prepared in the background by 2 workers. `size=full`: embedded JPEG as is.
- Ratings written into the DNGs with positioned reads / writes on the file descriptor (`Os.pread` / `pwrite`): same
  bytes as `dng.py`.
