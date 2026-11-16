"""inbox.py: merging the ratings files exported by the phone, and refusing anything that is not one."""

import json
import os
import threading
from datetime import datetime, timedelta

import pytest
from inbox import Inbox, clean_entry, find_same, merge

DT = "2026-09-18T15:00:00"


def entry(rating=3, dt=DT, updated="2026-11-15T12:00:00", **kw):
    return {"rating": rating, "datetime": dt, "updated": updated, **kw}


# ------------------------------------------------------------------ one entry


def test_clean_entry_keeps_documented_fields_only():
    e = entry(source="camera", payload="x" * 10000, nested={"a": [1]})
    assert clean_entry("100_1/A.DNG", e) == {"rating": 3, "datetime": DT, "source": "camera",
                                             "updated": "2026-11-15T12:00:00"}


@pytest.mark.parametrize("rating", [True, False, 6, -1, 4.0, "5", None, [5]])
def test_invalid_ratings_are_refused(rating):
    assert clean_entry("g/A.DNG", entry(rating=rating)) is None


def test_cleared_rating_is_a_valid_entry():
    assert clean_entry("g/A.DNG", entry(rating=0))["rating"] == 0


@pytest.mark.parametrize("key", ["", "x" * 513, "g/\x07.DNG", "g/A\n.DNG", 5, None])
def test_invalid_keys_are_refused(key):
    assert clean_entry(key, entry()) is None


@pytest.mark.parametrize("dt", ["2026-02-30T10:00:00", "2026-09-18 15:00:00", "18/09/2026", ["2026"], 20260918])
def test_invalid_datetime_is_dropped_not_the_rating(dt):
    assert clean_entry("g/A.DNG", entry(dt=dt)) == {"rating": 3, "datetime": None, "updated": "2026-11-15T12:00:00"}


def test_unknown_source_is_dropped():
    assert "source" not in clean_entry("g/A.DNG", entry(source="evil"))


@pytest.mark.parametrize("updated", ["9999-12-31T23:59:59", "2026-13-45T99:99:99", "yesterday", 5])
def test_bad_or_future_updated_rejects_the_entry(updated):
    """Otherwise a "9999" date would win every later merge: the rating could never change again."""
    assert clean_entry("g/A.DNG", entry(updated=updated)) is None


def test_clock_skew_of_a_few_hours_is_accepted():
    soon = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
    assert clean_entry("g/A.DNG", entry(updated=soon))["updated"] == soon


# ------------------------------------------------------------------ merging


def test_same_photo_under_another_name():
    """Camera / phone: "100_1511/X.DNG"; card in a reader: "DCIM/100_1511/X.DNG"."""
    data = {"DCIM/100_1/A.DNG": entry(rating=2, updated="2026-11-01T10:00:00")}
    assert find_same(data, "100_1/a.dng", {"datetime": DT}) == "DCIM/100_1/A.DNG"
    assert find_same(data, "100_1/A.DNG", {"datetime": "2026-09-18T15:00:01"}) is None  # same name, other photo
    assert merge(data, {"100_1/A.DNG": entry(rating=5)}) == (0, 1)
    assert list(data) == ["DCIM/100_1/A.DNG"]  # one entry per photo, under its existing name
    assert data["DCIM/100_1/A.DNG"]["rating"] == 5
    assert data["DCIM/100_1/A.DNG"]["updated"] == "2026-11-15T12:00:00"


def test_newest_rating_wins_and_nothing_is_removed():
    data = {"g/A.DNG": entry(rating=4, updated="2026-11-15T12:00:00"), "g/B.DNG": entry(rating=1, dt=None)}
    added, updated = merge(data, {
        "g/A.DNG": entry(rating=1, updated="2026-11-10T12:00:00"),  # older: ignored
        "g/C.DNG": entry(rating=5, dt="2026-09-18T16:00:00"),        # new photo
    })
    assert (added, updated) == (1, 0)
    assert data["g/A.DNG"]["rating"] == 4 and data["g/B.DNG"]["rating"] == 1 and data["g/C.DNG"]["rating"] == 5


def test_clearing_wins_when_newer_and_an_old_export_does_not_bring_the_rating_back():
    """Bug 5: rated 3 on the phone, cleared later on the Mac; the next phone export still holds the 3."""
    data = {"g/A.DNG": entry(rating=0, updated="2026-11-15T18:00:00")}           # cleared on the Mac
    assert merge(data, {"100_1/A.DNG": entry(rating=3, updated="2026-11-15T12:00:00")}) == (0, 0)
    assert data["g/A.DNG"]["rating"] == 0
    # cleared on the phone after being rated on the Mac: the clearing wins
    data = {"g/B.DNG": entry(rating=4, updated="2026-11-15T12:00:00")}
    assert merge(data, {"100_1/B.DNG": entry(rating=0, updated="2026-11-15T18:00:00")}) == (0, 1)
    assert data["g/B.DNG"]["rating"] == 0
    # re-rated later: the rating wins over the clearing
    assert merge(data, {"100_1/B.DNG": entry(rating=2, updated="2026-11-15T19:00:00")}) == (0, 1)
    assert data["g/B.DNG"]["rating"] == 2


@pytest.mark.parametrize("incoming", [[1, 2], "text", None, 5])
def test_not_a_ratings_object(incoming):
    with pytest.raises(ValueError, match="not a ratings file"):
        merge({}, incoming)


# ------------------------------------------------------------------ the inbox folder


@pytest.fixture
def box(tmp_path):
    ratings = tmp_path / "ratings.json"
    ratings.write_text("{}")
    folder = tmp_path / "in"
    folder.mkdir()
    ib = Inbox(tmp_path / "settings.json", lambda: json.loads(ratings.read_text()),
               lambda d: ratings.write_text(json.dumps(d)), threading.Lock())
    ib.set_folder(str(folder))
    return ib, folder, lambda: json.loads(ratings.read_text())


def test_each_file_is_merged_once_then_again_when_it_changes(box):
    ib, folder, ratings = box
    f = folder / "pentax-cull-ratings-Pixel-20261115-120000.json"
    f.write_text(json.dumps({"g/A.DNG": entry(rating=3)}))
    assert ib.check()["last"]["added"] == 1
    ib.last = None
    ib.check()
    assert ib.last is None  # unchanged file: not read again
    f.write_text(json.dumps({"g/A.DNG": entry(rating=5, updated="2026-11-15T13:00:00")}))
    os.utime(f, (1, 1))
    assert ib.check()["last"]["updated"] == 1
    assert ratings()["g/A.DNG"]["rating"] == 5


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("list.json", b"[1, 2, 3]"),
        ("broken.json", b"{not json"),
        ("binary.json", bytes(range(256)) * 8),
        ("deep.json", b"[" * 100000 + b"]" * 100000),
    ],
)
def test_bad_files_are_reported_and_do_not_block_good_ones(box, name, content):
    ib, folder, ratings = box
    (folder / name).write_bytes(content)
    (folder / "z-good.json").write_text(json.dumps({"g/A.DNG": entry()}))
    st = ib.check()
    assert st["error"] and st["error"].startswith(name)
    assert ratings() == {"g/A.DNG": {"rating": 3, "datetime": DT, "updated": "2026-11-15T12:00:00"}}


def test_huge_file_is_ignored_without_reading_it(box):
    ib, folder, ratings = box
    with open(folder / "huge.json", "wb") as f:
        f.seek(25 * 1024 * 1024)
        f.write(b"}")
    assert "too big" in ib.check()["error"]
    assert ratings() == {}


def test_hidden_and_non_json_files_are_ignored(box):
    ib, folder, ratings = box
    (folder / ".hidden.json").write_text(json.dumps({"g/A.DNG": entry()}))
    (folder / "notes.txt").write_text(json.dumps({"g/B.DNG": entry()}))
    assert ib.check()["error"] is None
    assert ratings() == {}
