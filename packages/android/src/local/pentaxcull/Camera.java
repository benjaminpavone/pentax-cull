package local.pentaxcull;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.wifi.WifiNetworkSpecifier;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.Semaphore;

/**
 * The Pentax camera: its Wi-Fi (joined with a WifiNetworkSpecifier: Android asks the user once, and the
 * phone keeps mobile data for everything else) and its read-only HTTP API (see docs/api.md).
 * Same behaviour as packages/desktop/sources/camera.py.
 */
class Camera implements Source {
    static final String HOST = "192.168.0.1";
    static final int CHUNK = 256 * 1024;

    private final ConnectivityManager cm;
    private final SharedPreferences prefs;
    // Measured on a K-1 II: 2 requests at once double the throughput, 3 make the camera time out for ~30 s.
    private final Semaphore slots = new Semaphore(2);
    private ConnectivityManager.NetworkCallback callback;
    private volatile Network network;
    private volatile String busy, error;
    private volatile long lastOk, connectStart;

    Camera(Context ctx) {
        cm = ctx.getSystemService(ConnectivityManager.class);
        prefs = ctx.getSharedPreferences("camera", Context.MODE_PRIVATE);
    }

    public String type() { return "camera"; }
    public String label() { return "Camera " + ssid(); }
    public boolean writable() { return false; }
    public boolean removable() { return false; }
    public String writeRating(String id, int rating) throws IOException { throw new IOException("read-only source"); }
    public void close() { }                     // the Wi-Fi link is kept: leaving it is the user's choice

    String ssid() { return prefs.getString("ssid", ""); }
    boolean hasPassword() { return !prefs.getString("password", "").isEmpty(); }

    void configure(String ssid, String password) {
        SharedPreferences.Editor e = prefs.edit();
        if (ssid != null) e.putString("ssid", ssid.trim());
        if (password != null && !password.isEmpty()) e.putString("password", password);
        e.apply();
    }

    // ------------------------------------------------------------------ Wi-Fi
    synchronized void connect() {
        if (busy != null) return;
        if (network != null) { error = null; return; }
        String ssid = ssid(), pw = prefs.getString("password", "");
        if (ssid.isEmpty() || pw.isEmpty()) {
            error = "Set the camera Wi-Fi name and password in Settings (⚙)";
            return;
        }
        if (callback != null) {                     // previous request (network lost since): released first
            try { cm.unregisterNetworkCallback(callback); } catch (Exception ignored) { }
            callback = null;
        }
        error = null;
        busy = "connect";
        connectStart = System.currentTimeMillis();
        WifiNetworkSpecifier spec = new WifiNetworkSpecifier.Builder().setSsid(ssid).setWpa2Passphrase(pw).build();
        NetworkRequest req = new NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)   // the camera has no internet
                .setNetworkSpecifier(spec)
                .build();
        callback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network n) {
                network = n;
                new Thread(() -> {
                    // the camera's HTTP server may need a few seconds after the Wi-Fi is up
                    for (int i = 0; i < 20 && network != null; i++) {
                        if (ping()) { lastOk = System.currentTimeMillis(); busy = null; return; }
                        sleep(1000);
                    }
                    error = "The camera does not answer on its Wi-Fi";
                    busy = null;
                }).start();
            }
            @Override public void onUnavailable() {
                error = "Camera Wi-Fi not found or connection refused (camera on? Wi-Fi enabled? right password?)";
                busy = null;
                callback = null;
            }
            @Override public void onLost(Network n) {
                if (n.equals(network)) network = null;
            }
        };
        cm.requestNetwork(req, callback, 120_000);
    }

    synchronized void disconnect() {
        if (callback != null) {
            try { cm.unregisterNetworkCallback(callback); } catch (Exception ignored) { }
        }
        callback = null;
        network = null;
        busy = null;
    }

    private boolean ping() {
        Network n = network;
        if (n == null) return false;
        if (!slots.tryAcquire()) return true;      // both slots busy: the camera is obviously alive
        try {
            HttpURLConnection c = (HttpURLConnection) n.openConnection(new URL("http://" + HOST + "/v1/ping"));
            c.setConnectTimeout(1500);
            c.setReadTimeout(1500);
            return new JSONObject(new String(readAll(c.getInputStream()), "UTF-8")).optInt("errCode") == 200;
        } catch (Exception e) {
            return false;
        } finally {
            slots.release();
        }
    }

    JSONObject status() throws Exception {
        boolean on = network != null;
        boolean reachable = on && busy == null && (System.currentTimeMillis() - lastOk < 10_000 || ping());
        if (reachable) lastOk = Math.max(lastOk, System.currentTimeMillis() - 5_000);
        String ssid = ssid();
        return new JSONObject()
                .put("ssid", on ? ssid : JSONObject.NULL)
                .put("cameraSsid", ssid)
                .put("hasPassword", hasPassword())
                .put("onCameraWifi", on)
                .put("reachable", reachable)
                .put("busy", busy == null ? JSONObject.NULL : busy)
                .put("error", error == null ? JSONObject.NULL : error)
                .put("waited", busy == null ? 0 : (int) ((System.currentTimeMillis() - connectStart) / 1000));
    }

    // ------------------------------------------------------------------ HTTP API
    static class Resp {
        final int status; final String type; final byte[] body;
        Resp(int status, String type, byte[] body) { this.status = status; this.type = type; this.body = body; }
    }

    private HttpURLConnection open(String path, int timeoutMs) throws IOException {
        Network n = network;
        if (n == null) throw new IOException("not connected to the camera");
        HttpURLConnection c = (HttpURLConnection) n.openConnection(new URL("http://" + HOST + path));
        c.setConnectTimeout(5000);
        c.setReadTimeout(timeoutMs);
        return c;
    }

    Resp get(String path) throws IOException {
        slots.acquireUninterruptibly();
        try {
            HttpURLConnection c = open(path, 20_000);
            int code = c.getResponseCode();
            byte[] body = readAll(code >= 400 ? c.getErrorStream() : c.getInputStream());
            lastOk = System.currentTimeMillis();
            return new Resp(code, c.getContentType() == null ? "" : c.getContentType(), body);
        } finally {
            slots.release();
        }
    }

    JSONObject getJson(String path) throws Exception {
        JSONObject j = new JSONObject(new String(get(path).body, "UTF-8"));
        if (j.optInt("errCode", 200) != 200) throw new IOException(j.optString("errMsg", "camera error"));
        return j;
    }

    public JSONArray list() throws Exception {
        JSONArray dirs = getJson("/v1/photos").getJSONArray("dirs"), out = new JSONArray();
        for (int i = 0; i < dirs.length(); i++) {
            JSONObject d = dirs.getJSONObject(i);
            JSONArray files = d.getJSONArray("files");
            for (int k = 0; k < files.length(); k++) {
                String f = files.getString(k), lf = f.toLowerCase();
                if (!(lf.endsWith(".dng") || lf.endsWith(".jpg") || lf.endsWith(".jpeg") || lf.endsWith(".pef"))) continue;
                String id = d.getString("name") + "/" + f;
                out.put(new JSONObject().put("id", id).put("key", id).put("group", d.getString("name")).put("name", f));
            }
        }
        return out;
    }

    public JSONObject info(String id) throws Exception {
        JSONObject i = getJson("/v1/photos/" + id + "/info"), out = new JSONObject();
        for (String k : new String[]{"datetime", "av", "tv", "sv", "orientation", "cameraModel"}) out.put(k, i.opt(k));
        return out;
    }

    /** The camera only offers a 720x480 preview (size=view), whatever is asked. */
    public byte[] preview(String id, String size) throws IOException {
        Resp r = get("/v1/photos/" + id + "?size=view");
        if (!r.type.startsWith("image/")) throw new IOException("preview unavailable");
        return r.body;
    }

    /** Streams the original file into `out`. Returns false if progress() asked to stop. HTTP Range is not supported. */
    public boolean fetch(String id, OutputStream out, Progress progress) throws IOException {
        slots.acquireUninterruptibly();
        try {
            HttpURLConnection c = open("/v1/photos/" + id + "?size=full", 30_000);
            if (c.getContentType() != null && c.getContentType().startsWith("application/json"))
                throw new IOException("camera error: " + new String(readAll(c.getInputStream()), "UTF-8"));
            long size = c.getContentLengthLong(), done = 0;
            try (InputStream in = c.getInputStream()) {
                byte[] buf = new byte[CHUNK];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    done += n;
                    lastOk = System.currentTimeMillis();
                    if (!progress.update(done, size)) return false;
                }
            }
            if (size > 0 && done != size) throw new IOException("incomplete transfer (" + done + "/" + size + " bytes)");
            return true;
        } finally {
            slots.release();
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        if (in == null) return new byte[0];
        try (InputStream i = in) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = i.read(buf)) > 0) b.write(buf, 0, n);
            return b.toByteArray();
        }
    }

    static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) { }
    }
}
