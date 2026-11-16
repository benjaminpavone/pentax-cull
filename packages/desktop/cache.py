"""Disk cache of the screen previews (~/Library/Caches/pentax-cull/previews), capped in size.

When the total goes over the limit, the least recently used files are deleted down to 90 % of it
(files are touched on every read, so their modification time is their last use).
The limit is a server-side setting (settings.json next to the cache), changed from the viewer's Settings dialog.
"""
import json
import os
import threading

ROOT = os.environ.get("PENTAX_CULL_CACHE") or os.path.expanduser("~/Library/Caches/pentax-cull")   # env: tests
DIR = os.path.join(ROOT, "previews")
SETTINGS = os.path.join(ROOT, "settings.json")
DEFAULT_LIMIT = 2 * 1024 ** 3
MIN_LIMIT = 100 * 1024 ** 2

_lock = threading.Lock()
_total = None            # bytes in DIR, computed on first use then kept up to date


def _settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def limit():
    return max(MIN_LIMIT, int(_settings().get("limitBytes") or DEFAULT_LIMIT))


def set_limit(nbytes):
    os.makedirs(ROOT, exist_ok=True)
    s = _settings()
    s["limitBytes"] = max(MIN_LIMIT, int(nbytes))
    with open(SETTINGS, "w") as f:
        json.dump(s, f)
    with _lock:
        _prune()


def _entries():
    """[(mtime, size, path)] of finished cache files (files being written end in .part.jpg)."""
    out = []
    try:
        with os.scandir(DIR) as it:
            for e in it:
                if e.is_file() and not e.name.endswith(".part.jpg"):
                    try:
                        st = e.stat()
                        out.append((st.st_mtime, st.st_size, e.path))
                    except FileNotFoundError:
                        pass
    except FileNotFoundError:
        pass
    return out


def _prune():
    """Deletes the oldest files until the total is under 90 % of the limit (call with _lock held)."""
    global _total
    entries = _entries()
    _total = sum(e[1] for e in entries)
    lim = limit()
    if _total <= lim:
        return
    # only the previews: the white-band .json files are tiny and read without a lock
    for _mtime, size, path in sorted(e for e in entries if e[2].endswith(".jpg")):
        if _total <= lim * 0.9:
            break
        try:
            os.remove(path)
            _total -= size
        except FileNotFoundError:
            pass


def added(nbytes):
    """To call after writing a new cache file."""
    global _total
    with _lock:
        if _total is None:
            _prune()
            return
        _total += nbytes
        if _total > limit():
            _prune()


def touch(path):
    """Marks a cache file as just used (least-recently-used order)."""
    try:
        os.utime(path)
    except OSError:
        pass


def stats():
    with _lock:
        entries = _entries()
        global _total
        _total = sum(e[1] for e in entries)
        return {"bytes": _total, "files": len(entries), "limitBytes": limit(), "dir": DIR}


def clear():
    global _total
    with _lock:
        for _, _, path in _entries():
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        _total = 0
    return stats()
