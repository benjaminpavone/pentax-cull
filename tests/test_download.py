"""download.py: copies to a folder. Bug 2: two different photos with the same file name."""

import dng
from conftest import make_dng
from download import DownloadJob
from sources.folder import FolderSource


def run(src, items, dest):
    job = DownloadJob(src, items, str(dest), write_rating=True).start()
    job.thread.join(30)
    return job.status()


def test_same_name_different_photos_are_both_copied(tmp_path):
    """Two cards (or the camera counter wrapping around) give two IMGP0001.DNG: both must be copied and each
    must get its own rating."""
    card = tmp_path / "card"
    (card / "100_1").mkdir(parents=True)
    (card / "101_1").mkdir()
    make_dng(card / "100_1/IMGP0001.DNG", date="2026:09:18 15:00:00")
    make_dng(card / "101_1/IMGP0001.DNG", date="2026:10:02 09:30:00")
    src, dest = FolderSource(str(card)), tmp_path / "dest"
    try:
        st = run(src, [{"id": "100_1/IMGP0001.DNG", "rating": 5}, {"id": "101_1/IMGP0001.DNG", "rating": 2}], dest)
        assert (st["downloaded"], st["skipped"], st["failed"]) == (2, 0, [])
        assert dng.info(str(dest / "IMGP0001.DNG"))["datetime"] == "2026-09-18T15:00:00"
        assert dng.info(str(dest / "IMGP0001-2.DNG"))["datetime"] == "2026-10-02T09:30:00"
        assert dng.read_rating(str(dest / "IMGP0001.DNG")) == 5
        assert dng.read_rating(str(dest / "IMGP0001-2.DNG")) == 2

        # copied again: both recognised (even the renamed one), nothing duplicated, ratings updated
        st = run(src, [{"id": "101_1/IMGP0001.DNG", "rating": 4}, {"id": "100_1/IMGP0001.DNG", "rating": 1}], dest)
        assert (st["downloaded"], st["skipped"]) == (0, 2)
        assert sorted(p.name for p in dest.iterdir()) == ["IMGP0001-2.DNG", "IMGP0001.DNG"]
        assert dng.read_rating(str(dest / "IMGP0001-2.DNG")) == 4
        assert dng.read_rating(str(dest / "IMGP0001.DNG")) == 1
    finally:
        src.close()


def test_unrelated_file_with_the_same_name_is_left_alone(tmp_path):
    card, dest = tmp_path / "card", tmp_path / "dest"
    card.mkdir()
    dest.mkdir()
    make_dng(card / "IMGP0001.DNG")
    (dest / "IMGP0001.DNG").write_bytes(b"not a DNG, someone else's file")
    src = FolderSource(str(card))
    try:
        st = run(src, [{"id": "IMGP0001.DNG", "rating": 3}], dest)
        assert st["downloaded"] == 1 and st["failed"] == []
        assert (dest / "IMGP0001.DNG").read_bytes() == b"not a DNG, someone else's file"
        assert dng.read_rating(str(dest / "IMGP0001-2.DNG")) == 3
    finally:
        src.close()
