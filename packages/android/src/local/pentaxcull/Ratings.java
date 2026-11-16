package local.pentaxcull;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/** ratings.json in the app's private storage. Same format as the Mac (docs/ratings-format.md). */
class Ratings {
    private static final DateTimeFormatter ISO = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss");
    private static final DateTimeFormatter STRICT = DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss")
            .withResolverStyle(java.time.format.ResolverStyle.STRICT);

    /** docs/ratings-format.md: key = "<group>/<path>", datetime = YYYY-MM-DDTHH:MM:SS (a real date). */
    static boolean validKey(Object k) {
        if (!(k instanceof String)) return false;
        String s = (String) k;
        if (s.isEmpty() || s.length() > 512) return false;
        for (int i = 0; i < s.length(); i++) if (Character.isISOControl(s.charAt(i))) return false;
        return true;
    }

    static boolean validDate(Object d) {
        if (!(d instanceof String) || ((String) d).length() != 19) return false;
        try {
            LocalDateTime.parse((String) d, STRICT);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
    private final File file;

    Ratings(File dir) {
        file = new File(dir, "ratings.json");
    }

    /** The ratings. A missing file is an empty one; an unreadable one throws: saving over it would erase them. */
    synchronized JSONObject read() throws IOException {
        byte[] raw;
        try {
            raw = Files.readAllBytes(file.toPath());
        } catch (java.nio.file.NoSuchFileException e) {
            return new JSONObject();
        }
        try {
            return new JSONObject(new String(raw, StandardCharsets.UTF_8));
        } catch (org.json.JSONException e) {
            throw new IOException("ratings.json is unreadable (" + e.getMessage() + "): nothing is saved meanwhile");
        }
    }

    /**
     * Saves a rating (1-5) or clears it (0). A cleared rating is kept as 0 with its date, so that an older copy
     * still holding the rating cannot bring it back when files are merged (docs/ratings-format.md).
     * The same photo stored under another key (same file name and shooting date) is replaced, as on the Mac.
     * Returns the number of rated photos.
     */
    synchronized int save(String key, int rating, Object datetime, String source) throws Exception {
        JSONObject data = read();
        if (datetime instanceof String) {
            String name = key.substring(key.lastIndexOf('/') + 1).toLowerCase();
            for (java.util.Iterator<String> it = data.keys(); it.hasNext(); ) {
                String k = it.next();
                JSONObject v = data.optJSONObject(k);
                if (!k.equals(key) && v != null && datetime.equals(v.opt("datetime"))
                        && k.substring(k.lastIndexOf('/') + 1).toLowerCase().equals(name)) it.remove();
            }
        }
        data.put(key, new JSONObject().put("rating", rating).put("datetime", datetime == null ? JSONObject.NULL : datetime)
                .put("source", source).put("updated", LocalDateTime.now().format(ISO)));
        File tmp = new File(file.getPath() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            out.write(data.toString(1).getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        }
        if (!tmp.renameTo(file)) throw new IOException("could not save ratings.json");
        return rated(data);
    }

    /** Number of photos with a rating (cleared ones, kept as 0, are not counted). */
    static int rated(JSONObject data) {
        int n = 0;
        for (java.util.Iterator<String> it = data.keys(); it.hasNext(); ) {
            JSONObject v = data.optJSONObject(it.next());
            if (v != null && v.optInt("rating") > 0) n++;
        }
        return n;
    }

    File file() { return file; }
}
