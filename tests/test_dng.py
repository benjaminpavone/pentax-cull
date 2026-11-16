"""dng.py: reading (info, preview) and writing xmp:Rating without ever moving existing data."""

import struct

import dng
import pytest
from conftest import make_dng, xmp_packet

DESC = '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"{}/>'


# ------------------------------------------------------------------ reading


def test_info_reads_exif_like_the_camera_api(tmp_path):
    p = make_dng(tmp_path / "a.DNG")
    assert dng.info(str(p)) == {
        "orientation": 6,
        "cameraModel": "PENTAX K-1 Mark II ",
        "datetime": "2026-09-18T15:20:19",
        "av": "4.0",
        "tv": "1.1600",
        "sv": "200",
    }


@pytest.mark.parametrize(
    ("exposure", "tv"),
    [((1, 1600), "1.1600"), ((10, 16000), "1.1600"), ((1, 1), "1.1"), ((2, 1), "2.1"), ((3, 10), "3.10")],
)
def test_shutter_speed_format(tmp_path, exposure, tv):
    assert dng.info(str(make_dng(tmp_path / "a.DNG", exposure=exposure)))["tv"] == tv


@pytest.mark.parametrize("jpeg_tags", [False, True])
def test_preview_finds_the_embedded_jpeg(tmp_path, jpeg_tags):
    """Skips the raw SubIFD; works with strip tags (compression 7) and with JPEGInterchangeFormat tags."""
    p = make_dng(tmp_path / "a.DNG", jpeg_tags=jpeg_tags)
    off, length, width = dng.preview(str(p))
    data = p.read_bytes()[off : off + length]
    assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
    assert width == 7360


def test_not_a_tiff(tmp_path):
    p = tmp_path / "x.DNG"
    p.write_bytes(b"\xff\xd8\xff\xe0 this is a JPEG")
    with pytest.raises(ValueError, match="not a TIFF"):
        dng.info(str(p))


# ------------------------------------------------------------------ writing


def test_no_xmp_appends_a_new_block_and_keeps_every_byte(tmp_path):
    p = make_dng(tmp_path / "a.DNG")
    before = p.read_bytes()
    assert dng.read_rating(str(p)) is None

    assert dng.write_rating(str(p), 4) == "appended (new XMP block)"

    after = p.read_bytes()
    assert dng.read_rating(str(p)) == 4
    # only the IFD0 pointer (bytes 4-8) changed; everything else is still there, new data is appended
    assert after[:4] == before[:4] and after[8 : len(before)] == before[8:]
    assert len(after) > len(before)
    assert struct.unpack_from("<I", after, 4)[0] >= len(before)
    # the photo is still readable the same way
    info = dng.info(str(p))
    assert info.pop("fileRating") == 4
    assert info == dng.info(str(make_dng(tmp_path / "b.DNG")))
    assert dng.preview(str(p)) == dng.preview(str(make_dng(tmp_path / "b.DNG")))


def test_second_write_is_in_place(tmp_path):
    p = make_dng(tmp_path / "a.DNG")
    dng.write_rating(str(p), 4)
    size = p.stat().st_size
    for r in (2, 5, 0, -1, 3):
        assert dng.write_rating(str(p), r) == "in place"
        assert dng.read_rating(str(p)) == r
    assert p.stat().st_size == size


@pytest.mark.parametrize(
    "body",
    [
        DESC.format(' xmp:Rating="2"'),  # attribute
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:Rating>2</xmp:Rating>'
        "</rdf:Description>",  # element (Lightroom style)
    ],
)
def test_existing_rating_is_replaced_in_place(tmp_path, body):
    p = make_dng(tmp_path / "a.DNG", xmp=xmp_packet(body))
    before = p.read_bytes()
    assert dng.read_rating(str(p)) == 2
    assert dng.write_rating(str(p), 5) == "in place"
    after = p.read_bytes()
    assert dng.read_rating(str(p)) == 5
    assert len(after) == len(before)
    assert sum(a != b for a, b in zip(before, after, strict=True)) == 1  # a single byte changed


def test_rating_added_into_xmp_padding(tmp_path):
    """XMP without a rating but with padding (Lightroom leaves ~2 KB): rewritten at the same size."""
    p = make_dng(tmp_path / "a.DNG", xmp=xmp_packet(DESC.format(' xmp:Label="Red"'), padding=500))
    size = p.stat().st_size
    assert dng.write_rating(str(p), 3) == "in place"
    assert dng.read_rating(str(p)) == 3
    assert p.stat().st_size == size
    assert b'xmp:Label="Red"' in p.read_bytes()  # other metadata kept


def test_full_xmp_is_appended_and_the_tag_repointed(tmp_path):
    p = make_dng(tmp_path / "a.DNG", xmp=xmp_packet(DESC.format(' xmp:Label="Red"')))
    before = p.read_bytes()
    assert dng.write_rating(str(p), 3) == "appended (existing tag repointed)"
    assert dng.read_rating(str(p)) == 3
    after = p.read_bytes()
    assert len(after) > len(before)
    assert b'xmp:Label="Red"' in after[len(before) :]  # the new packet keeps the other metadata
    # next change fits in the new packet's padding
    assert dng.write_rating(str(p), 1) == "in place"


@pytest.mark.parametrize("bad", [6, -2, 99])
def test_rating_out_of_range_is_refused_and_file_untouched(tmp_path, bad):
    p = make_dng(tmp_path / "a.DNG")
    before = p.read_bytes()
    with pytest.raises(ValueError):
        dng.write_rating(str(p), bad)
    assert p.read_bytes() == before
