"""server.py over HTTP: only the viewer may use it, ratings are checked, folder mode works end to end."""

import http.client
import importlib
import json
import os
import socket
import threading
import time

import dng
import pytest
from conftest import make_dng


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("server")
    port = _free_port()
    os.environ.update(PORT=str(port), RATINGS_FILE=str(d / "ratings.json"), SETTINGS_FILE=str(d / "settings.json"))
    srv_mod = importlib.import_module("server")
    srv_mod.PORT = port
    srv_mod.RATINGS = d / "ratings.json"
    httpd = srv_mod.ThreadingHTTPServer(("127.0.0.1", port), srv_mod.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port, d
    httpd.shutdown()


def call(port, method, path, body=None, headers=None):
    h = {"Host": f"localhost:{port}"}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


# ------------------------------------------------------------------ who may talk to the server


def test_viewer_page_is_served(server):
    port, _ = server
    status, body = call(port, "GET", "/")
    assert status == 200 and b"<html" in body.lower()


@pytest.mark.parametrize("host", ["evil.example:{port}", "192.168.1.10:{port}", "localhost:1"])
def test_other_host_names_are_refused(server, host):
    """DNS rebinding: a web site whose name points to 127.0.0.1 must not read the photos."""
    port, _ = server
    assert call(port, "GET", "/api/ratings", headers={"Host": host.format(port=port)})[0] == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/plain", "Origin": "https://evil.example"},  # "simple" cross-site request
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_posts_from_other_sites_are_refused(server, headers):
    port, d = server
    status, _ = call(port, "POST", "/api/ratings", {"key": "g/EVIL.DNG", "rating": 5}, headers)
    assert status == 403
    assert "g/EVIL.DNG" not in call(port, "GET", "/api/ratings")[1]


def test_post_from_the_viewer_is_accepted(server):
    port, _ = server
    status, r = call(port, "POST", "/api/ratings", {"key": "g/OK.DNG", "rating": 4},
                     {"Origin": f"http://localhost:{port}"})
    assert status == 200 and r["ok"]


# ------------------------------------------------------------------ ratings


@pytest.mark.parametrize("body", [{"key": "g/A.DNG", "rating": 9}, {"key": "g/A.DNG", "rating": "5"},
                                  {"key": "g/A.DNG", "rating": True}, {"rating": 3}, {"key": "", "rating": 3},
                                  {"key": "g/\x07.DNG", "rating": 3}])
def test_invalid_ratings_are_refused(server, body):
    port, _ = server
    status, r = call(port, "POST", "/api/ratings", body)
    assert status == 400 and r == {"ok": False, "error": "invalid rating"}


def test_rating_saved_cleared_and_bad_date_dropped(server):
    port, _ = server
    r = call(port, "POST", "/api/ratings", {"key": "g/B.DNG", "rating": 3, "datetime": {"x": 1}})[1]
    assert r["ok"]
    assert call(port, "GET", "/api/ratings")[1]["g/B.DNG"]["datetime"] is None
    r2 = call(port, "POST", "/api/ratings", {"key": "g/B.DNG", "rating": 0})[1]
    entry = call(port, "GET", "/api/ratings")[1]["g/B.DNG"]
    assert entry["rating"] == 0 and entry["updated"]  # bug 5: the clearing is kept, with its date
    assert r2["count"] == r["count"] - 1             # count = rated photos only


def test_unreadable_ratings_file_is_never_overwritten(server):
    """Bug 1: a damaged ratings.json (Dropbox conflict, hand edit) used to be replaced by a single rating."""
    port, d = server
    f = d / "ratings.json"
    good = f.read_text()
    f.write_text(good[:-5])                          # truncated: not JSON any more
    damaged = f.read_text()
    try:
        status, r = call(port, "POST", "/api/ratings", {"key": "g/C.DNG", "rating": 5})
        assert status == 400 and not r["ok"] and "unreadable" in r["error"]
        assert f.read_text() == damaged               # untouched
        assert call(port, "GET", "/api/ratings")[0] == 502
    finally:
        f.write_text(good)
    assert call(port, "POST", "/api/ratings", {"key": "g/C.DNG", "rating": 5})[1]["ok"]


def test_missing_ratings_file_starts_empty(server):
    port, d = server
    f = d / "ratings.json"
    saved = f.read_text()
    f.unlink()
    try:
        assert call(port, "GET", "/api/ratings") == (200, {})
        assert call(port, "POST", "/api/ratings", {"key": "g/D.DNG", "rating": 2})[1]["count"] == 1
    finally:
        f.write_text(saved)


# ------------------------------------------------------------------ folder mode


@pytest.fixture(scope="module")
def folder(server, tmp_path_factory):
    port, _ = server
    root = tmp_path_factory.mktemp("card") / "K-1 II"
    (root / "DCIM/100_1511").mkdir(parents=True)
    make_dng(root / "DCIM/100_1511/IMGP0001.DNG")
    make_dng(root / "DCIM/100_1511/IMGP0002.DNG", date="2026:09:18 15:21:00")
    (root / "DCIM/100_1511/.hidden.DNG").write_bytes(b"x")
    (root / "DCIM/100_1511/readme.txt").write_text("x")
    status, r = call(port, "POST", "/api/source", {"type": "folder", "path": str(root)})
    assert status == 200 and r["ok"] and r["type"] == "folder" and r["writable"] and not r["removable"]
    return root


def test_folder_list(server, folder):
    port, _ = server
    photos = call(port, "GET", "/api/photos")[1]["photos"]
    assert photos == [
        {"id": "DCIM/100_1511/IMGP0001.DNG", "key": "K-1 II/DCIM/100_1511/IMGP0001.DNG", "group": "DCIM/100_1511",
         "name": "IMGP0001.DNG"},
        {"id": "DCIM/100_1511/IMGP0002.DNG", "key": "K-1 II/DCIM/100_1511/IMGP0002.DNG", "group": "DCIM/100_1511",
         "name": "IMGP0002.DNG"},
    ]


def test_folder_info_and_full_preview(server, folder):
    port, _ = server
    info = call(port, "GET", "/api/photo/info?id=DCIM/100_1511/IMGP0002.DNG")[1]
    assert info["datetime"] == "2026-09-18T15:21:00" and info["orientation"] == 6
    status, jpeg = call(port, "GET", "/api/photo/preview?id=DCIM/100_1511/IMGP0001.DNG&size=full")
    off, length, _ = dng.preview(str(folder / "DCIM/100_1511/IMGP0001.DNG"))
    assert status == 200 and jpeg == (folder / "DCIM/100_1511/IMGP0001.DNG").read_bytes()[off:off + length]


@pytest.mark.parametrize("pid", ["../../../etc/passwd", "/etc/passwd", "DCIM/../../outside.DNG"])
def test_no_access_outside_the_chosen_folder(server, folder, pid):
    port, _ = server
    status, r = call(port, "GET", f"/api/photo/info?id={pid}")
    assert status == 502 and "invalid path" in r["error"]


def test_rating_written_into_the_file(server, folder):
    port, _ = server
    p = folder / "DCIM/100_1511/IMGP0001.DNG"
    r = call(port, "POST", "/api/ratings", {"id": "DCIM/100_1511/IMGP0001.DNG",
                                            "key": "K-1 II/DCIM/100_1511/IMGP0001.DNG", "rating": 4,
                                            "datetime": "2026-09-18T15:20:19", "writeFile": True})[1]
    assert r["ok"] and r["fileOk"] and r["fileMode"] == "appended (new XMP block)"
    assert dng.read_rating(str(p)) == 4
    assert call(port, "GET", "/api/ratings")[1]["K-1 II/DCIM/100_1511/IMGP0001.DNG"]["rating"] == 4


def test_copy_with_rating(server, folder, tmp_path):
    port, _ = server
    dest = tmp_path / "dest"
    items = [{"id": "DCIM/100_1511/IMGP0002.DNG", "rating": 5}]
    assert call(port, "POST", "/api/download", {"items": items, "dest": str(dest), "writeRating": True})[1]["ok"]
    for _ in range(100):
        st = call(port, "GET", "/api/download")[1]
        if not st["running"]:
            break
        time.sleep(0.05)
    assert st["downloaded"] == 1 and st["failed"] == []
    assert dng.read_rating(str(dest / "IMGP0002.DNG")) == 5
    assert dng.read_rating(str(folder / "DCIM/100_1511/IMGP0002.DNG")) is None  # the original is untouched
    assert not list(dest.glob("*.part"))


def test_screen_preview_and_white_band(server, real_jpeg, tmp_path):
    """The K-1 II leaves a white band on the right of its previews: measured so that the zoom can crop it."""
    port, _ = server
    jpeg, w, h, band = real_jpeg
    d = tmp_path / "band"
    d.mkdir()
    make_dng(d / "B.DNG", jpeg=jpeg, jpeg_width=w, jpeg_height=h)
    assert call(port, "POST", "/api/source", {"type": "folder", "path": str(d)})[1]["ok"]
    status, small = call(port, "GET", "/api/photo/preview?id=B.DNG")
    assert status == 200 and small.startswith(b"\xff\xd8") and len(small) < len(jpeg)
    info = call(port, "GET", "/api/photo/info?id=B.DNG")[1]
    assert (info["cropRight"], info["cropBottom"], info["fullWidth"]) == (band, 0, w - band)


def test_damaged_band_file_is_measured_again(server, real_jpeg, tmp_path):
    """Bug 4: a half-written white-band cache file used to fail every info() call for that photo."""
    import sources.folder as folder_mod

    port, _ = server
    jpeg, w, h, band = real_jpeg
    d = tmp_path / "band2"
    d.mkdir()
    make_dng(d / "C.DNG", jpeg=jpeg, jpeg_width=w, jpeg_height=h, date="2026:09:18 16:00:00")
    assert call(port, "POST", "/api/source", {"type": "folder", "path": str(d)})[1]["ok"]
    assert call(port, "GET", "/api/photo/info?id=C.DNG")[1]["cropRight"] == band
    src = importlib.import_module("server").current["source"]
    _, _, width, key = src._where(str(d / "C.DNG"))
    f = os.path.join(folder_mod.CACHE_DIR, folder_mod._digest(f"{key}|band-v3") + ".json")
    with open(f, "w") as fh:
        fh.write('{"right": 6')                       # half written
    info = call(port, "GET", "/api/photo/info?id=C.DNG")[1]
    assert info["cropRight"] == band


def test_cached_screen_preview_does_not_read_the_photo_again(server, real_jpeg, tmp_path, monkeypatch):
    """Bug 6: every cached preview used to read the whole embedded JPEG first (gigabytes on a full card)."""
    port, _ = server
    jpeg, w, h, _ = real_jpeg
    d = tmp_path / "cached"
    d.mkdir()
    make_dng(d / "D.DNG", jpeg=jpeg, jpeg_width=w, jpeg_height=h, date="2026:09:18 17:00:00")
    assert call(port, "POST", "/api/source", {"type": "folder", "path": str(d)})[1]["ok"]
    first = call(port, "GET", "/api/photo/preview?id=D.DNG")[1]
    call(port, "GET", "/api/photo/info?id=D.DNG")
    src = importlib.import_module("server").current["source"]
    reads = []
    real_full = src._full
    monkeypatch.setattr(src, "_full", lambda p: reads.append(p) or real_full(p))
    assert call(port, "GET", "/api/photo/preview?id=D.DNG")[1] == first
    call(port, "GET", "/api/photo/info?id=D.DNG")
    assert reads == []

