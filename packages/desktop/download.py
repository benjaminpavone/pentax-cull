"""Background download/copy of selected photos to a local folder.

Works with any source exposing `fetch(id, out_file, progress)` and `info(id)`: the camera streams the original
file over Wi-Fi, a folder source copies it. A photo already in the destination is skipped. Another photo with the
same file name (two cards, the camera counter wrapping around) is saved as NAME-2.DNG, NAME-3.DNG... Ratings can be
written into the downloaded DNGs (xmp:Rating), so Lightroom shows the stars right at import.
"""
import os
import threading

import dng


class DownloadJob:
    def __init__(self, source, items, dest, write_rating=True):
        self.source = source
        self.items = items                    # [{"id", "rating"}]
        self.dest = dest
        self.write_rating = write_rating
        self.cancelled = False
        self.state = {"running": True, "dest": dest, "total": len(items), "done": 0, "downloaded": 0,
                      "skipped": 0, "failed": [], "current": None, "bytes": 0, "size": 0, "cancelled": False}
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def cancel(self):
        self.cancelled = True

    def status(self):
        return dict(self.state, failed=list(self.state["failed"]))

    def _progress(self, done_bytes, size):
        self.state["bytes"], self.state["size"] = done_bytes, size or 0
        return not self.cancelled            # False -> the source stops the transfer

    def _target(self, pid, name):
        """(path in dest, already there?). Same photo = same name and same shooting date (DNG); a different
        photo with the same name gets a free NAME-n.EXT, so that nothing is skipped or rated by mistake."""
        stem, ext = os.path.splitext(name)
        dt = None
        for n in range(1, 1000):
            target = os.path.join(self.dest, name if n == 1 else f"{stem}-{n}{ext}")
            if not os.path.exists(target):
                return target, False
            if not target.lower().endswith(".dng"):
                return target, True            # JPEG: no date to compare, same name = same photo (as before)
            if dt is None:
                dt = self.source.info(pid).get("datetime") or ""
            try:
                same = bool(dt) and dng.info(target).get("datetime") == dt
            except Exception:
                same = False                   # unreadable file: certainly not this photo
            if same:
                return target, True
        raise RuntimeError(f"too many files named {name} in {self.dest}")

    def _run(self):
        st = self.state
        try:
            os.makedirs(self.dest, exist_ok=True)
            for it in self.items:
                if self.cancelled:
                    break
                name = os.path.basename(it["id"])
                st.update(current=name, bytes=0, size=0)
                target = None
                try:
                    target, exists = self._target(it["id"], name)
                    if exists:
                        st["skipped"] += 1    # already downloaded (partial downloads never get the final name)
                    else:
                        part = target + ".part"
                        with open(part, "wb") as fh:
                            ok = self.source.fetch(it["id"], fh, self._progress)
                        if not ok:            # cancelled mid-file
                            os.unlink(part)
                            break
                        os.replace(part, target)
                        st["downloaded"] += 1
                    if self.write_rating and it.get("rating") and target.lower().endswith(".dng"):
                        dng.write_rating(target, int(it["rating"]))
                except Exception as e:
                    st["failed"].append({"name": name, "error": str(e)})
                    if target:
                        try:
                            os.unlink(target + ".part")
                        except OSError:
                            pass
                st["done"] += 1
        finally:
            st.update(running=False, current=None, cancelled=self.cancelled)
