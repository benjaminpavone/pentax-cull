package local.pentaxcull;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.window.OnBackInvokedDispatcher;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * Hosts the shared viewer (packages/viewer, copied into assets/viewer at build time) in a WebView.
 *
 * The page is served from a fake https origin. Requests to it are answered here:
 * - /api/... GET  -> {@link Api#get} (JSON or JPEG), same HTTP contract as the Mac server (docs/api.md);
 * - /api/... POST -> window.AndroidBridge.post() (a WebView cannot read POST bodies), see api.js;
 * - anything else -> the viewer's static files.
 */
public class MainActivity extends Activity {
    static final String HOST = "pentaxcull.local";
    static final String ORIGIN = "https://" + HOST + "/";

    private static final int REQ_EXPORT = 1, REQ_TREE = 2;
    private WebView web;
    private Api api;
    private String exportJson;
    private int exportCount;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        api = Api.instance(this);
        api.attach(this);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        // Only our own page is ever shown: window.AndroidBridge (notes, copies, writing into files) must never be
        // reachable from another site, so navigation elsewhere and any outside request are refused.
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return !HOST.equals(req.getUrl().getHost()) || !"https".equals(req.getUrl().getScheme());
            }

            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (!HOST.equals(u.getHost()) || !"https".equals(u.getScheme())) {
                    if ("data".equals(u.getScheme()) || "blob".equals(u.getScheme())) return null;   // page-made images
                    return Api.json(403, "{\"error\":\"blocked\"}");
                }
                String path = u.getPath() == null ? "/" : u.getPath();
                if (path.startsWith("/api/")) return api.get(path, u);
                return asset(path.equals("/") ? "/index.html" : path);
            }
        });
        web.addJavascriptInterface(new Bridge(), "AndroidBridge");
        // Android 15+ draws apps edge to edge: keep the page clear of the status and navigation bars.
        // The padding goes on a container (a WebView ignores its own padding).
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF1B1B1B);
        root.addView(web, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            Insets b = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            v.setPadding(b.left, b.top, b.right, b.bottom);
            return WindowInsets.CONSUMED;
        });
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::onBack);
        }
        if (saved != null) web.restoreState(saved);
        else web.loadUrl(ORIGIN + "index.html");
    }

    @Override
    protected void onDestroy() {
        api.detach(this);
        super.onDestroy();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    /** Back = Escape in the viewer (closes a dialog, the zoom or the grid); leaves the app from the plain view. */
    private void onBack() {
        web.evaluateJavascript("window.androidBack ? androidBack() : false", v -> {
            if (!"true".equals(v)) moveTaskToBack(true);
        });
    }

    /**
     * Ratings export: the system file picker (Dropbox, Drive, Files…) creates a NEW dated file, so that a synced
     * folder never gets two versions of the same file. The Mac merges each file once (packages/desktop/inbox.py).
     */
    void exportRatings(String json, int count) {
        String model = Build.MODEL.replaceAll("[^A-Za-z0-9]+", "");
        String name = "pentax-cull-ratings-" + model + "-"
                + java.time.LocalDateTime.now().format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")) + ".json";
        runOnUiThread(() -> {
            exportJson = json;
            exportCount = count;
            Intent i = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("application/json").putExtra(Intent.EXTRA_TITLE, name);
            startActivityForResult(i, REQ_EXPORT);
        });
    }

    /** System folder picker (memory card in a USB-C reader, phone folder…). The answer goes to Api.folderPicked. */
    void pickFolder(Uri initial) {
        runOnUiThread(() -> {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
            if (initial != null) i.putExtra(android.provider.DocumentsContract.EXTRA_INITIAL_URI, initial);
            try {
                startActivityForResult(i, REQ_TREE);
            } catch (Exception e) {
                api.folderPicked(null);
            }
        });
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (req == REQ_TREE) {
            api.folderPicked(result == RESULT_OK && data != null ? data.getData() : null);
            return;
        }
        if (req != REQ_EXPORT || exportJson == null) return;
        String json = exportJson;
        exportJson = null;
        if (result != RESULT_OK || data == null || data.getData() == null) return;     // cancelled
        Uri uri = data.getData();
        try (OutputStream out = getContentResolver().openOutputStream(uri, "wt")) {
            out.write(json.getBytes(StandardCharsets.UTF_8));
            String name = uri.getLastPathSegment();
            try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
                if (c != null && c.moveToFirst()) name = c.getString(0);
            } catch (Exception ignored) { }
            callJs("onRatingsExported", true, name, exportCount);
        } catch (Exception e) {
            callJs("onRatingsExported", false, String.valueOf(e.getMessage()), 0);
        }
    }

    private void callJs(String fn, boolean ok, String text, int n) {
        String q = org.json.JSONObject.quote(text);
        web.post(() -> web.evaluateJavascript("window." + fn + " && " + fn + "(" + ok + "," + q + "," + n + ")", null));
    }

    private WebResourceResponse asset(String path) {
        try {
            InputStream in = getAssets().open("viewer" + path);
            return new WebResourceResponse(mime(path), "utf-8", in);
        } catch (IOException e) {
            return Api.json(404, "{\"error\":\"not found\"}");
        }
    }

    private static final Map<String, String> MIME = new HashMap<>();
    static {
        MIME.put("html", "text/html"); MIME.put("js", "text/javascript"); MIME.put("css", "text/css");
        MIME.put("json", "application/json"); MIME.put("png", "image/png"); MIME.put("svg", "image/svg+xml");
    }

    private static String mime(String path) {
        String ext = path.substring(path.lastIndexOf('.') + 1).toLowerCase();
        String m = MIME.get(ext);
        return m != null ? m : "application/octet-stream";
    }

    /** Called from api.js for POST requests: returns the JSON response as a string. Runs on a WebView thread. */
    class Bridge {
        @JavascriptInterface
        public String post(String path, String body) {
            return api.post(path, body);
        }
    }

    static InputStream stream(String s) {
        return new ByteArrayInputStream(s.getBytes(StandardCharsets.UTF_8));
    }
}
