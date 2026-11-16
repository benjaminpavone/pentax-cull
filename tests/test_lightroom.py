"""Lightroom plugin (Lua), run with LuaJIT (Lua 5.1, like Lightroom) against a stand-in of the Lightroom SDK."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parent.parent / "packages/lightroom-plugin/PentaxCull.lrplugin"
MOCK = Path(__file__).parent / "lightroom/mock.lua"


def _lua():
    cands = [os.environ.get("LUA"), shutil.which("luajit"), shutil.which("lua5.1"),
             str(Path(sys.executable).parent / "luajit")]                    # conda env: environment.yml
    return next((c for c in cands if c and os.path.exists(c)), None)


LUA = _lua()
pytestmark = pytest.mark.skipif(LUA is None, reason="needs luajit (in environment.yml) or lua5.1")


def lua(code):
    r = subprocess.run([LUA, "-e", code], cwd=PLUGIN, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_json_decoder_handles_accents_and_surrogate_pairs():
    out = lua(r'''
        package.path = "./?.lua;" .. package.path
        local d = require("json").decode([[{"g/café.DNG":{"rating":3},"g/📷.DNG":{"rating":5}}]])
        for k, v in pairs(d) do io.write(k, "=", v.rating, "\n") end''')
    assert set(out.splitlines()) == {"g/café.DNG=3", "g/📷.DNG=5"}


@pytest.mark.parametrize("bad", ["[1,", '{"a":"\\uZZZZ"}', '{"a" 1}', '{"a":tru}'])
def test_json_decoder_rejects_broken_files(bad):
    out = lua(f'''
        package.path = "./?.lua;" .. package.path
        local ok, e = pcall(require("json").decode, {json.dumps(bad)})
        io.write(tostring(ok), " ", tostring(e))''')
    assert out.startswith("false invalid JSON")


def test_apply_ratings_including_cleared_ones(tmp_path):
    f = tmp_path / "ratings.json"
    f.write_text(json.dumps({
        "100_1/A.DNG": {"rating": 5, "datetime": "2026-09-18T15:00:00"},
        "100_1/B.DNG": {"rating": 0, "datetime": "2026-09-18T15:01:00"},   # cleared: 4 stars -> none
        "100_1/C.DNG": {"rating": 0, "datetime": "2026-09-18T15:02:00"},   # cleared, already none
        "100_1/D.DNG": {"rating": 3, "datetime": "2026-09-18T15:03:00"},   # already right
        "100_1/E.DNG": {"rating": 9, "datetime": "2026-09-18T15:04:00"},   # invalid: ignored
    }))
    r = subprocess.run([LUA, str(MOCK), str(f)], cwd=PLUGIN, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    result = dict(line.split()[1:] for line in r.stdout.splitlines() if line.startswith("RESULT "))
    assert result == {"A.DNG": "5", "B.DNG": "nil", "C.DNG": "nil", "D.DNG": "3", "E.DNG": "2"}
    assert "Apply 2 rating(s)?" in r.stdout and "Including 1 rating(s) cleared" in r.stdout
