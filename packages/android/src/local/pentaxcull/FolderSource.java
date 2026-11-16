package local.pentaxcull;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.BitmapRegionDecoder;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.media.ExifInterface;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.os.storage.StorageManager;
import android.os.storage.StorageVolume;
import android.provider.DocumentsContract;
import android.provider.DocumentsContract.Document;
import android.system.ErrnoException;
import android.system.Os;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Folder source: DNG/JPEG files of a folder chosen with the system picker (storage access framework), e.g. a
 * memory card in a USB-C reader or a folder of the phone. Same behaviour as packages/desktop/sources/folder.py:
 * - size="full"   : the full-resolution JPEG embedded in the DNG (7360 px on a K-1 II), as is -> zoom
 * - size="screen" : a 1840 px version (JPEG decoded at 1/4: fast), cached in the app's cache folder and prepared
 *                   in the background for the whole folder when it is opened.
 * Photo ids are paths relative to the chosen folder; only listed ids are accepted.
 */
class FolderSource implements Source {
    static final int SCREEN_PX = 1840, WHITE = 220, WORKERS = 2;
    static final long CACHE_LIMIT = 1024L * 1024 * 1024;

    private final ContentResolver cr;
    private final Context ctx;
    final Uri tree;
    private final String name, label;
    private final boolean removable;
    private final File cacheDir;
    private final Map<String, String> docs = new ConcurrentHashMap<>();      // id -> document id
    private final Map<String, Long> sizes = new ConcurrentHashMap<>();
    private final Map<String, long[]> locs = new ConcurrentHashMap<>();      // id -> embedded JPEG {offset, length, width}
    private final Map<String, Object> locks = new ConcurrentHashMap<>();
    private final Object writeLock = new Object();
    private final ExecutorService pool = Executors.newFixedThreadPool(WORKERS);
    private volatile boolean closed;

    FolderSource(Context ctx, Uri tree) throws IOException {
        this.ctx = ctx;
        this.cr = ctx.getContentResolver();
        this.tree = tree;
        String root = DocumentsContract.getTreeDocumentId(tree);
        // "primary:Pictures/Pentax Cull", "1234-ABCD:DCIM" (USB drive / SD card)… for the external storage provider
        boolean ext = "com.android.externalstorage.documents".equals(tree.getAuthority());
        int c = root.indexOf(':');
        String vol = ext && c >= 0 ? root.substring(0, c) : "", rel = ext && c >= 0 ? root.substring(c + 1) : "";
        String rootName = displayName(DocumentsContract.buildDocumentUriUsingTree(tree, root));
        if (rootName == null) throw new IOException("folder not accessible (permission lost?): choose it again");
        String volName = volumeName(vol);
        name = !rel.isEmpty() ? rel.substring(rel.lastIndexOf('/') + 1) : volName != null ? volName : rootName;
        label = ext ? (volName != null ? volName : vol) + (rel.isEmpty() ? "" : "/" + rel) : rootName;
        removable = ext && !"primary".equals(vol);
        cacheDir = new File(ctx.getCacheDir(), "previews");
        cacheDir.mkdirs();
    }

    public String type() { return "folder"; }
    public String label() { return label; }
    public boolean writable() { return true; }
    public boolean removable() { return removable; }

    public void close() {
        closed = true;
        pool.shutdownNow();
    }

    private String displayName(Uri doc) {
        try (Cursor c = cr.query(doc, new String[]{Document.COLUMN_DISPLAY_NAME}, null, null, null)) {
            return c != null && c.moveToFirst() ? c.getString(0) : null;
        } catch (Exception e) {
            return null;
        }
    }

    private String volumeName(String uuid) {
        if (uuid.isEmpty()) return null;
        for (StorageVolume v : ctx.getSystemService(StorageManager.class).getStorageVolumes()) {
            if ("primary".equals(uuid) ? v.isPrimary() : uuid.equalsIgnoreCase(v.getUuid())) return v.getDescription(ctx);
        }
        return null;
    }

    private Uri uri(String id) throws IOException {
        String doc = docs.get(id);
        if (doc == null) throw new IOException("unknown photo: " + id);
        return DocumentsContract.buildDocumentUriUsingTree(tree, doc);
    }

    private static boolean isJpeg(String n) {
        String l = n.toLowerCase();
        return l.endsWith(".jpg") || l.endsWith(".jpeg");
    }

    private static boolean isDng(String n) {
        return n.toLowerCase().endsWith(".dng");
    }

    // ------------------------------------------------------------------ listing
    public JSONArray list() throws Exception {
        docs.clear();
        sizes.clear();
        JSONArray out = new JSONArray();
        walk(DocumentsContract.getTreeDocumentId(tree), "", out);
        // screen previews prepared in the background, in list order
        for (int i = 0; i < out.length(); i++) {
            String id = out.getJSONObject(i).getString("id");
            pool.execute(() -> { if (!closed) try { screen(id); } catch (Exception ignored) { } });
        }
        return out;
    }

    private void walk(String docId, String rel, JSONArray out) throws Exception {
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId);
        List<String[]> dirs = new ArrayList<>(), files = new ArrayList<>();
        try (Cursor c = cr.query(children, new String[]{Document.COLUMN_DOCUMENT_ID, Document.COLUMN_DISPLAY_NAME,
                Document.COLUMN_MIME_TYPE, Document.COLUMN_SIZE}, null, null, null)) {
            if (c == null) throw new IOException("cannot read the folder");
            while (c.moveToNext()) {
                String n = c.getString(1);
                if (n == null || n.startsWith(".")) continue;
                if (Document.MIME_TYPE_DIR.equals(c.getString(2))) dirs.add(new String[]{c.getString(0), n});
                else if (isDng(n) || isJpeg(n)) files.add(new String[]{c.getString(0), n, String.valueOf(c.getLong(3))});
            }
        }
        Comparator<String[]> byName = Comparator.comparing(a -> a[1]);
        files.sort(byName);
        dirs.sort(byName);
        for (String[] f : files) {
            String id = rel.isEmpty() ? f[1] : rel + "/" + f[1];
            docs.put(id, f[0]);
            sizes.put(id, Long.parseLong(f[2]));
            out.put(new JSONObject().put("id", id).put("key", name + "/" + id)
                    .put("group", rel.isEmpty() ? name : rel).put("name", f[1]));
        }
        for (String[] d : dirs) walk(d[0], rel.isEmpty() ? d[1] : rel + "/" + d[1], out);
    }

    // ------------------------------------------------------------------ info
    public JSONObject info(String id) throws Exception {
        JSONObject out = new JSONObject();
        if (isJpeg(id)) {
            try (InputStream in = cr.openInputStream(uri(id))) {
                String dt = new ExifInterface(in).getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL);
                if (dt != null && dt.length() >= 19) out.put("datetime", dt.substring(0, 10).replace(':', '-') + "T" + dt.substring(11, 19));
            } catch (Exception ignored) { }
            return out.put("orientation", 1);       // the screen preview is already upright
        }
        try (FdIo io = new FdIo(cr, uri(id), "r")) {
            for (Map.Entry<String, Object> e : Dng.info(io).entrySet()) out.put(e.getKey(), e.getValue());
        }
        long[] loc = loc(id);
        if (loc != null) {
            int[] band = band(id);
            // for the zoom: useful (unrotated) width of the full-resolution preview + white band to crop
            out.put("fullWidth", loc[2] - band[0]).put("cropRight", band[0]).put("cropBottom", band[1]);
        }
        return out;
    }

    private long[] loc(String id) throws IOException {
        long[] l = locs.get(id);
        if (l == null) {
            try (FdIo io = new FdIo(cr, uri(id), "r")) { l = Dng.preview(io); }
            if (l == null) l = new long[0];
            locs.put(id, l);
        }
        return l.length == 0 ? null : l;
    }

    // ------------------------------------------------------------------ previews
    public byte[] preview(String id, String size) throws Exception {
        return "full".equals(size) ? full(id) : screen(id);
    }

    /** The full-resolution JPEG: embedded in a DNG, or the JPEG file itself. */
    private byte[] full(String id) throws IOException {
        if (isJpeg(id)) {
            try (InputStream in = cr.openInputStream(uri(id))) { return Camera.readAll(in); }
        }
        long[] l = loc(id);
        if (l == null) throw new IOException("no embedded JPEG preview");
        byte[] b = new byte[(int) l[1]];
        try (FdIo io = new FdIo(cr, uri(id), "r")) { io.read(l[0], b); }
        return b;
    }

    /** Cache key: the embedded preview does not move when a rating is written. */
    private String key(String id) throws IOException {
        long[] l = isJpeg(id) ? null : loc(id);
        return tree + "|" + docs.get(id) + "|" + (l != null ? l[0] + "|" + l[1] : sizes.get(id));
    }

    private Object lock(String k) {
        return locks.computeIfAbsent(k, x -> new Object());
    }

    private byte[] screen(String id) throws Exception {
        String key = key(id);
        File f = new File(cacheDir, sha1(key + "|" + SCREEN_PX + "|v1") + ".jpg");
        byte[] hit = readCached(f);
        if (hit != null) return hit;
        synchronized (lock(f.getName())) {                  // one generation per photo
            hit = readCached(f);
            if (hit != null) return hit;
            byte[] data = full(id);
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(data, 0, data.length, o);
            int w = o.outWidth, h = o.outHeight;
            if (w <= 0) throw new IOException("unreadable preview");
            int orientation = isJpeg(id) ? jpegOrientation(data) : 1;
            if (Math.max(w, h) <= SCREEN_PX && orientation == 1) {
                if (!isJpeg(id)) saveBand(key, new int[]{0, 0});
                return data;
            }
            int sample = 1;                                 // 7360 px -> 1/4: fast JPEG decoding
            while (Math.max(w, h) / (sample * 2) >= SCREEN_PX) sample *= 2;
            BitmapFactory.Options d = new BitmapFactory.Options();
            d.inSampleSize = sample;
            Bitmap bm = BitmapFactory.decodeByteArray(data, 0, data.length, d);
            if (bm == null) throw new IOException("unreadable preview");
            if (!isJpeg(id)) saveBand(key, measureBand(bm, data, w, h));   // read by info() (zoom)
            int m = Math.max(bm.getWidth(), bm.getHeight());
            Matrix mx = new Matrix();
            if (m > SCREEN_PX) mx.postScale((float) SCREEN_PX / m, (float) SCREEN_PX / m);
            mx.postRotate(orientation == 6 ? 90 : orientation == 3 ? 180 : orientation == 8 ? 270 : 0);
            if (!mx.isIdentity()) {
                Bitmap s = Bitmap.createBitmap(bm, 0, 0, bm.getWidth(), bm.getHeight(), mx, true);
                if (s != bm) bm.recycle();
                bm = s;
            }
            ByteArrayOutputStream jpg = new ByteArrayOutputStream(400_000);
            bm.compress(Bitmap.CompressFormat.JPEG, 85, jpg);
            bm.recycle();
            byte[] out = jpg.toByteArray();
            File part = new File(f.getPath() + ".part");
            try (FileOutputStream fo = new FileOutputStream(part)) { fo.write(out); }
            if (part.renameTo(f)) prune();
            return out;
        }
    }

    private static int jpegOrientation(byte[] data) {
        try {
            return new ExifInterface(new ByteArrayInputStream(data)).getAttributeInt(ExifInterface.TAG_ORIENTATION, 1);
        } catch (Exception e) {
            return 1;
        }
    }

    private static byte[] readCached(File f) {
        try {
            byte[] b = Files.readAllBytes(f.toPath());
            f.setLastModified(System.currentTimeMillis());  // most recently used: pruned last
            return b;
        } catch (IOException e) {
            return null;
        }
    }

    /** Keeps the preview cache under CACHE_LIMIT: deletes the least recently used previews (down to 90 %). */
    private void prune() {
        File[] all = cacheDir.listFiles((dir, n) -> n.endsWith(".jpg"));
        if (all == null) return;
        long total = 0;
        for (File x : all) total += x.length();
        if (total <= CACHE_LIMIT) return;
        Arrays.sort(all, Comparator.comparingLong(File::lastModified));
        for (File x : all) {
            if (total <= CACHE_LIMIT * 9 / 10) break;
            long n = x.length();
            if (x.delete()) total -= n;
        }
    }

    // ------------------------------------------------------------------ white band
    // The 7360 px preview written by the K-1 II only holds the image on 7296 px: the last 64 columns are white.
    // Rough detection on the screen version, then exact on a full-resolution strip. {right, bottom} in full px.

    private File bandFile(String key) throws Exception {
        return new File(cacheDir, sha1(key + "|band-v1") + ".band");
    }

    private void saveBand(String key, int[] band) throws Exception {
        Files.write(bandFile(key).toPath(), (band[0] + " " + band[1]).getBytes(StandardCharsets.US_ASCII));
    }

    private int[] band(String id) throws Exception {
        File f = bandFile(key(id));
        if (!f.exists()) screen(id);                        // measured while making the screen preview
        try {
            String[] p = new String(Files.readAllBytes(f.toPath()), StandardCharsets.US_ASCII).trim().split(" ");
            return new int[]{Integer.parseInt(p[0]), Integer.parseInt(p[1])};
        } catch (Exception e) {
            return new int[]{0, 0};
        }
    }

    private static int[] measureBand(Bitmap small, byte[] data, int w, int h) {
        int[] res = {0, 0};
        if (w <= SCREEN_PX) return res;
        boolean right = whiteEdge(small, true, small.getWidth() / 20) > 0;
        boolean bottom = whiteEdge(small, false, small.getHeight() / 20) > 0;
        if (!right && !bottom) return res;
        try {
            BitmapRegionDecoder rd = BitmapRegionDecoder.newInstance(data, 0, data.length);
            try {
                int sw = Math.min(256, w), sh = Math.min(256, h);
                if (right) {
                    Bitmap s = rd.decodeRegion(new Rect(w - sw, 0, w, h), null);
                    res[0] = whiteEdge(s, true, w / 20);
                    s.recycle();
                }
                if (bottom) {
                    Bitmap s = rd.decodeRegion(new Rect(0, h - sh, w, h), null);
                    res[1] = whiteEdge(s, false, h / 20);
                    s.recycle();
                }
            } finally {
                rd.recycle();
            }
        } catch (Exception ignored) { }
        return res;
    }

    /** Number of nearly white columns (right edge) or rows (bottom edge), at most `limit`; 0 if `limit` reached. */
    private static int whiteEdge(Bitmap b, boolean x, int limit) {
        int w = b.getWidth(), h = b.getHeight(), size = x ? w : h, other = x ? h : w;
        int step = Math.max(1, other / 300), samples = (other + step - 1) / step, n = 0;
        int[] line = new int[other];
        for (int k = size - 1; k > Math.max(-1, size - 1 - limit); k--) {
            if (x) b.getPixels(line, 0, 1, k, 0, 1, h);
            else b.getPixels(line, 0, w, 0, k, w, 1);
            int white = 0;
            for (int t = 0; t < other; t += step) {
                int p = line[t];
                if (Math.min((p >> 16) & 0xFF, Math.min((p >> 8) & 0xFF, p & 0xFF)) >= WHITE) white++;
            }
            if (white < 0.95 * samples) break;
            n++;
        }
        return n < limit ? n : 0;                           // too wide: probably real white (sky…)
    }

    // ------------------------------------------------------------------ copy / rating
    public boolean fetch(String id, OutputStream out, Progress progress) throws IOException {
        Long size = sizes.get(id);
        long total = size == null ? 0 : size, done = 0;
        try (InputStream in = cr.openInputStream(uri(id))) {
            if (in == null) throw new IOException("cannot open " + id);
            byte[] buf = new byte[1024 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                done += n;
                if (!progress.update(done, total)) return false;
            }
        }
        if (total > 0 && done != total) throw new IOException("incomplete copy (" + done + "/" + total + " bytes)");
        return true;
    }

    public String writeRating(String id, int rating) throws Exception {
        if (!isDng(id)) throw new IOException("writing ratings: DNG files only for now");
        synchronized (writeLock) {
            try (FdIo io = new FdIo(cr, uri(id), "rw")) { return Dng.writeRating(io, rating); }
        }
    }

    /** {@link Dng.Io} over a file descriptor given by the storage access framework (positioned reads / writes). */
    static final class FdIo implements Dng.Io {
        private final ParcelFileDescriptor pfd;
        private final FileDescriptor fd;

        FdIo(ContentResolver cr, Uri u, String mode) throws IOException {
            pfd = cr.openFileDescriptor(u, mode);
            if (pfd == null) throw new IOException("cannot open " + u);
            fd = pfd.getFileDescriptor();
        }

        public void read(long pos, byte[] b) throws IOException {
            try {
                for (int off = 0; off < b.length; ) {
                    int n = Os.pread(fd, b, off, b.length - off, pos + off);
                    if (n <= 0) throw new EOFException("unexpected end of file");
                    off += n;
                }
            } catch (ErrnoException e) { throw new IOException(e); }
        }

        public void write(long pos, byte[] b) throws IOException {
            try {
                for (int off = 0; off < b.length; ) off += Os.pwrite(fd, b, off, b.length - off, pos + off);
            } catch (ErrnoException e) { throw new IOException(e); }
        }

        public long length() throws IOException {
            try { return Os.fstat(fd).st_size; } catch (ErrnoException e) { throw new IOException(e); }
        }

        public void sync() throws IOException {
            try { Os.fsync(fd); } catch (ErrnoException e) { throw new IOException(e); }
        }

        public void close() throws IOException { pfd.close(); }
    }

    static String sha1(String s) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-1").digest(s.getBytes(StandardCharsets.UTF_8))) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}
