"""refresh_numbers.py: the numbers that describe the published text agree with it again; nothing else changes.

    python -m pytest tests/test_refresh_numbers.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCRIPT = ROOT / "scripts" / "release" / "refresh_numbers.py"
FILES = pa.list_(pa.struct([("path", pa.string()), ("bytes", pa.int64()), ("sha256", pa.string()), ("content", pa.string())]))


def _tier(root: Path) -> Path:
    t = root / "a-names"
    (t / "data" / "entire").mkdir(parents=True)
    good = "plain text\n"
    files = [[{"path": "a.jsonl", "bytes": 3, "sha256": "0" * 64, "content": "x [REDACTED] y"},
              {"path": "b.jsonl", "bytes": len(good), "sha256": hashlib.sha256(good.encode()).hexdigest(), "content": good}]]
    pq.write_table(pa.table({"session_key": ["entire:a"], "bytes": [999], "files": pa.array(files, FILES)}),
                   str(t / "data" / "entire" / "part-000-00000.parquet"))
    (t / "conversations" / "entire").mkdir(parents=True)
    pq.write_table(pa.table({"session_key": ["entire:a", "entire:a"], "content": ["a [REDACTED] b", "two words"],
                             "word_count": [9, 2], "char_count": [9, 9]}),
                   str(t / "conversations" / "entire" / "part-000.parquet"))
    return t


def _run(tier: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(tier), "--procs", "1", *extra], capture_output=True,
                          text=True, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})


def test_stale_numbers_are_counted_then_refreshed_and_nothing_else_changes(tmp_path: Path) -> None:
    t = _tier(tmp_path)
    before = {p: pq.read_table(p) for p in t.rglob("*.parquet")}
    dry = _run(t)
    assert dry.returncode == 0 and 'disagree: {"session files": 1, "message rows": 1}' in dry.stdout, dry.stdout + dry.stderr
    assert all(pq.read_table(p).equals(x) for p, x in before.items())          # counting changes nothing
    run = _run(t, "--write")
    assert run.returncode == 0 and '"files rewritten": 2' in run.stdout, run.stdout + run.stderr
    s = pq.read_table(t / "data" / "entire" / "part-000-00000.parquet").to_pylist()[0]
    data = "x [REDACTED] y".encode()
    assert s["files"][0] == {"path": "a.jsonl", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "content": "x [REDACTED] y"}
    assert s["files"][1] == before[t / "data" / "entire" / "part-000-00000.parquet"].to_pylist()[0]["files"][1]
    assert s["bytes"] == 999                                                    # the size as fetched is left alone
    m = pq.read_table(t / "conversations" / "entire" / "part-000.parquet").to_pylist()
    assert [(r["word_count"], r["char_count"]) for r in m] == [(3, 14), (2, 9)]
    assert _run(t).stdout.startswith("disagree: {}")


def test_a_rewrite_that_would_change_text_is_refused(tmp_path: Path) -> None:
    t = _tier(tmp_path)
    spec = importlib.util.spec_from_file_location("refresh_numbers", SCRIPT)
    rn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rn)
    real = rn._texts
    calls = []
    rn._texts = lambda table: (calls.append(1), real(table) + (["changed"] if len(calls) % 2 == 0 else []))[1]
    with pytest.raises(RuntimeError, match="would change its text"):
        rn.fix_file(("message", str(t / "conversations" / "entire" / "part-000.parquet"), True))


def test_the_text_guard_reads_the_message_text_and_an_empty_tier_fails(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("refresh_numbers", SCRIPT)
    rn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rn)
    t = _tier(tmp_path)
    texts = rn._texts(pq.read_table(t / "conversations" / "entire" / "part-000.parquet"))
    assert "a [REDACTED] b" in texts and "two words" in texts
    files = rn._texts(pq.read_table(t / "data" / "entire" / "part-000-00000.parquet"))
    assert "x [REDACTED] y" in files and "0" * 64 not in files                  # the digest is the one string left out
    (tmp_path / "empty").mkdir()
    run = _run(tmp_path / "empty")
    assert run.returncode != 0 and "no session or message tables" in run.stderr
