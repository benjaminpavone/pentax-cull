# pentax-cull

Rate your Pentax photos straight from the camera (Wi-Fi, phone or Mac) or from the memory card, and find the
ratings in Lightroom Classic. Built and tested with a PENTAX K-1 Mark II.

- **In the field (Android phone)**: join the camera's Wi-Fi (mobile data stays on), browse the previews, rate,
  download a few photos with the rating written into the DNG. Export the ratings to a synced folder (Dropbox…).
- **Back home (Mac)**: put the card in a reader and open it in folder mode: sharp full-resolution previews, 100 %
  zoom, contact sheet, the phone's ratings already merged. Copy the best ones with their rating, or import everything
  into Lightroom and apply the ratings with the plugin.
- Display-only **Auto** tone to judge dark previews (the files are never changed).

Why the card reader for the real culling: over Wi-Fi the camera only serves a 720x480 preview (too soft to judge
sharpness) or the whole 45 MB file (about 1 min). See `docs/api.md`.

## Packages
| Folder | Role |
|---|---|
| `packages/viewer/` | The viewer / rating page (HTML/JS, en + fr), shared by the Mac and Android. Everything goes through `api.js`. |
| `packages/desktop/` | Local Mac server (Python standard library only, tested on 3.14; `environment.yml` for conda). Sources: the camera (Wi-Fi) or a folder. DNG read/write (`dng.py`), phone ratings inbox (`inbox.py`). |
| `packages/android/` | Android app: a WebView showing the same viewer, the API in Java. Built without Gradle (`build.sh`). |
| `packages/lightroom-plugin/` | Lightroom Classic plugin: applies `ratings.json` to the catalog. |
| `packages/tools/` | Camera probing / benchmark scripts (`camera_session.sh` always switches back to your Wi-Fi). |
| `docs/` | Contracts between the packages: `api.md`, `ratings-format.md`. |

## Mac
The desktop server currently runs on **macOS only**: it uses `sips` (screen previews), `osascript` (folder picker)
and `networksetup` (camera Wi-Fi). Windows support is planned for a V2. The Android app and the Lightroom plugin
do not depend on it.
```
conda env create -f environment.yml   # once: Python 3.14, no third-party package
conda activate pentax-cull
cp .env.example .env      # then fill in the camera's Wi-Fi name and password (only for camera mode)
python packages/desktop/server.py
```
Then open http://localhost:8765. Folder mode works on any folder, including a memory card (ratings are not written
into the card's files unless you tick the box).

Note: in camera mode the Mac joins the camera's Wi-Fi and loses internet until you disconnect.

## Android
Needs a JDK 17+ and the Android SDK (command-line tools, a platform and build-tools); by default `build.sh` looks in
`~/Developer/jdk-*` and `~/Developer/android-sdk`.
```
packages/android/build.sh            # -> packages/android/build/pentax-cull.apk
packages/android/build.sh install    # installs and starts it on a phone connected with USB debugging
```
In the app: ⚙ Settings → camera Wi-Fi name and password, then Connect.

## Lightroom plugin
File → Plug-in Manager → Add → `packages/lightroom-plugin/PentaxCull.lrplugin`, then
Library → Plug-in Extras → Apply phone ratings….

## Tests
```
conda activate pentax-cull       # environment.yml also installs pytest, ruff, pip-audit and luajit
pytest                           # ~20 s, no camera, phone or real photo needed
ruff check .
pip-audit -r requirements-dev.txt
```
The tests build small synthetic DNG files laid out like the K-1 II ones (`tests/conftest.py`). They cover:
- `test_dng.py`: EXIF reading, preview location, rating writing in every case (no XMP, in place, padding, appended),
  and that existing bytes never move;
- `test_dng_java.py`: the Android `Dng.java` writes exactly the same bytes as `dng.py` (needs a JDK 11+, else skipped);
- `test_inbox.py`: merging of phone exports and rejection of invalid ratings or files;
- `test_server.py`: requests from other web sites are refused, ratings are validated, an unreadable ratings.json is
  never overwritten, folder mode end to end (list, info, previews and their cache, white band, no access outside the
  folder, rating written into the file, copy);
- `test_download.py`: two different photos with the same file name are both copied (NAME-2.DNG);
- `test_lightroom.py`: the Lightroom plugin, run with LuaJIT against a stand-in of the Lightroom SDK (skipped
  without luajit).
The white-band test needs macOS (`sips`); it is skipped elsewhere.


## License

[MIT](LICENSE)
