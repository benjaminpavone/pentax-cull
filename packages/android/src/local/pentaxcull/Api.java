package local.pentaxcull;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.UriPermission;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.webkit.WebResourceResponse;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.util.Collections;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * The viewer's API (docs/api.md), implemented on the phone: same routes and JSON as packages/desktop/server.py.
 * GET requests come from WebView request interception, POST requests from the JavaScript bridge.
 */
class Api {
    private static final Map<String, String> NO_STORE = Collections.singletonMap("Cache-Control", "no-store");
    private final Camera camera;
    private volatile Source source;                   // the camera, or a FolderSource
    private final SharedPreferences prefs;
    private CompletableFuture<Uri> picking;           // folder picker open
    private final Ratings ratings;
    private final Downloads downloads;
    private final Context app;
    private volatile MainActivity activity;           // the visible screen, if any (file picker, permission prompt)

    private static Api instance;

    /** One instance per app process: the camera link and a running download outlive the screen. */
    static synchronized Api instance(Context c) {
        if (instance == null) instance = new Api(c.getApplicationContext());
        return instance;
    }

    private Api(Context app) {
        this.app = app;
        camera = new Camera(app);
        source = camera;
        prefs = app.getSharedPreferences("folder", Context.MODE_PRIVATE);
        ratings = new Ratings(app.getFilesDir());
        downloads = new Downloads(app);
    }

    void attach(MainActivity a) { activity = a; }
    void detach(MainActivity a) { if (activity == a) activity = null; }
    Downloads downloads() { return downloads; }

    // ------------------------------------------------------------------ GET
    WebResourceResponse get(String path, Uri u) {
        try {
            String id = u.getQueryParameter("id");
            Source src = source;
            switch (path) {
                case "/api/state":
                    return json(200, state().toString());
                case "/api/photos":
                    return json(200, new JSONObject().put("photos", src.list()).toString());
                case "/api/photo/info":
                    return json(200, src.info(id).toString());
                case "/api/photo/preview":
                    return new WebResourceResponse("image/jpeg", null, 200, "OK", NO_STORE,
                            new ByteArrayInputStream(src.preview(id, u.getQueryParameter("size"))));
                case "/api/ratings":
                    return json(200, ratings.read().toString());
                case "/api/download":
                    return json(200, downloads.status().toString());
                case "/api/cache":
                    return json(200, "{\"bytes\":0,\"files\":0,\"limitBytes\":2147483648,\"dir\":\"\"}");
                default:
                    return json(404, "{\"error\":\"not found\"}");
            }
        } catch (Exception e) {
            return json(502, err(e).toString());
        }
    }

    // ------------------------------------------------------------------ POST
    String post(String path, String body) {
        try {
            JSONObject b = body == null || body.isEmpty() ? new JSONObject() : new JSONObject(body);
            switch (path) {
                case "/api/source":
                    return setSource(b).toString();
                case "/api/camera/connect":
                    camera.connect();
                    return ok();
                case "/api/camera/disconnect":
                    camera.disconnect();
                    return ok();
                case "/api/camera/config":
                    camera.configure(b.optString("ssid", null), b.optString("password", null));
                    return state().put("ok", true).toString();
                case "/api/ratings": {
                    Source src = source;
                    Object r = b.opt("rating"), key = b.opt("key"), dt = b.opt("datetime");
                    if (r == null || r == JSONObject.NULL) r = 0;
                    if (!(r instanceof Integer) || (Integer) r < 0 || (Integer) r > 5 || !Ratings.validKey(key))
                        return new JSONObject().put("ok", false).put("error", "invalid rating").toString();
                    int rating = (Integer) r;
                    int count = ratings.save((String) key, rating, Ratings.validDate(dt) ? dt : null, src.type());
                    JSONObject out = new JSONObject().put("ok", true).put("count", count);
                    if (b.optBoolean("writeFile")) {
                        if (!src.writable()) out.put("fileOk", false).put("fileError", "read-only source");
                        else try {
                            out.put("fileOk", true).put("fileMode", src.writeRating(b.getString("id"), rating));
                        } catch (Exception e) {
                            out.put("fileOk", false).put("fileError", err(e).optString("error"));
                        }
                    }
                    return out.toString();
                }
                case "/api/download": {
                    if (downloads.running()) return new JSONObject().put("ok", false).put("busy", true).toString();
                    org.json.JSONArray items = b.optJSONArray("items");
                    if (items == null || items.length() == 0)
                        return new JSONObject().put("ok", false).put("error", "nothing to download").toString();
                    MainActivity a = activity;                     // notifications: progress while the phone is locked
                    if (a != null && a.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                        a.runOnUiThread(() -> a.requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2));
                    downloads.start(source, items, b.optBoolean("writeRating", true));
                    return downloads.status().put("ok", true).toString();
                }
                case "/api/ratings/export": {
                    JSONObject data = ratings.read();
                    if (data.length() == 0) return new JSONObject().put("ok", false).put("error", "no ratings yet").toString();
                    MainActivity a = activity;
                    if (a == null) return new JSONObject().put("ok", false).put("error", "app not visible").toString();
                    a.exportRatings(data.toString(1).replace("\\/", "/"), Ratings.rated(data));
                    return ok();
                }
                case "/api/download/cancel":
                    downloads.cancel();
                    return ok();
                case "/api/download/reveal":
                    downloads.reveal();
                    return ok();
                default:
                    return new JSONObject().put("ok", false).put("error", "not available yet on Android").toString();
            }
        } catch (Exception e) {
            return err(e).toString();
        }
    }

    private JSONObject state() throws Exception {
        Source s = source;
        JSONObject cam = s == camera ? camera.status()
                : new JSONObject().put("cameraSsid", camera.ssid()).put("busy", JSONObject.NULL);
        return new JSONObject().put("type", s.type()).put("label", s.label())
                .put("writable", s.writable()).put("removable", s.removable())
                .put("camera", cam);
    }

    // ------------------------------------------------------------------ source
    /**
     * {type: "camera"} or {type: "folder", path?}. Folder with a path: the last chosen folder again (its access
     * permission is kept); without a path, or if that access was lost: the system folder picker.
     */
    private JSONObject setSource(JSONObject b) throws Exception {
        Source old = source;
        if ("folder".equals(b.optString("type"))) {
            String saved = prefs.getString("tree", null);
            FolderSource f = null;
            if (!b.optString("path").isEmpty() && saved != null) {
                if (old instanceof FolderSource && ((FolderSource) old).tree.toString().equals(saved))
                    return state().put("ok", true);
                try { f = new FolderSource(app, Uri.parse(saved)); } catch (Exception ignored) { }
            }
            if (f == null) {
                Uri tree = pickFolder(saved);
                if (tree == null) return new JSONObject().put("ok", false).put("cancelled", true);
                if (tree.equals(Uri.EMPTY)) return new JSONObject().put("ok", false).put("busy", true);
                int rw = Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION;
                app.getContentResolver().takePersistableUriPermission(tree, rw);
                for (UriPermission p : app.getContentResolver().getPersistedUriPermissions()) {   // only one kept
                    if (!p.getUri().equals(tree)) app.getContentResolver().releasePersistableUriPermission(p.getUri(), rw);
                }
                prefs.edit().putString("tree", tree.toString()).apply();
                f = new FolderSource(app, tree);
            }
            source = f;
        } else {
            source = camera;
        }
        if (old != source && old instanceof FolderSource) old.close();
        return state().put("ok", true);
    }

    /** Opens the system folder picker and waits for the choice: the tree URI, null if cancelled, Uri.EMPTY if busy. */
    private Uri pickFolder(String initial) throws Exception {
        MainActivity a = activity;
        if (a == null) throw new Exception("app not visible");
        CompletableFuture<Uri> f;
        synchronized (this) {
            if (picking != null) return Uri.EMPTY;
            f = picking = new CompletableFuture<>();
        }
        try {
            a.pickFolder(initial == null ? null : Uri.parse(initial));
            return f.get(10, TimeUnit.MINUTES);
        } catch (java.util.concurrent.TimeoutException e) {
            return null;
        } finally {
            synchronized (this) { picking = null; }
        }
    }

    /** From MainActivity.onActivityResult: the chosen folder, or null. */
    synchronized void folderPicked(Uri tree) {
        if (picking != null) picking.complete(tree);
    }

    private static String ok() {
        return "{\"ok\":true}";
    }

    private static JSONObject err(Exception e) {
        try {
            return new JSONObject().put("ok", false).put("error", e.getMessage() == null ? e.toString() : e.getMessage());
        } catch (Exception ignored) {
            return new JSONObject();
        }
    }

    static WebResourceResponse json(int status, String body) {
        return new WebResourceResponse("application/json", "utf-8", status, status == 200 ? "OK" : "Error",
                NO_STORE, MainActivity.stream(body));
    }
}
