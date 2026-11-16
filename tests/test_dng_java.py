"""The Android app writes ratings with Dng.java: it must produce exactly the same bytes as dng.py."""

import glob
import os
import shutil
import subprocess
from pathlib import Path

import dng
import pytest
from conftest import make_dng, xmp_packet

DNG_JAVA = Path(__file__).parent.parent / "packages/android/src/local/pentaxcull/Dng.java"


def _java():
    """java 11+ (runs a single source file). JAVA env var, then PATH, then the JDK used by packages/android."""
    for j in [os.environ.get("JAVA"), shutil.which("java"), *sorted(glob.glob(os.path.expanduser(
            "~/Developer/jdk-*/Contents/Home/bin/java")))[::-1]]:
        # `--version` only exists from Java 9 on; running a source file needs 11+ (Java 8 is often first in PATH)
        r = subprocess.run([j, "--version"], capture_output=True, text=True) if j else None
        if r and r.returncode == 0 and int(r.stdout.split()[1].split(".")[0]) >= 11:
            return j
    return None


JAVA = _java()
pytestmark = pytest.mark.skipif(JAVA is None, reason="needs a JDK (11+)")


def _java_write(path, rating):
    r = subprocess.run([JAVA, str(DNG_JAVA), str(path), str(rating)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.split(" -> ")[0]


@pytest.mark.parametrize(
    "xmp",
    [
        None,
        xmp_packet('<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="2"/>'),
        xmp_packet('<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Label="Red"/>', 500),
        xmp_packet('<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Label="Red"/>'),
    ],
    ids=["no-xmp", "rating-attr", "padding", "full"],
)
def test_same_bytes_as_python(tmp_path, xmp):
    py, jv = make_dng(tmp_path / "py.DNG", xmp=xmp), make_dng(tmp_path / "java.DNG", xmp=xmp)
    for rating in (4, 2, 5):
        assert _java_write(jv, rating) == dng.write_rating(str(py), rating)
        assert jv.read_bytes() == py.read_bytes()
