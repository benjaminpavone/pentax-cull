"""Folder source: DNG/JPEG files of a folder on disk (or a memory card in a reader).

Previews:
- size="full"   : the full-resolution JPEG embedded in the DNG (7360 px on a K-1 II), read as is (~20 ms) -> zoom
- size="screen" : a 1840 px version (1/4: fast JPEG decoding) made by `sips` (macOS), cached on disk
                  (size-capped, see cache.py); prepared in the background for the whole folder when it is opened.
"""
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import cache as preview_cache
import dng

EXT_DNG = (".dng",)
EXT_JPEG = (".jpg", ".jpeg")
SCREEN_PX = 1840
CACHE_DIR = preview_cache.DIR
WORKERS = 3


class FolderSource:
    type = "folder"
    writable = True       # xmp:Rating can be written into the files

    def __init__(self, path):
        self.root = os.path.realpath(os.path.expanduser(path))
        if not os.path.isdir(self.root):
            raise ValueError(f"folder not found: {path}")
        self.name = os.path.basename(self.root)
        # memory card or external disk: ratings are not written into its files by default
        self.removable = self.root.startswith("/Volumes/")
        self.write_lock = threading.Lock()
        self.gen_locks = {}
        self.gen_locks_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(WORKERS)
        self.closed = False
        os.makedirs(CACHE_DIR, exist_ok=True)

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)

    def label(self):
        return self.root

    def _path(self, pid):
        p = os.path.realpath(os.path.join(self.root, pid))
        if not p.startswith(self.root + os.sep):          # never outside the chosen folder
            raise ValueError("invalid path")
        return p

    def list(self):
        out = []
        for dirpath, dirnames, files in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for f in sorted(files):
                if f.startswith(".") or not f.lower().endswith(EXT_DNG + EXT_JPEG):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, f), self.root)
                group = os.path.dirname(rel) or self.name
                out.append({"id": rel, "key": f"{self.name}/{rel}", "group": group, "name": f})
        # screen previews prepared in the background, in list order
        for p in out:
            self.pool.submit(self._warm, p["id"])
        return out

    def info(self, pid):
        p = self._path(pid)
        if p.lower().endswith(EXT_JPEG):
            try:
                i = dng.info(p)   # JPEGs are not TIFFs: just the minimum
            except Exception:
                i = {}
            i["orientation"] = 1  # the browser applies the EXIF orientation of JPEGs itself
            return i
        i = dng.info(p)
        if dng.preview(p):
            _, _, width, key = self._where(p)
            band = self._band(p, width, key) if width > SCREEN_PX else {"right": 0, "bottom": 0}
            # for the zoom: useful (unrotated) width of the full-resolution preview + white band to crop
            i["fullWidth"] = width - band["right"]
            i["cropRight"], i["cropBottom"] = band["right"], band["bottom"]
        return i

    # ------------------------------------------------------------------ previews
    def _where(self, p):
        """(offset, length, width, cache key) of the full-resolution JPEG, without reading it."""
        if p.lower().endswith(EXT_JPEG):
            st = os.stat(p)
            return 0, st.st_size, None, f"{p}|{st.st_size}|{st.st_mtime_ns}"
        loc = dng.preview(p)
        if not loc:
            raise RuntimeError("no embedded JPEG preview")
        off, length, width = loc
        # the preview does not move when a rating is written: a good cache key
        return off, length, width, f"{p}|{off}|{length}"

    def _full(self, p):
        """(full-resolution JPEG bytes, width, cache key)."""
        off, length, width, key = self._where(p)
        with open(p, "rb") as f:
            f.seek(off)
            return f.read(length), width, key

    def _lock(self, name):
        with self.gen_locks_lock:
            return self.gen_locks.setdefault(name, threading.Lock())

    def _band(self, p, width, key, data=None):
        """White filler band on the right / bottom edge of the preview, in full-resolution pixels.

        The 7360 px preview written by the K-1 II only holds the image on 7296 px: the last 64 columns
        are white. Lightroom regenerates a clean preview. Rough detection at 1/8, then exact.
        """
        if not width:
            return {"right": 0, "bottom": 0}
        f = os.path.join(CACHE_DIR, _digest(f"{key}|band-v3") + ".json")
        hit = _read_band(f)
        if hit is not None:
            return hit
        with self._lock(f):
            hit = _read_band(f)
            if hit is not None:
                return hit
            res = {"right": 0, "bottom": 0}
            if data is None:
                data = self._full(p)[0]
            with tempfile.TemporaryDirectory() as td:
                jpg, bmp = os.path.join(td, "p.jpg"), os.path.join(td, "p.bmp")
                with open(jpg, "wb") as fh:
                    fh.write(data)
                # 1. rough pass on a 1/8 version (very fast JPEG decoding)
                if _sips(["-Z", str(width // 8), "-s", "format", "bmp", jpg, "--out", bmp]):
                    w, h, px = _read_bmp(bmp)
                    rough_r, rough_b = _white_edge(w, h, px, "x"), _white_edge(w, h, px, "y")
                    H = round(h * width / w)
                    # 2. exact measurement, at full resolution, on a 256 px strip only
                    if rough_r and _sips(["--cropToHeightWidth", str(H), "256", "--cropOffset", "0", str(width - 256),
                                          "-s", "format", "bmp", jpg, "--out", bmp]):
                        res["right"] = _white_edge(*_read_bmp(bmp), "x", full=width)
                    if rough_b and _sips(["--cropToHeightWidth", "256", str(width), "--cropOffset", str(H - 256), "0",
                                          "-s", "format", "bmp", jpg, "--out", bmp]):
                        res["bottom"] = _white_edge(*_read_bmp(bmp), "y", full=H)
                    res["width"], res["height"] = width, H
            tmp = f + ".part"             # written aside then renamed: never read half-written
            with open(tmp, "w") as fh:
                json.dump(res, fh)
            os.replace(tmp, f)
            return res

    def _screen(self, pid):
        p = self._path(pid)
        _, _, width, key = self._where(p)                   # cheap: the preview itself is read only if needed
        if width and width <= SCREEN_PX:
            return self._full(p)[0]
        cache = os.path.join(CACHE_DIR, _digest(f"{key}|{SCREEN_PX}|v7") + ".jpg")
        hit = _read_cached(cache)
        if hit is not None:
            return hit
        data = self._full(p)[0]
        with self._lock(cache):                             # one generation per photo
            if not os.path.exists(cache):
                self._band(p, width, key, data)             # detected here (in the background) for info()
                with tempfile.TemporaryDirectory() as td:
                    src = os.path.join(td, "p.jpg")
                    with open(src, "wb") as fh:
                        fh.write(data)
                    out = cache + ".part.jpg"
                    # 1840 px = 1/4 of 7360: fast JPEG decoding
                    if not _sips(["-Z", str(SCREEN_PX), "-s", "formatOptions", "85", src, "--out", out]):
                        return data                         # fallback: full resolution
                    # the white band, if any, is cropped by the page (info.cropRight / cropBottom):
                    # sips silently ignores some crops
                    os.replace(out, cache)
                    preview_cache.added(os.path.getsize(cache))   # may delete the oldest previews
        hit = _read_cached(cache)
        return hit if hit is not None else data

    def _warm(self, pid):
        if self.closed:
            return
        try:
            self._screen(pid)
        except Exception:   # noqa: S110 (background warm-up: the page asks again and gets the error then)
            pass

    def preview(self, pid, size="screen"):
        if size == "full":
            return self._full(self._path(pid))[0], "image/jpeg"
        return self._screen(pid), "image/jpeg"

    def fetch(self, pid, out, progress):
        """Copies the original file into `out`. Returns False if progress() asked to stop."""
        p = self._path(pid)
        size, done = os.path.getsize(p), 0
        with open(p, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if not progress(done, size):
                    return False
        return True

    # ------------------------------------------------------------------ ratings
    def write_rating(self, pid, rating):
        p = self._path(pid)
        if not p.lower().endswith(EXT_DNG):
            raise RuntimeError("writing ratings: DNG files only for now")
        with self.write_lock:
            return dng.write_rating(p, rating)


WHITE = 220


def _read_band(path):
    """Cached white band, or None (absent or damaged: measured again)."""
    try:
        with open(path) as fh:
            res = json.load(fh)
        return res if isinstance(res, dict) and {"right", "bottom"} <= res.keys() else None
    except (OSError, ValueError):
        return None


def _digest(s):
    """Cache file name (a name, not a security check)."""
    return hashlib.sha1(s.encode(), usedforsecurity=False).hexdigest()


def _read_cached(path):
    """Cached preview bytes (and marks it as used), or None (absent, or just deleted by the size cap)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return None
    preview_cache.touch(path)
    return data


def _sips(args):
    """False if sips failed or is missing (not macOS): no screen preview, no white-band detection."""
    try:
        r = subprocess.run(["sips", *args], capture_output=True, timeout=60)
    except FileNotFoundError:
        return False
    return r.returncode == 0


def _read_bmp(path):
    """(width, height, px(x, y) -> min(R,G,B)) for a 24/32-bit BMP written by sips."""
    with open(path, "rb") as fh:
        b = fh.read()
    off = struct.unpack_from("<I", b, 10)[0]
    w, h = struct.unpack_from("<ii", b, 18)
    bpp = struct.unpack_from("<H", b, 28)[0] // 8
    row, ah = (w * bpp + 3) & ~3, abs(h)

    def px(x, y):
        fy = ah - 1 - y if h > 0 else y            # BMP stored bottom-up when h > 0
        p = off + fy * row + x * bpp
        return min(b[p], b[p + 1], b[p + 2])
    return w, ah, px


def _white_edge(w, h, px, axis, full=None):
    """Number of nearly white columns (axis='x', right edge) or rows ('y', bottom). At most 5 % of the image."""
    n, size, other = 0, (w if axis == "x" else h), (h if axis == "x" else w)
    limit = (full or size) // 20                   # 5 % of the whole image (not of the analysed strip)
    samples = range(0, other, max(1, other // 300))
    for k in range(size - 1, max(-1, size - 1 - limit), -1):
        white = sum((px(k, t) if axis == "x" else px(t, k)) >= WHITE for t in samples)
        if white < 0.95 * len(samples):
            break
        n += 1
    return n if n < limit else 0                   # too wide: probably real white (sky...)


_choose_lock = threading.Lock()


def choose_folder(prompt="Choose a photo folder", default=None):
    """Native macOS folder picker. Returns the path, None (cancelled) or False (already open)."""
    if not _choose_lock.acquire(blocking=False):
        return False
    try:
        prompt = prompt.replace('"', "'")
        loc = ""
        if default and os.path.isdir(os.path.expanduser(default)):
            loc = f' default location (POSIX file "{os.path.expanduser(default)}")'
        script = f'POSIX path of (choose folder with prompt "{prompt}"{loc})'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip() or None
    finally:
        _choose_lock.release()
