"""scripts/release/withdraw.py on a small release: a withdrawn repository's tier-A sessions move to tier B as pointers
with their labels, their copies leave tier A, every other row stays as it was, and the original release (hard-linked)
does not change.

    python -m pytest tests/test_withdraw.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import tiers
from test_tiers import SIGNALS, labelled, version  # noqa: F401  (fixtures)

SPEC = importlib.util.spec_from_file_location(
    "withdraw", Path(__file__).resolve().parents[1] / "scripts" / "release" / "withdraw.py")
withdraw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(withdraw)


def rows(folder: Path, pattern: str) -> list[dict]:
    out = []
    for f in sorted(folder.glob(pattern)):
        out += pq.read_table(f).to_pylist()
    return out


@pytest.fixture
def release(labelled, tmp_path):
    tiers.write(labelled, tmp_path / "w", log=lambda m: None, signals=SIGNALS)
    orig = tmp_path / "orig"
    orig.mkdir()
    (tmp_path / "w" / "a").rename(orig / "a-names")
    (tmp_path / "w" / "b").rename(orig / "b")
    new = tmp_path / "new"
    shutil.copytree(orig, new, copy_function=os.link)
    return labelled, orig, new


def test_a_withdrawn_repositorys_sessions_move_to_tier_b_and_nothing_else_changes(release):
    v, orig, new = release
    before = {p: p.read_bytes() for p in orig.rglob("*") if p.is_file()}
    a_conv = [r for r in rows(orig / "a-names", "conversations/*/*.parquet") if r["session_key"] != "entire:a"]
    b_old = rows(orig / "b", "pointers.parquet")

    rep = withdraw.run(v, orig, new, {"o/licensed"}, SIGNALS, date(2026, 11, 2), log=lambda m: None)

    # the original release: every byte as it was (the new one replaced its links, never wrote through them)
    assert {p: p.read_bytes() for p in orig.rglob("*") if p.is_file()} == before
    assert rep["moved_sessions"] == 1 and rep["copies_left_out"] == 1   # entire:a, and its copy entire:d
    a, b = new / "a-names", new / "b"
    assert {r["session_key"] for r in rows(a, "data/*/*.parquet")} == {"dataclaw:x"}
    assert rows(a, "conversations/*/*.parquet") == a_conv
    assert all(r["session_key"] != "entire:a" for r in rows(a, "conversations_subagents/*/*.parquet"))
    # tier B: the release's pointers as they were, and entire:a's, without text
    p = rows(b, "pointers.parquet")
    assert p[:len(b_old)] == b_old and [r["session_key"] for r in p[len(b_old):]] == ["entire:a"]
    assert "content" not in json.dumps(p[-1]["files"])
    assert "o/licensed" in (b / "repositories.txt").read_text().split()
    labels = rows(b, "labels/conversations/entire/*.parquet")
    mine = [r for r in labels if r["session_key"] == "entire:a"]
    assert [r["turn_number"] for r in mine] == [0, 1, 2] and mine[0]["text_id"] and "content" not in mine[0]
    helper = [r for r in rows(b, "labels/conversations_subagents/entire/*.parquet") if r["session_key"] == "entire:a"]
    assert [r["task_pk"] for r in helper] == ["p#t2"]
    # the changelogs' counts and the owners follow
    ca = json.loads((a / "changelog.json").read_text())
    cb = json.loads((b / "changelog.json").read_text())
    assert ca["counts"]["sessions"] == 1 and ca["counts"]["copies"] == 0
    assert cb["counts"]["sessions"] == len(b_old) + 1 and ca["withdrawn"]["sessions_moved_to_tier_b"] == 1
    assert cb["labels"]["message_labels"]["rows"]["conversations"] == len(rows(b, "labels/conversations/*/*.parquet"))
    assert [r["repo"] for r in rows(a, "repositories.parquet")] == []
    assert {"repo": "o/licensed", "owner_type": "person"} in rows(b, "repositories.parquet")
    assert rep["pointers_made_differ_from_release"] == 0 and rep["original_unchanged_files"] == len(before)


def test_refuses_a_copy_that_is_not_hard_linked(release, tmp_path):
    v, orig, _ = release
    other = tmp_path / "copied"
    shutil.copytree(orig, other)
    with pytest.raises(ValueError, match="hard-linked"):
        withdraw.run(v, orig, other, {"o/licensed"}, SIGNALS, date(2026, 11, 2), log=lambda m: None)


def test_refuses_the_original_itself(release):
    v, orig, _ = release
    with pytest.raises(ValueError, match="a copy"):
        withdraw.run(v, orig, orig, {"o/licensed"}, SIGNALS, date(2026, 11, 2), log=lambda m: None)


VERIFY = Path(__file__).resolve().parents[1] / "scripts" / "release" / "verify_withdraw.py"


def verify(orig, new, tmp_path, snap):
    import subprocess
    import sys
    wd = tmp_path / "wd.tsv"
    wd.write_text("gone\to/licensed\t{}\n")
    out = tmp_path / "verify.json"
    p = subprocess.run([sys.executable, str(VERIFY), str(orig), str(new), str(wd), str(snap), str(out)],
                       capture_output=True, text=True)
    return p.returncode, json.loads(out.read_text())


def test_the_checks_pass_a_clean_withdrawal_and_fail_a_tampered_one(release, tmp_path):
    v, orig, new = release
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(withdraw.snapshot(orig)))
    withdraw.run(v, orig, new, {"o/licensed"}, SIGNALS, date(2026, 11, 2), log=lambda m: None)
    code, got = verify(orig, new, tmp_path, snap)
    assert code == 0 and got["pass"] and got["tier_a"]["moved"] == 1 and got["kept"]["all"] == got["kept"]["tier_a"] + got["kept"]["tier_b"]
    assert got["labels_added"]["conversations"] == {"rows": 3, "same_marks_as_tier_a": 3}
    # a mark changed in an added label row: caught
    f = next((new / "b" / "labels" / "conversations" / "entire").glob("*.parquet"))
    t = pq.read_table(f).to_pylist()
    t[-1]["actor"] = "developer" if t[-1]["actor"] != "developer" else "agent"
    os.unlink(f)
    pq.write_table(pa.Table.from_pylist(t, schema=pq.read_schema(next((orig / "b" / "labels" / "conversations" / "entire").glob("*.parquet")))), f)
    code, got = verify(orig, new, tmp_path, snap)
    assert code == 1 and any("same marks" in x for x in got["failures"])


def test_the_checks_fail_when_the_original_changed(release, tmp_path):
    v, orig, new = release
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(withdraw.snapshot(orig)))
    withdraw.run(v, orig, new, {"o/licensed"}, SIGNALS, date(2026, 11, 2), log=lambda m: None)
    (orig / "b" / "repositories.txt").write_text("changed\n")
    code, got = verify(orig, new, tmp_path, snap)
    assert code == 1 and any("original release unchanged" in x for x in got["failures"])
