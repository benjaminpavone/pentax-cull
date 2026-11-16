"""Shared test helpers: synthetic DNG files built from scratch (no real photo needed).

`make_dng()` writes a small little-endian TIFF/DNG laid out like a Pentax K-1 II file: IFD0 (thumbnail, model,
orientation, EXIF pointer, SubIFDs), a raw SubIFD (skipped by the preview search) and a JPEG preview SubIFD
(compression 7, reduced-image bit set), the EXIF IFD (date, aperture, shutter speed, ISO) and optionally an XMP
packet (tag 700).
"""

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

# Never touch the user's real preview cache (~/Library/Caches/pentax-cull).
os.environ.setdefault("PENTAX_CULL_CACHE", str(Path(__file__).parent / ".cache-tests"))

BYTE, ASCII, SHORT, LONG, RATIONAL = 1, 2, 3, 4, 5
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"


def _short(v):
    return SHORT, 1, struct.pack("<H", v)


def _long(*v):
    return LONG, len(v), struct.pack(f"<{len(v)}I", *v)


def _rational(n, d):
    return RATIONAL, 1, struct.pack("<II", n, d)


def _ascii(s):
    b = s.encode("latin-1") + b"\0"
    return ASCII, len(b), b


def _ifd(entries, offset, next_ifd=0):
    """IFD bytes placed at `offset`; values longer than 4 bytes follow the IFD."""
    n = len(entries)
    data_pos = offset + 2 + 12 * n + 4
    head, data = struct.pack("<H", n), b""
    for tag in sorted(entries):
        typ, count, raw = entries[tag]
        if isinstance(raw, int):                          # data already in the file at this offset
            head += struct.pack("<HHII", tag, typ, count, raw)
        elif len(raw) <= 4:
            head += struct.pack("<HHI", tag, typ, count) + raw.ljust(4, b"\0")
        else:
            if (data_pos + len(data)) % 2:
                data += b"\0"
            head += struct.pack("<HHII", tag, typ, count, data_pos + len(data))
            data += raw
    return head + struct.pack("<I", next_ifd) + data


def make_dng(path, *, jpeg=FAKE_JPEG, jpeg_width=7360, jpeg_height=4912, xmp=None, orientation=6,
             exposure=(1, 1600), fnumber=(40, 10), iso=200, date="2026:09:18 15:20:19",
             model="PENTAX K-1 Mark II ", jpeg_tags=False, raw_size=4096):
    """Writes a synthetic DNG and returns its path. `jpeg_tags`: preview located with tags 513/514 (old style)."""
    out = bytearray(b"II*\0\0\0\0\0")                    # IFD0 offset patched at the end
    jpeg_pos = len(out)
    out += jpeg
    if len(out) % 2:
        out += b"\0"
    raw_pos = len(out)
    out += bytes(range(256)) * (raw_size // 256)          # stands for the raw image data
    xmp_pos = len(out)
    if xmp is not None:
        out += xmp
        if len(out) % 2:
            out += b"\0"

    exif_pos = len(out)
    exif = {33434: _rational(*exposure), 33437: _rational(*fnumber), 34855: _short(iso), 36867: _ascii(date)}
    out += _ifd(exif, exif_pos)

    raw_ifd_pos = len(out)
    out += _ifd({254: _long(0), 256: _long(7392), 259: _short(1), 273: _long(raw_pos), 279: _long(raw_size)},
                raw_ifd_pos)

    prev_ifd_pos = len(out)
    prev = {254: _long(1), 256: _long(jpeg_width), 257: _long(jpeg_height)}
    if jpeg_tags:
        prev.update({513: _long(jpeg_pos), 514: _long(len(jpeg))})
    else:
        prev.update({259: _short(7), 273: _long(jpeg_pos), 279: _long(len(jpeg))})
    out += _ifd(prev, prev_ifd_pos)

    ifd0_pos = len(out)
    ifd0 = {254: _long(1), 256: _long(160), 257: _long(120), 259: _short(1), 272: _ascii(model),
            274: _short(orientation), 330: _long(raw_ifd_pos, prev_ifd_pos), 34665: _long(exif_pos)}
    if xmp is not None:
        ifd0[700] = (BYTE, len(xmp), xmp_pos)
    out += _ifd(ifd0, ifd0_pos)
    struct.pack_into("<I", out, 4, ifd0_pos)
    Path(path).write_bytes(bytes(out))
    return Path(path)


def xmp_packet(body, padding=0):
    """An XMP packet around an rdf:Description `body`, with `padding` spaces before the end marker."""
    return (
        '<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        f"  {body}\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n" + " " * padding + '<?xpacket end="w"?>'
    ).encode("latin-1")


def _bmp(path, w, h, pixel):
    """24-bit BMP; pixel(x, y) -> (r, g, b)."""
    row = (w * 3 + 3) & ~3
    data = bytearray()
    for y in range(h - 1, -1, -1):                       # bottom-up
        line = bytearray()
        for x in range(w):
            r, g, b = pixel(x, y)
            line += bytes((b, g, r))
        data += line.ljust(row, b"\0")
    header = struct.pack("<2sIHHI", b"BM", 54 + len(data), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(data), 2835, 2835, 0, 0)
    Path(path).write_bytes(header + info + data)


@pytest.fixture(scope="session")
def real_jpeg(tmp_path_factory):
    """(JPEG bytes, width, height, white band width): a 2048 px JPEG with a 32 px white band on the right, like the
    K-1 II previews. Made with `sips` (macOS only: the screen previews of the Mac server use it too)."""
    if sys.platform != "darwin" or not shutil.which("sips"):
        pytest.skip("needs macOS sips")
    d = tmp_path_factory.mktemp("jpeg")
    w, h, band = 2048, 1360, 32
    _bmp(d / "p.bmp", w, h, lambda x, y: (255, 255, 255) if x >= w - band else (90 + (x * 7 + y * 3) % 60, 80, 70))
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "90", str(d / "p.bmp"),
                    "--out", str(d / "p.jpg")], check=True, capture_output=True)
    return (d / "p.jpg").read_bytes(), w, h, band
