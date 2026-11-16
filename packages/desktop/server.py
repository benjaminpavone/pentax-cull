#!/usr/bin/env python3
"""Local (Mac) server for the viewer: serves packages/viewer and implements the API in docs/api.md.

Photo sources: the Pentax camera (Wi-Fi) or a folder on disk.
Ratings are always saved to ratings.json (format: docs/ratings-format.md) and, in folder mode when asked,
also into the DNG files (xmp:Rating). Selected photos can be downloaded/copied to a local folder.

Run: python3 packages/desktop/server.py   then open http://localhost:8765
No dependencies (Python standard library only; tested on 3.14, see environment.yml).
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cache as preview_cache                          # noqa: E402
from download import DownloadJob                       # noqa: E402
from inbox import Inbox, find_same, valid_date, valid_key                     # noqa: E402
from sources.camera import CameraSource               # noqa: E402
from sources.folder import FolderSource, choose_folder  # noqa: E402

ROOT = HERE.parent.parent
VIEWER = ROOT / "packages" / "viewer"
RATINGS = Path(os.environ.get("RATINGS_FILE", ROOT / "ratings.json"))
PORT = int(os.environ.get("PORT", 8765))
SETTINGS = Path(os.environ.get("SETTINGS_FILE", ROOT / "settings.json"))   # inbox folder etc.
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


def load_env():
    env, p = {}, ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
camera = CameraSource(ENV.get("CAMERA_WIFI_SSID", ""), ENV.get("CAMERA_WIFI_PASSWORD", ""),
                      ENV.get("CAMERA_HOST", "192.168.0.1"), ENV.get("HOME_WIFI_SSID"))
current = {"source": camera, "download": None}
ratings_lock = threading.Lock()


# ------------------------------------------------------------------ ratings (ratings.json)

def read_ratings():
    """The ratings. A missing file is an empty one; an unreadable one raises: saving over it would erase
    every rating it holds (it may be a Dropbox conflict, a hand edit...)."""
    try:
        text = RATINGS.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        raise RatingsUnreadable(f"{RATINGS} is unreadable ({e}): fix or move it, nothing is saved meanwhile") from e
    if not isinstance(data, dict):
        raise RatingsUnreadable(f"{RATINGS} is not a ratings file: fix or move it, nothing is saved meanwhile")
    return data


class RatingsUnreadable(Exception):
    pass


def rated_count(data):
    return sum(1 for v in data.values() if isinstance(v, dict) and v.get("rating"))


def write_ratings(data):
    tmp = RATINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True))
    tmp.replace(RATINGS)


def save_rating(key, rating, dt, source):
    with ratings_lock:
        data = read_ratings()
        # the same photo may be stored under another name (rated on the phone: "100_1511/X.DNG",
        # here from the card: "DCIM/100_1511/X.DNG"): one entry per photo
        while True:
            same = find_same({k: v for k, v in data.items() if k != key}, key, {"datetime": dt})
            if same is None:
                break
            del data[same]
        # a cleared rating is kept as 0 (with its date): otherwise an older export of the phone, still holding
        # the rating, would bring it back at the next merge (docs/ratings-format.md)
        data[key] = {"rating": rating, "datetime": dt, "source": source,
                     "updated": datetime.now().isoformat(timespec="seconds")}
        write_ratings(data)
        return rated_count(data)


inbox = Inbox(SETTINGS, read_ratings, write_ratings, ratings_lock)


# ------------------------------------------------------------------ HTTP

def source_state():
    s = current["source"]
    out = {"type": s.type, "label": s.label(), "writable": s.writable, "removable": getattr(s, "removable", False),
           "ratingsMtime": RATINGS.stat().st_mtime if RATINGS.exists() else 0, "inbox": inbox.status()}
    out["camera"] = camera.status() if s is camera else {"cameraSsid": camera.ssid, "busy": camera.net["busy"]}
    return out


def download_state():
    job = current["download"]
    return job.status() if job else {"running": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass   # the page gave up on the request: harmless

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def allowed(self, post=False):
        """Only the viewer itself may talk to this server, not other web pages open in the browser:
        - Host must be localhost / 127.0.0.1 (blocks DNS rebinding: a web site whose name points to 127.0.0.1);
        - POST: JSON only (a cross-site JSON request needs a CORS preflight, which this server never accepts)
          and, when the browser sends one, an Origin of this server."""
        hosts = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        ok = self.headers.get("Host", "") in hosts
        if ok and post:
            origin = self.headers.get("Origin")
            ok = (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"
                  and (origin is None or origin in {f"http://{h}" for h in hosts}))
        if not ok:
            self.json({"error": "forbidden"}, 403)
        return ok

    def do_GET(self):
        if not self.allowed():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        src = current["source"]
        try:
            if u.path == "/api/state":
                return self.json(source_state())
            if u.path == "/api/photos":
                return self.json({"photos": src.list()})
            if u.path == "/api/photo/info":
                return self.json(src.info(q["id"]))
            if u.path == "/api/photo/preview":
                data, ctype = src.preview(q["id"], q.get("size", "screen"))
                return self.send(200, data, ctype)
            if u.path == "/api/ratings":
                return self.json(read_ratings())
            if u.path == "/api/download":
                return self.json(download_state())
            if u.path == "/api/cache":
                return self.json(preview_cache.stats())
            if u.path == "/api/inbox":
                return self.json(inbox.status())
            # viewer static files
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (VIEWER / rel).resolve()
            if VIEWER.resolve() in f.parents and f.is_file():
                return self.send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
            return self.json({"error": "not found"}, 404)
        except Exception as e:
            return self.json({"error": str(e)}, 502)

    def do_POST(self):
        if not self.allowed(post=True):
            return
        u = urlparse(self.path)
        try:
            b = self.body()
            if u.path == "/api/source":
                old = current["source"]
                if b.get("type") == "camera":
                    current["source"] = camera
                elif b.get("type") == "folder":
                    path = b.get("path") or choose_folder(b.get("prompt") or "Choose a photo folder")
                    if path is False:
                        return self.json({"ok": False, "busy": True})
                    if not path:
                        return self.json({"ok": False, "cancelled": True})
                    if not (isinstance(old, FolderSource) and os.path.realpath(path) == old.root):
                        current["source"] = FolderSource(path)
                if old is not current["source"] and isinstance(old, FolderSource):
                    old.close()
                return self.json({"ok": True, **source_state()})

            if u.path == "/api/inbox":
                if "dir" in b:
                    return self.json({"ok": True, **inbox.set_folder(b["dir"])})
                return self.json({"ok": True, **inbox.check()})

            if u.path == "/api/cache":
                if b.get("clear"):
                    preview_cache.clear()
                if b.get("limitBytes"):
                    preview_cache.set_limit(int(b["limitBytes"]))
                return self.json({"ok": True, **preview_cache.stats()})

            if u.path == "/api/choose-folder":
                path = choose_folder(b.get("prompt") or "Choose a folder", b.get("default"))
                if path is False:
                    return self.json({"ok": False, "busy": True})
                return self.json({"ok": bool(path), "path": path, "cancelled": not path})

            if u.path in ("/api/camera/connect", "/api/camera/disconnect"):
                return self.json(camera.start(u.path.rsplit("/", 1)[1]))

            if u.path == "/api/ratings":
                src = current["source"]
                rating = b.get("rating") or 0
                if type(rating) is not int or not 0 <= rating <= 5 or not valid_key(b.get("key")):
                    return self.json({"ok": False, "error": "invalid rating"}, 400)
                dt = b.get("datetime") if valid_date(b.get("datetime")) else None
                count = save_rating(b["key"], rating, dt, src.type)
                out = {"ok": True, "count": count}
                if b.get("writeFile"):
                    if not src.writable:
                        out.update(fileOk=False, fileError="read-only source")
                    else:
                        try:
                            out.update(fileOk=True, fileMode=src.write_rating(b["id"], rating))
                        except Exception as e:
                            out.update(fileOk=False, fileError=str(e))
                return self.json(out)

            if u.path == "/api/download":
                if download_state().get("running"):
                    return self.json({"ok": False, "busy": True})
                items = [{"id": str(i["id"]), "rating": int(i.get("rating") or 0)} for i in b.get("items", [])]
                if not items:
                    return self.json({"ok": False, "error": "nothing to download"})
                dest = os.path.realpath(os.path.expanduser(b.get("dest") or "~/Downloads/pentax-cull"))
                job = DownloadJob(current["source"], items, dest, bool(b.get("writeRating", True)))
                current["download"] = job.start()
                return self.json({"ok": True, **job.status()})
            if u.path == "/api/download/cancel":
                if current["download"]:
                    current["download"].cancel()
                return self.json({"ok": True})
            if u.path == "/api/download/reveal":
                job = current["download"]
                if job and os.path.isdir(job.dest):
                    subprocess.run(["open", job.dest])
                return self.json({"ok": True})

            return self.json({"error": "not found"}, 404)
        except Exception as e:
            return self.json({"ok": False, "error": str(e)}, 400)


if __name__ == "__main__":
    print(f"Viewer:  http://localhost:{PORT}")
    print(f"Ratings: {RATINGS}")
    print(f"Camera:  {camera.ssid} @ {camera.host}  |  home Wi-Fi: {camera.home_ssid}")
    print(f"Inbox:   {inbox.folder() or '(none: set it in Settings)'}")
    inbox.watch()                                      # merges phone exports now, then every 30 s
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
