"""Camera benchmark: concurrency, HTTP Range support, embedded full-res preview extraction.

Run through camera_session.sh (it joins the camera Wi-Fi and switches back):
    packages/tools/camera_session.sh python3 -I packages/tools/camera_bench.py
Writes results.json (and the extracted preview) into $OUT.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop"))
import dng  # noqa: E402

HOST = os.environ.get("CAMERA_HOST", "192.168.0.1")
OUT = os.environ.get("OUT", ".")
R = {}


def get(path, headers=None, timeout=20):
    t0 = time.time()
    req = urllib.request.Request(f"http://{HOST}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed camera URL)
            body = r.read()
            return {"ok": True, "status": r.status, "len": len(body), "s": time.time() - t0,
                    "ctype": r.headers.get("Content-Type"), "crange": r.headers.get("Content-Range"), "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "s": time.time() - t0, "err": str(e)}
    except Exception as e:
        return {"ok": False, "s": time.time() - t0, "err": str(e)}


def batch(paths, parallel):
    """Fetches `paths` with `parallel` workers. Returns total time, per-request times, errors."""
    results, lock, queue = [], threading.Lock(), list(paths)

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                p = queue.pop(0)
            r = get(p)
            r.pop("body", None)
            with lock:
                results.append(r)

    t0 = time.time()
    ths = [threading.Thread(target=worker) for _ in range(parallel)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    total = time.time() - t0
    errors = [r for r in results if not r["ok"] or not (r.get("ctype") or "").startswith("image/")]
    return {"parallel": parallel, "n": len(paths), "total_s": round(total, 2),
            "per_photo_s": round(total / len(paths), 3), "req_s": [round(r["s"], 2) for r in results],
            "errors": len(errors), "error_samples": errors[:2]}


def say(k, v):
    R[k] = v
    print(f"{k}: {json.dumps(v, ensure_ascii=False)[:400]}", flush=True)


def main():
    photos = json.loads(get("/v1/photos")["body"])
    files = [f"{d['name']}/{f}" for d in photos["dirs"] for f in d["files"] if f.lower().endswith(".dng")]
    say("photos_on_card", len(files))
    if len(files) < 20:
        sys.exit("not enough DNG files on the card for the test")

    # 1. concurrency on previews (different photos each round: no cache effect)
    def view(fs):
        return [f"/v1/photos/{f}?size=view" for f in fs]
    say("seq_1", batch(view(files[0:6]), 1))
    say("par_2", batch(view(files[6:12]), 2))
    say("par_3", batch(view(files[12:18]), 3))
    say("seq_1_again", batch(view(files[18:24] or files[0:6]), 1))

    # 2. Range support on the original file
    target = files[1]
    r = get(f"/v1/photos/{target}?size=full", {"Range": "bytes=0-262143"})
    head = r.pop("body", b"")
    say("range_head", {k: r.get(k) for k in ("ok", "status", "len", "crange", "ctype")} | {"s": round(r["s"], 2)})
    if r.get("status") != 206:
        say("conclusion_range", "NO Range support: full-res zoom in camera mode is not possible this way")
        return

    # 3. locate the embedded full-res JPEG preview from the first 256 KB, then fetch only that
    with tempfile.NamedTemporaryFile(suffix=".dng", delete=False) as tmp:
        tmp.write(head)
    try:
        with dng.Tiff(tmp.name) as t:
            best, todo, seen = None, [t.ifd0], set()
            while todo:
                off = todo.pop()
                if not off or off in seen or off > len(head) - 4:
                    continue
                seen.add(off)
                e, nxt = t.entries(off)
                todo.append(nxt)
                if dng.T_SUBIFDS in e:
                    todo.extend(t.value(e[dng.T_SUBIFDS]))
                if (t.first(e, dng.T_COMPRESSION) in (6, 7) and t.first(e, dng.T_SUBFILE, 0) & 1
                        and dng.T_STRIP_OFF in e):
                    offs, cnts, w = t.value(e[dng.T_STRIP_OFF]), t.value(e[dng.T_STRIP_CNT]), t.first(e, dng.T_WIDTH)
                    if len(offs) == 1 and (not best or w > best[2]):
                        best = (offs[0], cnts[0], w)
    finally:
        os.unlink(tmp.name)
    say("preview_location", best)
    if best:
        off, length, w = best
        r = get(f"/v1/photos/{target}?size=full", {"Range": f"bytes={off}-{off + length - 1}"}, timeout=60)
        body = r.pop("body", b"")
        valid = body[:2] == b"\xff\xd8" and body[-2:] == b"\xff\xd9"
        say("preview_fetch", {"status": r.get("status"), "len": r.get("len"), "s": round(r["s"], 2),
                              "MB_per_s": round(len(body) / r["s"] / 1e6, 2) if r["s"] else None, "valid_jpeg": valid})
        if valid:
            with open(os.path.join(OUT, "fullres_preview.jpg"), "wb") as fh:
                fh.write(body)

    # 4. throughput: one 4 MB range vs two 4 MB ranges in parallel (Wi-Fi vs camera limit)
    def rng(a):
        return f"/v1/photos/{files[2]}?size=full", {"Range": f"bytes={a}-{a + 4 * 2**20 - 1}"}
    p, h = rng(0)
    r = get(p, h, timeout=60)
    r.pop("body", None)
    say("throughput_1x4MB",
        {"s": round(r["s"], 2), "MB_per_s": round(4 * 2**20 / r["s"] / 1e6, 2), "status": r.get("status")})
    res = []
    ths = [threading.Thread(target=lambda a=a: res.append(get(*rng(a), timeout=60))) for a in (8 * 2**20, 16 * 2**20)]
    t0 = time.time()
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    tot = time.time() - t0
    say("throughput_2x4MB_parallel", {"total_s": round(tot, 2), "MB_per_s_combined": round(8 * 2**20 / tot / 1e6, 2),
                                      "statuses": [x.get("status") for x in res]})


if __name__ == "__main__":
    try:
        main()
    finally:
        with open(os.path.join(OUT, "results.json"), "w") as fh:
            json.dump(R, fh, indent=1, ensure_ascii=False, default=str)
