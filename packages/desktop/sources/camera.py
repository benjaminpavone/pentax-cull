"""Camera source: Pentax Wi-Fi (macOS networksetup) + the camera's HTTP API (read-only)."""
import json
import re
import subprocess
import threading
import time
import urllib.request

CHUNK = 256 * 1024
# Measured on a K-1 II (packages/tools/camera_bench.py, 2026-11-15): 2 concurrent requests double the preview
# throughput; 3 make the camera time out for ~30 s. HTTP Range is ignored (full file every time).
MAX_CONCURRENT = 2


def _run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()


class CameraSource:
    type = "camera"
    writable = False      # the camera API cannot write to the card

    def __init__(self, ssid, password, host="192.168.0.1", home_ssid=None):
        self.ssid, self.password, self.host = ssid, password, host
        self.lock = threading.Semaphore(MAX_CONCURRENT)   # never more than 2 requests at once (see above)
        self.net_lock = threading.Lock()
        self.net = {"busy": None, "error": None, "waited": 0}
        self.last_ok = 0                            # last successful camera request (avoids extra pings)
        self.iface = self._wifi_iface()
        cur = self.current_ssid()
        self.home_ssid = home_ssid or (cur if cur != ssid else None)

    # ------------------------------------------------------------ Wi-Fi
    def _wifi_iface(self):
        ports = _run(["networksetup", "-listallhardwareports"])
        m = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\nDevice: (\S+)", ports)
        return m.group(1) if m else "en0"

    def current_ssid(self):
        out = _run(["networksetup", "-getairportnetwork", self.iface])
        return out.split(": ", 1)[1] if out.startswith("Current Wi-Fi Network") else None

    def reachable(self):
        # counts in the 2-request limit; if both slots are busy, the camera is obviously alive
        if not self.lock.acquire(blocking=False):
            return True
        try:
            with urllib.request.urlopen(f"http://{self.host}/v1/ping", timeout=1.5) as r:
                return json.loads(r.read()).get("errCode") == 200
        except Exception:
            return False
        finally:
            self.lock.release()

    def status(self):
        ssid = self.current_ssid()
        on = ssid == self.ssid
        # no extra request if the camera answered recently: it only serves one request at a time
        reachable = on and (time.time() - self.last_ok < 10 or self.reachable())
        if reachable:
            self.last_ok = max(self.last_ok, time.time() - 5)
        return {"ssid": ssid, "cameraSsid": self.ssid, "homeSsid": self.home_ssid,
                "onCameraWifi": on, "reachable": reachable,
                "busy": self.net["busy"], "error": self.net["error"], "waited": self.net.get("waited", 0)}

    def start(self, op):
        """connect/disconnect in the background (the macOS admin prompt can take a while)."""
        if self.net["busy"]:
            return {"ok": True, "busy": self.net["busy"]}

        def work():
            self.net.update(busy=op, error=None, waited=0)
            try:
                r = self._connect() if op == "connect" else self._disconnect()
                if not r.get("ok"):
                    self.net["error"] = r.get("error") or "failed"
            except Exception as e:
                self.net["error"] = str(e)
            finally:
                self.net["busy"] = None

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "busy": op}

    def _connect(self, max_wait=90):
        """Joins the camera Wi-Fi, retrying for ~max_wait s: the camera may be asleep or still starting its Wi-Fi."""
        with self.net_lock:
            cur = self.current_ssid()
            if cur and cur != self.ssid:
                self.home_ssid = cur
            start, out = time.time(), ""
            while time.time() - start < max_wait:
                self.net["waited"] = int(time.time() - start)
                if self.current_ssid() != self.ssid:
                    # the first join may wait for the macOS admin prompt: not counted in max_wait
                    t0 = time.time()
                    out = _run(["networksetup", "-setairportnetwork", self.iface, self.ssid, self.password],
                               timeout=240)
                    start += max(0, time.time() - t0 - 15)
                for _ in range(5):
                    if self.reachable():
                        self.last_ok = time.time()
                        return {"ok": True}
                    time.sleep(1)
            return {"ok": False, "error": out or "camera unreachable (asleep? Wi-Fi off?)"}

    def _disconnect(self):
        with self.net_lock:
            _run(["networksetup", "-removepreferredwirelessnetwork", self.iface, self.ssid])
            def cycle():
                _run(["networksetup", "-setairportpower", self.iface, "off"])
                time.sleep(2)
                _run(["networksetup", "-setairportpower", self.iface, "on"])
                time.sleep(8)
            if not self.home_ssid:
                cycle()
                return {"ok": True}
            for _ in range(3):
                _run(["networksetup", "-setairportnetwork", self.iface, self.home_ssid], timeout=60)
                time.sleep(4)
                if self.current_ssid() == self.home_ssid:
                    return {"ok": True}
                cycle()
                if self.current_ssid() == self.home_ssid:
                    return {"ok": True}
            return {"ok": False, "error": "could not switch back to " + self.home_ssid}

    # ------------------------------------------------------------ camera API
    def _get(self, path, timeout=20, headers=None):
        with self.lock:
            req = urllib.request.Request(f"http://{self.host}{path}", headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed http://<camera> URL)
                self.last_ok = time.time()
                return r.read(), r.headers.get("Content-Type", ""), r.status, r.headers

    def label(self):
        return f"Camera {self.ssid}"

    def list(self):
        data = json.loads(self._get("/v1/photos")[0])
        if data.get("errCode") != 200:
            raise RuntimeError(data.get("errMsg"))
        return [{"id": f"{d['name']}/{f}", "key": f"{d['name']}/{f}", "group": d["name"], "name": f}
                for d in data["dirs"] for f in d["files"]
                if f.lower().endswith((".dng", ".jpg", ".jpeg", ".pef"))]

    def info(self, pid):
        i = json.loads(self._get(f"/v1/photos/{pid}/info")[0])
        if i.get("errCode") != 200:
            raise RuntimeError(i.get("errMsg"))
        return {k: i.get(k) for k in ("datetime", "av", "tv", "sv", "orientation", "cameraModel")}

    def preview(self, pid, size="screen"):
        # the camera only offers 720x480 (size=view), whatever size is asked
        body, ctype, _, _ = self._get(f"/v1/photos/{pid}?size=view")
        if not ctype.startswith("image/"):
            raise RuntimeError("preview unavailable")
        return body, ctype

    def fetch(self, pid, out, progress):
        """Streams the original file (size=full) into `out`. Returns False if progress() asked to stop.
        Uses one of the 2 request slots for the whole file (~1 min at ~0.85 MB/s): previews use the other one.
        HTTP Range is not supported by the camera, so the file cannot be fetched in pieces."""
        with self.lock:
            with urllib.request.urlopen(f"http://{self.host}/v1/photos/{pid}?size=full", timeout=30) as r:
                if r.headers.get("Content-Type", "").startswith("application/json"):
                    raise RuntimeError(json.loads(r.read()).get("errMsg", "camera error"))
                size = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    self.last_ok = time.time()
                    if not progress(done, size):
                        return False
                if size and done != size:
                    raise RuntimeError(f"incomplete transfer ({done}/{size} bytes)")
        return True
