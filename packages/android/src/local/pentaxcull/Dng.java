package local.pentaxcull;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Minimal TIFF/DNG reading / writing. Java port of packages/desktop/dng.py (same bytes written, tested):
 * - info()        -> date, aperture, shutter speed, ISO, orientation, model, XMP rating if any
 * - preview()     -> {offset, length, width} of the largest embedded JPEG preview
 * - writeRating() -> writes xmp:Rating, never moving existing data:
 *   1. the XMP packet already has a one-digit xmp:Rating -> that byte is replaced (in place);
 *   2. the XMP packet has enough padding -> rewritten at the same size (in place);
 *   3. otherwise the new packet (and, without XMP, a copy of IFD0 with tag 0x02BC) is appended at the END,
 *      then a single pointer is updated, last.
 * Works on any {@link Io}: a plain file here, a file opened through Android's storage access framework in
 * FolderSource. No Android dependency: also runs on a desktop JVM (`java Dng.java <file> <rating>`).
 */
public final class Dng {
    /** Random access to the file's bytes. */
    public interface Io extends AutoCloseable {
        void read(long pos, byte[] b) throws IOException;
        void write(long pos, byte[] b) throws IOException;
        long length() throws IOException;
        void sync() throws IOException;
        @Override void close() throws IOException;
    }

    /** {@link Io} over a plain file. */
    public static final class FileIo implements Io {
        private final RandomAccessFile f;
        public FileIo(File file, String mode) throws IOException { f = new RandomAccessFile(file, mode); }
        public void read(long pos, byte[] b) throws IOException { f.seek(pos); f.readFully(b); }
        public void write(long pos, byte[] b) throws IOException { f.seek(pos); f.write(b); }
        public long length() throws IOException { return f.length(); }
        public void sync() throws IOException { f.getFD().sync(); }
        public void close() throws IOException { f.close(); }
    }

    static final int T_ORIENTATION = 274, T_MODEL = 272, T_EXIF = 34665, T_SUBIFDS = 330, T_XMP = 700;
    static final int T_SUBFILE = 254, T_WIDTH = 256, T_COMPRESSION = 259, T_STRIP_OFF = 273, T_STRIP_CNT = 279;
    static final int T_JPEG_OFF = 513, T_JPEG_LEN = 514;
    static final int T_EXPOSURE = 33434, T_FNUMBER = 33437, T_ISO = 34855, T_DATE_ORIG = 36867;
    static final String XMP_NS = "http://ns.adobe.com/xap/1.0/";
    private static final int[] TYPE_SIZES = new int[17];
    static {
        int[][] s = {{1, 1}, {2, 1}, {3, 2}, {4, 4}, {5, 8}, {6, 1}, {7, 1}, {8, 2}, {9, 4}, {10, 8}, {11, 4}, {12, 8}, {13, 4}, {16, 8}};
        for (int[] p : s) TYPE_SIZES[p[0]] = p[1];
    }
    // bytes <-> String 1:1 (ISO-8859-1) so that regexes work on raw bytes
    private static final java.nio.charset.Charset L1 = StandardCharsets.ISO_8859_1;
    private static final Pattern ATTR = Pattern.compile("(xmp:Rating\\s*=\\s*[\"'])(-?\\d+)([\"'])");
    private static final Pattern ELEM = Pattern.compile("(<xmp:Rating>\\s*)(-?\\d+)(\\s*</xmp:Rating>)");
    private static final Pattern DESC = Pattern.compile("<rdf:Description\\b([^>]*?)(/?>)", Pattern.DOTALL);

    private Dng() { }

    private static final class Tiff {
        final Io io;
        final ByteOrder order;
        final long ifd0;

        Tiff(Io io) throws IOException {
            this.io = io;
            byte[] h = read(0, 8);
            if (h[0] == 'I' && h[1] == 'I') order = ByteOrder.LITTLE_ENDIAN;
            else if (h[0] == 'M' && h[1] == 'M') order = ByteOrder.BIG_ENDIAN;
            else throw new IOException("not a TIFF/DNG file");
            ByteBuffer b = ByteBuffer.wrap(h).order(order);
            if (b.getShort(2) != 42) throw new IOException("not a TIFF/DNG file (BigTIFF not supported)");
            ifd0 = b.getInt(4) & 0xFFFFFFFFL;
        }

        byte[] read(long pos, int size) throws IOException {
            byte[] b = new byte[size];
            io.read(pos, b);
            return b;
        }

        ByteBuffer buf(byte[] b) { return ByteBuffer.wrap(b).order(order); }
        int u16(long pos) throws IOException { return buf(read(pos, 2)).getShort() & 0xFFFF; }
        long u32(long pos) throws IOException { return buf(read(pos, 4)).getInt() & 0xFFFFFFFFL; }

        /** All entries of the IFD at `off`: tag -> {type, count, value-field position}. */
        Map<Integer, long[]> entries(long off) throws IOException {
            int n = u16(off);
            ByteBuffer raw = buf(read(off + 2, 12 * n));
            Map<Integer, long[]> out = new LinkedHashMap<>();
            for (int i = 0; i < n; i++) {
                out.put(raw.getShort(12 * i) & 0xFFFF,
                        new long[]{raw.getShort(12 * i + 2) & 0xFFFF, raw.getInt(12 * i + 4) & 0xFFFFFFFFL, off + 2 + 12L * i + 8});
            }
            return out;
        }

        long next(long off) throws IOException { return u32(off + 2 + 12L * u16(off)); }

        /** {data position, size} of an entry's value. */
        long[] dataPosOf(long[] e) throws IOException {
            int t = (int) e[0];
            long size = (t < TYPE_SIZES.length && TYPE_SIZES[t] > 0 ? TYPE_SIZES[t] : 1) * e[1];
            return new long[]{size <= 4 ? e[2] : u32(e[2]), size};
        }

        /** Integer values (BYTE, SHORT, LONG). */
        long[] ints(long[] e) throws IOException {
            long[] dp = dataPosOf(e);
            ByteBuffer b = buf(read(dp[0], (int) dp[1]));
            int n = (int) e[1];
            long[] v = new long[n];
            for (int i = 0; i < n; i++) {
                switch ((int) e[0]) {
                    case 1: case 7: v[i] = b.get(i) & 0xFF; break;
                    case 3: v[i] = b.getShort(2 * i) & 0xFFFF; break;
                    default: v[i] = b.getInt(4 * i) & 0xFFFFFFFFL; break;
                }
            }
            return v;
        }

        long first(Map<Integer, long[]> e, int tag, long def) throws IOException {
            long[] x = e.get(tag);
            if (x == null) return def;
            long[] v = ints(x);
            return v.length > 0 ? v[0] : def;
        }

        long[] rational(long[] e) throws IOException {
            ByteBuffer b = buf(read(dataPosOf(e)[0], 8));
            return new long[]{b.getInt(0) & 0xFFFFFFFFL, b.getInt(4) & 0xFFFFFFFFL};
        }

        String ascii(long[] e) throws IOException {
            long[] dp = dataPosOf(e);
            String s = new String(read(dp[0], (int) dp[1]), L1);
            int z = s.indexOf('\0');
            return (z >= 0 ? s.substring(0, z) : s).trim();
        }

        long append(byte[] data) throws IOException {
            long pos = io.length();
            if (pos % 2 == 1) { io.write(pos, new byte[1]); pos++; }    // TIFF offsets must be even
            io.write(pos, data);
            io.sync();
            return pos;
        }
    }

    // ---------------------------------------------------------------- reading

    /** Same keys and formats as dng.info() and the camera API ("tv": "1.800" = 1/800 s). */
    public static Map<String, Object> info(Io io) throws IOException {
        Tiff t = new Tiff(io);
        Map<Integer, long[]> e0 = t.entries(t.ifd0);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("orientation", t.first(e0, T_ORIENTATION, 1));
        out.put("cameraModel", e0.containsKey(T_MODEL) ? t.ascii(e0.get(T_MODEL)) : "");
        if (e0.containsKey(T_EXIF)) {
            Map<Integer, long[]> ex = t.entries(t.first(e0, T_EXIF, 0));
            if (ex.containsKey(T_DATE_ORIG)) {
                String dt = t.ascii(ex.get(T_DATE_ORIG));
                if (dt.length() >= 19) out.put("datetime", dt.substring(0, 10).replace(':', '-') + "T" + dt.substring(11, 19));
            }
            if (ex.containsKey(T_FNUMBER)) {
                long[] r = t.rational(ex.get(T_FNUMBER));
                if (r[1] != 0) out.put("av", String.format(java.util.Locale.ROOT, "%.1f", (double) r[0] / r[1]));
            }
            if (ex.containsKey(T_EXPOSURE)) {
                long[] r = t.rational(ex.get(T_EXPOSURE));
                long n = r[0], d = r[1];
                if (n != 0 && d != 0) {
                    if (n != 1 && d % n == 0) { d /= n; n = 1; }
                    out.put("tv", n + "." + d);
                }
            }
            if (ex.containsKey(T_ISO)) out.put("sv", String.valueOf(t.first(ex, T_ISO, 0)));
        }
        if (e0.containsKey(T_XMP)) out.put("fileRating", ratingIn(t, e0.get(T_XMP)));
        return out;
    }

    /** {offset, length, width} of the largest embedded JPEG preview, or null. */
    public static long[] preview(Io io) throws IOException {
        Tiff t = new Tiff(io);
        long[] best = null;
        ArrayDeque<Long> todo = new ArrayDeque<>();
        Set<Long> seen = new HashSet<>();
        todo.push(t.ifd0);
        long len = io.length();
        while (!todo.isEmpty()) {
            long off = todo.pop();
            if (off == 0 || off >= len || !seen.add(off)) continue;
            Map<Integer, long[]> e = t.entries(off);
            todo.push(t.next(off));
            if (e.containsKey(T_SUBIFDS)) for (long s : t.ints(e.get(T_SUBIFDS))) todo.push(s);
            long comp = t.first(e, T_COMPRESSION, 0), w = t.first(e, T_WIDTH, 0);
            boolean reduced = (t.first(e, T_SUBFILE, 0) & 1) == 1;     // bit 0 = reduced image (not the RAW)
            long[] loc = null;
            if (e.containsKey(T_JPEG_OFF) && e.containsKey(T_JPEG_LEN)) {
                loc = new long[]{t.first(e, T_JPEG_OFF, 0), t.first(e, T_JPEG_LEN, 0)};
            } else if ((comp == 6 || comp == 7) && reduced && e.containsKey(T_STRIP_OFF)) {
                long[] offs = t.ints(e.get(T_STRIP_OFF)), cnts = t.ints(e.get(T_STRIP_CNT));
                if (offs.length == 1) loc = new long[]{offs[0], cnts[0]};
            }
            if (loc != null && loc[0] + 2 <= len) {
                byte[] soi = t.read(loc[0], 2);
                if ((soi[0] & 0xFF) == 0xFF && (soi[1] & 0xFF) == 0xD8 && (best == null || w > best[2]))
                    best = new long[]{loc[0], loc[1], w};
            }
        }
        return best;
    }

    private static Integer ratingIn(Tiff t, long[] xmpEntry) throws IOException {
        long[] dp = t.dataPosOf(xmpEntry);
        String x = new String(t.read(dp[0], (int) dp[1]), L1);
        Matcher m = ATTR.matcher(x);
        if (m.find()) return Integer.parseInt(m.group(2));
        m = ELEM.matcher(x);
        return m.find() ? Integer.parseInt(m.group(2)) : null;
    }

    public static Integer readRating(Io io) throws IOException {
        Tiff t = new Tiff(io);
        Map<Integer, long[]> e0 = t.entries(t.ifd0);
        return e0.containsKey(T_XMP) ? ratingIn(t, e0.get(T_XMP)) : null;
    }

    public static Integer readRating(File file) throws IOException {
        try (Io io = new FileIo(file, "r")) { return readRating(io); }
    }

    // ---------------------------------------------------------------- writing

    static String newPacket(int rating) {
        StringBuilder pad = new StringBuilder();
        for (int i = 0; i < 20; i++) pad.append(" ".repeat(99)).append('\n');   // ~2 KB for later edits
        return "<?xpacket begin=\"\u00ef\u00bb\u00bf\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>\n"
                + "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">\n"
                + " <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\n"
                + "  <rdf:Description rdf:about=\"\"\n"
                + "    xmlns:xmp=\"" + XMP_NS + "\"\n"
                + "   xmp:Rating=\"" + rating + "\"/>\n"
                + " </rdf:RDF>\n"
                + "</x:xmpmeta>\n"
                + pad + "<?xpacket end=\"w\"?>";
    }

    static String setInPacket(String pkt, int rating) throws IOException {
        String r = Integer.toString(rating);
        Matcher m = ATTR.matcher(pkt);
        if (m.find()) return pkt.substring(0, m.start(2)) + r + pkt.substring(m.end(2));
        m = ELEM.matcher(pkt);
        if (m.find()) return pkt.substring(0, m.start(2)) + r + pkt.substring(m.end(2));
        m = DESC.matcher(pkt);
        if (!m.find()) throw new IOException("XMP without rdf:Description");
        String attrs = m.group(1);
        String add = attrs.contains("xmlns:xmp=") ? "" : "\n    xmlns:xmp=\"" + XMP_NS + "\"";
        add += "\n   xmp:Rating=\"" + r + "\"";
        return pkt.substring(0, m.end(1)) + add + pkt.substring(m.end(1));
    }

    /** The packet resized to exactly `size` bytes using its padding, or null if impossible. */
    static String fit(String pkt, long size) {
        int end = pkt.lastIndexOf("<?xpacket end");
        if (end < 0) return null;
        String core = pkt.substring(0, end).replaceAll("[ \\t\\r\\n]+$", ""), tail = pkt.substring(end);
        long pad = size - core.length() - tail.length();
        if (pad < 1) return null;
        return core + "\n" + " ".repeat((int) pad - 1) + tail;
    }

    public static String writeRating(File file, int rating) throws IOException {
        try (Io io = new FileIo(file, "rw")) { return writeRating(io, rating); }
    }

    /** Writes xmp:Rating (-1..5). Returns how it was written. */
    public static String writeRating(Io io, int rating) throws IOException {
        if (rating < -1 || rating > 5) throw new IllegalArgumentException("rating must be between -1 and 5");
        Tiff t = new Tiff(io);
        Map<Integer, long[]> e0 = t.entries(t.ifd0);
        long[] e = e0.get(T_XMP);
        if (e != null) {
            long[] dp = t.dataPosOf(e);
            String old = new String(t.read(dp[0], (int) dp[1]), L1);
            String neu = setInPacket(old, rating);
            if (neu.length() == old.length()) return patch(t, dp[0], old, neu, "in place");             // case 1
            String fitted = fit(neu, dp[1]);
            if (fitted != null) return patch(t, dp[0], old, fitted, "in place");                         // case 2
            String grown = fit(neu, neu.length() + 2048);                                                 // case 3a
            if (grown != null) neu = grown;
            byte[] pkt = neu.getBytes(L1);
            long xmpPos = t.append(pkt);
            io.write(e[2] - 4, ByteBuffer.allocate(8).order(t.order).putInt(pkt.length).putInt((int) xmpPos).array());
            io.sync();
            return "appended (existing tag repointed)";
        }
        // case 3b: no XMP -> packet + copy of IFD0 with tag 0x02BC, appended
        byte[] pkt = newPacket(rating).getBytes(L1);
        long xmpPos = t.append(pkt);
        int n = t.u16(t.ifd0);
        byte[] raw = t.read(t.ifd0 + 2, 12 * n);
        long next = t.u32(t.ifd0 + 2 + 12L * n);
        List<byte[]> recs = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            byte[] r = new byte[12];
            System.arraycopy(raw, 12 * i, r, 0, 12);
            recs.add(r);
        }
        recs.add(ByteBuffer.allocate(12).order(t.order).putShort((short) T_XMP).putShort((short) 1)
                .putInt(pkt.length).putInt((int) xmpPos).array());
        recs.sort(Comparator.comparingInt(r -> ByteBuffer.wrap(r).order(t.order).getShort(0) & 0xFFFF));
        ByteBuffer ifd = ByteBuffer.allocate(2 + 12 * recs.size() + 4).order(t.order);
        ifd.putShort((short) recs.size());
        for (byte[] r : recs) ifd.put(r);
        ifd.putInt((int) next);
        long ifdPos = t.append(ifd.array());
        io.write(4, ByteBuffer.allocate(4).order(t.order).putInt((int) ifdPos).array());   // last of all: the IFD0 pointer
        io.sync();
        return "appended (new XMP block)";
    }

    /** Writes only the byte range that changes. */
    private static String patch(Tiff t, long pos, String old, String neu, String how) throws IOException {
        int i = 0, j = old.length();
        while (i < j && old.charAt(i) == neu.charAt(i)) i++;
        while (j > i && old.charAt(j - 1) == neu.charAt(j - 1)) j--;
        if (j > i) {
            t.io.write(pos + i, neu.substring(i, j).getBytes(L1));
            t.io.sync();
        }
        return how;
    }

    /** Desktop test: `java Dng.java <file> <rating>` writes; `java Dng.java <file>` prints info + preview. */
    public static void main(String[] a) throws IOException {
        File f = new File(a[0]);
        if (a.length > 1) {
            System.out.println(writeRating(f, Integer.parseInt(a[1])) + " -> read back: " + readRating(f));
        } else {
            try (Io io = new FileIo(f, "r")) {
                long[] p = preview(io);
                System.out.println(info(io) + " preview=" + (p == null ? null : p[0] + "," + p[1] + "," + p[2]));
            }
        }
    }
}
