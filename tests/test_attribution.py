"""Entire's own line attribution as a side table: only kept Entire sessions whose metadata.json holds
initial_attribution, split by tier, the split of committed sessions counted, the changelogs replaced (never written
through a hard link), nothing written without --write.

    python -m pytest tests/test_attribution.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from collect.build import SCHEMA  # noqa: E402

from test_check import _row  # noqa: E402

SCRIPT = ROOT / "scripts" / "release" / "attribution_release.py"


def _session(key: str, meta: dict | None, dup: str | None = None) -> dict:
    r = _row(key, '{"type":"user","message":"hi"}\n', {})
    r.update(session_key=f"entire:{key}", duplicate_of=dup, agent="Claude Code")
    if meta is not None:
        r["files"] = r["files"] + [{"path": "metadata.json", "bytes": 1, "sha256": "0" * 64, "content": json.dumps(meta)}]
    return r


def _att(pct, total) -> dict:
    return {"checkpoint_id": "c1", "initial_attribution": {
        "calculated_at": "2026-03-01T00:00:00Z", "agent_lines": 7, "human_added": 1, "human_modified": 0,
        "human_removed": 2, "total_committed": total, "agent_percentage": pct}}


def _setup(tmp: Path) -> tuple[Path, Path, Path]:
    v = tmp / "v"
    (v / "data" / "entire").mkdir(parents=True)
    rows = [_session("a", _att(100, 10)), _session("b", _att(0, 5)), _session("c", _att(50.5, 0)),
            _session("d", _att(100, 10), dup="entire:a"), _session("e", {"checkpoint_id": "x"}), _session("f", None),
            _session("g", _att(42.0, 8)), _session("h", _att(99, 3))]     # h: 99% counts as 99%+
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), str(v / "data" / "entire" / "part-0.parquet"))
    a = tmp / "a"
    (a / "data" / "entire").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([rows[0], rows[6]], schema=SCHEMA), str(a / "data" / "entire" / "p.parquet"))
    b = tmp / "b"
    b.mkdir()
    for t in (a, b):
        (t / "changelog.json").write_text(json.dumps({"version": "2026-11-02"}))
    return v, a, b


def _run(*args) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env)


def test_counts_only_without_write(tmp_path: Path) -> None:
    v, a, b = _setup(tmp_path)
    run = _run(v, a, b, tmp_path / "s.json")
    assert run.returncode == 0, run.stderr
    s = json.loads((tmp_path / "s.json").read_text())
    assert s["all"] == {"sessions": 5, "committed lines": 4, "agent 99%+": 2, "agent none": 1, "between": 1,
                        "pct agent 99%+": 50.0, "pct agent none": 25.0, "pct between": 25.0}
    assert s["tier A"]["sessions"] == 2 and s["tier B"]["sessions"] == 3
    assert not (a / "attribution").exists() and not (b / "attribution").exists()


def test_tables_and_changelogs_written_beside_never_through_a_link(tmp_path: Path) -> None:
    v, a, b = _setup(tmp_path)
    orig = tmp_path / "orig-changelog.json"
    os.link(a / "changelog.json", orig)                     # a hard-linked copy's original
    run = _run(v, a, b, tmp_path / "s.json", "--write")
    assert run.returncode == 0, run.stderr
    ta = pq.read_table(a / "attribution" / "entire.parquet").to_pylist()
    tb = pq.read_table(b / "attribution" / "entire.parquet").to_pylist()
    assert [r["session_key"] for r in ta] == ["entire:a", "entire:g"]
    assert [r["session_key"] for r in tb] == ["entire:b", "entire:c", "entire:h"]
    assert ta[0]["agent_percentage"] == 100.0 and ta[0]["total_committed"] == 10 and ta[0]["checkpoint_id"] == "c1"
    assert tb[1]["agent_percentage"] == 50.5 and tb[1]["agent"] == "Claude Code"
    assert json.loads((a / "changelog.json").read_text())["attribution"]["sessions"] == 2
    assert json.loads((b / "changelog.json").read_text())["attribution"]["sessions"] == 3
    assert json.loads(orig.read_text()) == {"version": "2026-11-02"}   # the original untouched
