package local.pentaxcull;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Background copy of selected photos (from the camera or a folder / memory card) into the phone's photo library (Pictures/Pentax Cull), with the
 * rating written into the DNG (xmp:Rating, see Dng.java) so Lightroom shows the stars on import.
 * Same status JSON as packages/desktop/download.py. Files already in the folder are skipped (their rating
 * is still updated). Runs inside {@link DownloadService} (foreground service + notification), so the phone
 * can be locked meanwhile.
 */
class Downloads {
    static final String FOLDER = "Pictures/Pentax Cull/";

    private final Context ctx;                  // application context
    private volatile Source source;
    private volatile boolean cancelled;
    private volatile Uri last;
    private JSONObject state;

    Downloads(Context ctx) {
        this.ctx = ctx;
    }

    synchronized JSONObject status() throws Exception {
        if (state == null) return new JSONObject().put("running", false);
        return new JSONObject(state.toString());
    }

    synchronized boolean running() {
        return state != null && state.optBoolean("running");
    }

    synchronized void start(Source source, JSONArray items, boolean writeRating) throws Exception {
        this.source = source;
        cancelled = false;
        state = new JSONObject().put("running", true).put("dest", FOLDER).put("total", items.length()).put("done", 0)
                .put("downloaded", 0).put("skipped", 0).put("failed", new JSONArray()).put("current", JSONObject.NULL)
                .put("bytes", 0).put("size", 0).put("cancelled", false);
        DownloadService.start(ctx);
        new Thread(() -> run(items, writeRating)).start();
    }

    void cancel() { cancelled = true; }

    /** Opens the last downloaded photo in the gallery app. */
    void reveal() {
        Uri u = last;
        if (u == null) return;
        Intent i = new Intent(Intent.ACTION_VIEW).setDataAndType(u, "image/*")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try { ctx.startActivity(i); } catch (Exception ignored) { }
    }

    private synchronized void set(String k, Object v) {
        try { state.put(k, v); } catch (Exception ignored) { }
    }

    private synchronized void inc(String k) {
        set(k, state.optInt(k) + 1);
    }

    private synchronized void fail(String name, Exception e) {
        try {
            state.getJSONArray("failed").put(new JSONObject().put("name", name)
                    .put("error", e.getMessage() == null ? e.toString() : e.getMessage()));
        } catch (Exception ignored) { }
    }

    private void run(JSONArray items, boolean writeRating) {
        ContentResolver cr = ctx.getContentResolver();
        File tmpDir = new File(ctx.getCacheDir(), "downloads");
        tmpDir.mkdirs();
        try {
            for (int i = 0; i < items.length() && !cancelled; i++) {
                JSONObject it = items.optJSONObject(i);
                String id = it.optString("id"), name = id.substring(id.lastIndexOf('/') + 1);
                int rating = it.optInt("rating");
                boolean isDng = name.toLowerCase().endsWith(".dng");
                set("current", name); set("bytes", 0); set("size", 0);
                notify(i, items.length(), name, 0);
                File part = new File(tmpDir, name + ".part");
                try {
                    String[] target = target(cr, id, name);         // {file name, existing path or null}
                    String existing = target[1];
                    if (existing != null) {
                        inc("skipped");
                        // our own file: direct path access is allowed (scoped storage)
                        if (writeRating && rating > 0 && isDng) Dng.writeRating(new File(existing), rating);
                    } else {
                        boolean ok;
                        try (OutputStream out = new FileOutputStream(part)) {
                            final int index = i;
                            ok = source.fetch(id, out, (done, size) -> {
                                set("bytes", done); set("size", size);
                                notify(index, items.length(), name, size > 0 ? (int) (100 * done / size) : 0);
                                return !cancelled;
                            });
                        }
                        if (!ok) break;                                     // cancelled mid-file
                        if (writeRating && rating > 0 && isDng) Dng.writeRating(part, rating);
                        last = publish(cr, part, target[0], isDng ? "image/x-adobe-dng" : "image/jpeg");
                        inc("downloaded");
                    }
                } catch (Exception e) {
                    fail(name, e);
                } finally {
                    part.delete();
                }
                inc("done");
            }
        } finally {
            set("running", false); set("current", JSONObject.NULL); set("cancelled", cancelled);
            DownloadService.stop(ctx);
            JSONObject st = state;
            DownloadService.done(ctx, cancelled ? ctx.getString(R.string.dl_cancelled)
                    : ctx.getString(R.string.dl_done, st.optInt("downloaded"), st.optInt("skipped"),
                            st.optJSONArray("failed") == null ? 0 : st.optJSONArray("failed").length()));
        }
    }

    /**
     * {file name to use, path of that file if this photo is already there, else null}. Same photo = same name and
     * same shooting date (DNG); another photo with the same name (two cards, the camera counter wrapping around)
     * is saved as NAME-2.DNG, NAME-3.DNG… so that nothing is skipped or rated by mistake.
     */
    private String[] target(ContentResolver cr, String id, String name) throws Exception {
        int dot = name.lastIndexOf('.');
        String stem = dot > 0 ? name.substring(0, dot) : name, ext = dot > 0 ? name.substring(dot) : "";
        String dt = null;
        for (int n = 1; n < 1000; n++) {
            String fname = n == 1 ? name : stem + "-" + n + ext;
            String existing = find(cr, fname);
            if (existing == null) return new String[]{fname, null};
            if (!fname.toLowerCase().endsWith(".dng")) return new String[]{fname, existing};   // JPEG: same name = same
            if (dt == null) dt = source.info(id).optString("datetime", "");
            try (Dng.Io io = new Dng.FileIo(new File(existing), "r")) {
                if (!dt.isEmpty() && dt.equals(Dng.info(io).get("datetime"))) return new String[]{fname, existing};
            } catch (Exception ignored) { }                // unreadable: certainly not this photo
        }
        throw new Exception("too many files named " + name + " in " + FOLDER);
    }

    /** Path of `name` in our folder if we already saved it, else null. */
    private static String find(ContentResolver cr, String name) {
        String[] cols = {MediaStore.MediaColumns.DATA};
        String where = MediaStore.MediaColumns.DISPLAY_NAME + "=? AND " + MediaStore.MediaColumns.RELATIVE_PATH + "=?";
        try (Cursor c = cr.query(MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), cols, where,
                new String[]{name, FOLDER}, null)) {
            return c != null && c.moveToFirst() ? c.getString(0) : null;
        }
    }

    /** Copies the downloaded file into the photo library. A failed copy leaves nothing behind. */
    private static Uri publish(ContentResolver cr, File src, String name, String mime) throws Exception {
        ContentValues v = new ContentValues();
        v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        v.put(MediaStore.MediaColumns.MIME_TYPE, mime);
        v.put(MediaStore.MediaColumns.RELATIVE_PATH, FOLDER);
        v.put(MediaStore.MediaColumns.IS_PENDING, 1);
        Uri uri = cr.insert(MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), v);
        if (uri == null) throw new Exception("could not create the file in " + FOLDER);
        try (InputStream in = new FileInputStream(src); OutputStream out = cr.openOutputStream(uri)) {
            byte[] buf = new byte[256 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        } catch (Exception e) {
            cr.delete(uri, null, null);
            throw e;
        }
        v.clear();
        v.put(MediaStore.MediaColumns.IS_PENDING, 0);
        cr.update(uri, v, null, null);
        return uri;
    }

    private long lastNotify;

    /** Progress notification, at most twice a second. */
    private void notify(int index, int total, String name, int percent) {
        long now = System.currentTimeMillis();
        if (now - lastNotify < 500 && percent != 0) return;
        lastNotify = now;
        int overall = (int) ((index * 100L + percent) / Math.max(1, total));
        DownloadService.update(ctx, ctx.getString(R.string.dl_progress, index + 1, total, name, percent), Math.max(1, overall));
    }
}
