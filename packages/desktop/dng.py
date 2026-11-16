"""Minimal TIFF/DNG reading/writing, in pure Python (no dependencies).

- info(path)            -> date, aperture, shutter speed, ISO, orientation, XMP rating if any
- preview(path)         -> (offset, length, width) of the largest embedded JPEG preview
- read_rating(path)     -> xmp:Rating or None
- write_rating(path, n) -> writes xmp:Rating into the file, never moving existing data:
    1. the XMP packet already has a one-digit xmp:Rating      -> that byte is replaced (in place)
    2. the XMP packet exists and has enough padding         -> rewritten at the same size (in place)
    3. otherwise -> the new XMP packet (and, if needed, a copy of IFD0 with tag 0x02BC) is appended
       at the END of the file, then a single pointer is updated, last.
Java port (same bytes, tested): packages/android/src/local/pentaxcull/Dng.java
"""
import os
import re
import struct

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8}

T_ORIENTATION, T_EXIF, T_SUBIFDS, T_XMP = 274, 34665, 330, 700
T_SUBFILE, T_WIDTH, T_COMPRESSION = 254, 256, 259
T_STRIP_OFF, T_STRIP_CNT, T_JPEG_OFF, T_JPEG_LEN = 273, 279, 513, 514
T_EXPOSURE, T_FNUMBER, T_ISO, T_DATE_ORIG = 33434, 33437, 34855, 36867

XMP_NS = b"http://ns.adobe.com/xap/1.0/"


class Tiff:
    def __init__(self, path, mode="rb"):
        self.path = path
        self.f = open(path, mode)
        bo = self.f.read(4)
        if bo[:2] == b"II":
            self.e = "<"
        elif bo[:2] == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF/DNG file")
        if struct.unpack(self.e + "H", bo[2:4])[0] != 42:
            raise ValueError("not a TIFF/DNG file (BigTIFF not supported)")
        self.ifd0 = self.u32(4)

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def read(self, pos, size):
        self.f.seek(pos)
        return self.f.read(size)

    def u16(self, pos):
        return struct.unpack(self.e + "H", self.read(pos, 2))[0]

    def u32(self, pos):
        return struct.unpack(self.e + "I", self.read(pos, 4))[0]

    def entries(self, off):
        """{tag: (type, count, position of the value/offset field)} + offset of the next IFD."""
        n = self.u16(off)
        raw = self.read(off + 2, 12 * n + 4)
        out = {}
        for i in range(n):
            tag, typ, cnt = struct.unpack_from(self.e + "HHI", raw, 12 * i)
            out[tag] = (typ, cnt, off + 2 + 12 * i + 8)
        nxt = struct.unpack_from(self.e + "I", raw, 12 * n)[0]
        return out, nxt

    def data_pos(self, entry):
        typ, cnt, vpos = entry
        size = TYPE_SIZES.get(typ, 1) * cnt
        return (vpos if size <= 4 else self.u32(vpos)), size

    def value(self, entry):
        typ, cnt, _ = entry
        pos, size = self.data_pos(entry)
        b = self.read(pos, size)
        if typ == 2:
            return b.split(b"\0")[0].decode("latin1")
        if typ in (1, 6, 7):
            return b
        if typ in (5, 10):
            v = struct.unpack(self.e + ("%dI" if typ == 5 else "%di") % (2 * cnt), b)
            return [(v[2 * i], v[2 * i + 1]) for i in range(cnt)]
        fmt = {3: "H", 4: "I", 8: "h", 9: "i", 13: "I", 16: "Q"}.get(typ)
        return list(struct.unpack(self.e + f"{cnt}{fmt}", b)) if fmt else b

    def first(self, entries, tag, default=None):
        if tag not in entries:
            return default
        v = self.value(entries[tag])
        return v[0] if isinstance(v, list) and v else v


# ---------------------------------------------------------------- lecture

def _fmt_tv(r):
    n, d = r
    if not n or not d:
        return ""
    if n != 1 and d % n == 0:
        n, d = 1, d // n
    return f"{n}.{d}"   # same format as the camera API: "1.800" = 1/800 s


def _xmp_rating(xmp):
    m = (re.search(rb'xmp:Rating\s*=\s*["\'](-?\d+)["\']', xmp)
         or re.search(rb"<xmp:Rating>\s*(-?\d+)\s*</xmp:Rating>", xmp))
    return int(m.group(1)) if m else None


def info(path):
    with Tiff(path) as t:
        e0, _ = t.entries(t.ifd0)
        out = {"orientation": t.first(e0, T_ORIENTATION, 1), "cameraModel": t.first(e0, 272, "")}
        if T_EXIF in e0:
            ex, _ = t.entries(t.first(e0, T_EXIF))
            dt = t.first(ex, T_DATE_ORIG)
            if dt:
                out["datetime"] = dt.strip().replace(":", "-", 2).replace(" ", "T")
            fn = t.first(ex, T_FNUMBER)
            if fn and fn[1]:
                out["av"] = f"{fn[0] / fn[1]:.1f}"
            tv = t.first(ex, T_EXPOSURE)
            if tv:
                out["tv"] = _fmt_tv(tv)
            iso = t.first(ex, T_ISO)
            if iso:
                out["sv"] = str(iso)
        if T_XMP in e0:
            out["fileRating"] = _xmp_rating(t.value(e0[T_XMP]))
        return out


def preview(path):
    """(offset, length, width) of the largest embedded JPEG preview, or None."""
    best = None
    with Tiff(path) as t:
        todo, seen = [t.ifd0], set()
        while todo:
            off = todo.pop()
            if not off or off in seen:
                continue
            seen.add(off)
            e, nxt = t.entries(off)
            todo.append(nxt)
            if T_SUBIFDS in e:
                todo.extend(t.value(e[T_SUBIFDS]))
            comp = t.first(e, T_COMPRESSION)
            is_preview = t.first(e, T_SUBFILE, 0) & 1   # bit 0 = reduced-resolution image (not the RAW)
            w = t.first(e, T_WIDTH, 0)
            loc = None
            if T_JPEG_OFF in e and T_JPEG_LEN in e:
                loc = (t.first(e, T_JPEG_OFF), t.first(e, T_JPEG_LEN))
            elif comp in (6, 7) and is_preview and T_STRIP_OFF in e:
                offs, cnts = t.value(e[T_STRIP_OFF]), t.value(e[T_STRIP_CNT])
                if len(offs) == 1:
                    loc = (offs[0], cnts[0])
            if loc and t.read(loc[0], 2) == b"\xff\xd8" and (not best or w > best[2]):
                best = (loc[0], loc[1], w)
    return best


def read_rating(path):
    with Tiff(path) as t:
        e0, _ = t.entries(t.ifd0)
        return _xmp_rating(t.value(e0[T_XMP])) if T_XMP in e0 else None


# ---------------------------------------------------------------- writing

def _new_packet(rating):
    body = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        f'   xmp:Rating="{rating}"/>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
    ).encode()
    padding = (b" " * 99 + b"\n") * 20   # ~2 KB of padding for later edits
    return (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' + body + padding +
            b'<?xpacket end="w"?>')


def _set_in_packet(pkt, rating):
    """The XMP packet with xmp:Rating = rating (any size)."""
    r = str(rating).encode()
    pat_attr = re.compile(rb'(xmp:Rating\s*=\s*["\'])(-?\d+)(["\'])')
    if pat_attr.search(pkt):
        return pat_attr.sub(lambda m: m.group(1) + r + m.group(3), pkt, count=1)
    pat_el = re.compile(rb"(<xmp:Rating>\s*)(-?\d+)(\s*</xmp:Rating>)")
    if pat_el.search(pkt):
        return pat_el.sub(lambda m: m.group(1) + r + m.group(3), pkt, count=1)
    m = re.search(rb"<rdf:Description\b([^>]*?)(/?>)", pkt, re.S)
    if not m:
        raise ValueError("XMP without rdf:Description")
    attrs = m.group(1)
    add = b"" if b"xmlns:xmp=" in attrs else b'\n    xmlns:xmp="' + XMP_NS + b'"'
    add += b'\n   xmp:Rating="' + r + b'"'
    return pkt[:m.end(1)] + add + pkt[m.end(1):]


def _fit(pkt, size):
    """The packet resized to exactly `size` bytes using its padding, or None if impossible."""
    end = pkt.rfind(b"<?xpacket end")
    if end < 0:
        return None
    head, tail = pkt[:end], pkt[end:]
    core = head.rstrip(b" \t\r\n")
    pad = size - len(core) - len(tail)
    if pad < 1:
        return None
    return core + b"\n" + b" " * (pad - 1) + tail


def write_rating(path, rating):
    rating = int(rating)
    if not -1 <= rating <= 5:
        raise ValueError("rating must be between -1 and 5")
    with Tiff(path, "r+b") as t:
        e0, nxt = t.entries(t.ifd0)

        if T_XMP in e0:
            pos, size = t.data_pos(e0[T_XMP])
            old = t.read(pos, size)
            new = _set_in_packet(old, rating)
            if len(new) == len(old):
                return _patch(t, pos, old, new)                       # case 1
            fitted = _fit(new, size)
            if fitted:
                return _patch(t, pos, old, fitted)                    # case 2
            # case 3a: new packet at the end of the file, the existing tag is repointed
            new = _fit(new, len(new) + 2048) or new
            xmp_pos = _append(t, new)
            typ, _, vpos = e0[T_XMP]
            cnt_pos = vpos - 4
            t.f.seek(cnt_pos)
            t.f.write(struct.pack(t.e + "II", len(new), xmp_pos))
            t.f.flush()
            os.fsync(t.f.fileno())
            return "appended (existing tag repointed)"

        # case 3b: no XMP -> packet + copy of IFD0 with tag 0x02BC, at the end of the file
        pkt = _new_packet(rating)
        xmp_pos = _append(t, pkt)
        n = t.u16(t.ifd0)
        raw = t.read(t.ifd0 + 2, 12 * n)
        recs = [raw[12 * i:12 * i + 12] for i in range(n)]
        recs.append(struct.pack(t.e + "HHII", T_XMP, 1, len(pkt), xmp_pos))
        recs.sort(key=lambda r: struct.unpack_from(t.e + "H", r)[0])
        ifd = struct.pack(t.e + "H", len(recs)) + b"".join(recs) + struct.pack(t.e + "I", nxt)
        ifd_pos = _append(t, ifd)
        t.f.seek(4)                                                   # last of all: the pointer
        t.f.write(struct.pack(t.e + "I", ifd_pos))
        t.f.flush()
        os.fsync(t.f.fileno())
        return "appended (new XMP block)"


def _patch(t, pos, old, new):
    # writes only the byte range that changes
    i = 0
    while i < len(old) and old[i] == new[i]:
        i += 1
    j = len(old)
    while j > i and old[j - 1] == new[j - 1]:
        j -= 1
    if j > i:
        t.f.seek(pos + i)
        t.f.write(new[i:j])
        t.f.flush()
        os.fsync(t.f.fileno())
    return "in place"


def _append(t, data):
    t.f.seek(0, 2)
    pos = t.f.tell()
    if pos % 2:                      # TIFF offsets must be even
        t.f.write(b"\0")
        pos += 1
    t.f.write(data)
    t.f.flush()
    os.fsync(t.f.fileno())
    return pos
