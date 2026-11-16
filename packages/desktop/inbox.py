"""Ratings inbox: merges rating files exported by the phone (or another computer) into ratings.json.

The phone saves a NEW dated file at each export (pentax-cull-ratings-<device>-<date>.json) into a folder of
your choice, typically synced by Dropbox / Google Drive / iCloud / Syncthing. This module watches that folder
while the server runs (at start, then every 30 s) and merges each new or changed file once:
- a photo is the same if its key matches, or if its file name AND shooting date match (the camera names a
  photo "100_1511/IMGP0447.DNG", a folder of the card "DCIM/100_1511/IMGP0447.DNG");
- the most recent rating wins (`updated`);
- ratings are only added or updated here, never removed: a cleared rating is an entry with rating 0.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

INTERVAL = 30
MAX_FILE = 20 * 1024 * 1024           # ~100 000 ratings: anything bigger is not a ratings file
MAX_KEY = 512
DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
SOURCES = ("camera", "folder")


def valid_date(v):
    """`YYYY-MM-DDTHH:MM:SS` that is a real date (docs/ratings-format.md)."""
    if not isinstance(v, str) or not DATE.fullmatch(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
        return True
    except ValueError:
        return False


def valid_key(k):
    return isinstance(k, str) and 0 < len(k) <= MAX_KEY and k.isprintable()


def clean_entry(key, entry):
    """A rating entry reduced to the documented fields, or None if it is not a valid rating.
    Unknown fields are dropped; a bad `datetime` / `source` is dropped; `updated` in the future (more than a day:
    clocks differ) is refused, otherwise it would win every later merge."""
    if not valid_key(key) or not isinstance(entry, dict):
        return None
    r = entry.get("rating")
    if type(r) is not int or not 0 <= r <= 5:                    # type(): JSON true is not a rating; 0 = cleared
        return None
    out = {"rating": r, "datetime": entry["datetime"] if valid_date(entry.get("datetime")) else None}
    if entry.get("source") in SOURCES:
        out["source"] = entry["source"]
    u = entry.get("updated")
    if u is not None:
        if not valid_date(u):
            return None
        if u > (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"):
            return None
        out["updated"] = u
    return out


def photo_id(key, entry):
    """(file name lowercased, shooting date): identifies a photo whatever the folder naming."""
    return key.rsplit("/", 1)[-1].lower(), (entry or {}).get("datetime")


def find_same(data, key, entry):
    """Key in `data` of the same photo as (key, entry), or None."""
    if key in data:
        return key
    name, dt = photo_id(key, entry)
    if not dt:
        return None
    for k, v in data.items():
        if photo_id(k, v) == (name, dt):
            return k
    return None


def merge(data, incoming):
    """Merges `incoming` into `data` (in place). Returns (added, updated)."""
    if not isinstance(incoming, dict):
        raise ValueError("not a ratings file (a JSON object is expected)")
    added = updated = 0
    for key, entry in incoming.items():
        entry = clean_entry(key, entry)
        if entry is None:
            continue
        same = find_same(data, key, entry)
        if same is None:
            data[key] = entry
            added += 1
        elif ((entry.get("updated") or "") > (data[same].get("updated") or "")
              and entry["rating"] != data[same].get("rating")):
            data[same] = dict(data[same], rating=entry["rating"], updated=entry.get("updated"),
                              source=entry.get("source", data[same].get("source")))
            updated += 1
    return added, updated


class Inbox:
    def __init__(self, settings_file, read_ratings, write_ratings, lock):
        self.settings_file = settings_file
        self.read_ratings, self.write_ratings, self.lock = read_ratings, write_ratings, lock
        self.last = None              # last import: {file, added, updated, at}
        self.error = None
        self.check_lock = threading.Lock()

    # settings.json: {"inbox": "/path", "imported": {"file name": mtime}}
    def _settings(self):
        try:
            with open(self.settings_file) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self, s):
        tmp = str(self.settings_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.settings_file)

    def folder(self):
        return self._settings().get("inbox")

    def set_folder(self, path):
        s = self._settings()
        s["inbox"] = os.path.realpath(os.path.expanduser(path)) if path else None
        s["imported"] = {}            # new folder: everything in it is merged once
        self._save_settings(s)
        return self.check()

    def status(self):
        return {"dir": self.folder(), "last": self.last, "error": self.error}

    def check(self):
        """Merges the new / changed files of the inbox. Returns the status."""
        with self.check_lock:
            s = self._settings()
            folder = s.get("inbox")
            if not folder:
                return self.status()
            if not os.path.isdir(folder):
                self.error = "folder not found: " + folder
                return self.status()
            self.error = None
            imported = s.setdefault("imported", {})
            changed = False
            for name in sorted(os.listdir(folder)):
                p = os.path.join(folder, name)
                if not name.lower().endswith(".json") or name.startswith(".") or not os.path.isfile(p):
                    continue
                mtime = os.path.getmtime(p)
                if imported.get(name) == mtime:
                    continue
                try:
                    if os.path.getsize(p) > MAX_FILE:
                        raise ValueError(f"too big for a ratings file (> {MAX_FILE // 2**20} MB), ignored")
                    with open(p, encoding="utf-8") as f:
                        incoming = json.load(f)
                    with self.lock:
                        data = self.read_ratings()
                        added, updated = merge(data, incoming)
                        if added or updated:
                            self.write_ratings(data)
                    self.last = {"file": name, "added": added, "updated": updated,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                except Exception as e:
                    self.error = f"{name}: {e}"
                    continue
                imported[name] = mtime
                changed = True
            if changed:
                self._save_settings(s)
            return self.status()

    def watch(self):
        def loop():
            while True:
                try:
                    self.check()
                except Exception as e:
                    self.error = str(e)
                time.sleep(INTERVAL)
        threading.Thread(target=loop, daemon=True).start()
