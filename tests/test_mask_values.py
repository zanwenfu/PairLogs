"""Values a second scanner found, written privately and masked in a tier as published: second_scan.py --want/--values-out
writes only the wanted values, only to its private file, and rescans only the files named; mask_finding.py --values
masks every value wherever it occurs (the longest first), on hard-linked copies without touching the originals, counts
them in the sessions' redactions and the changelog, and never prints a value.

    python -m pytest tests/test_mask_values.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from collect import conv_build, events  # noqa: E402
from collect.build import SCHEMA  # noqa: E402

from test_check import _row  # noqa: E402

LONG = "tokA_" + "Zq8vN3xLw7PkR2tYb5HsQm4C"          # a made-up token
INNER = LONG[5:20]                                    # a value that is also part of LONG
PREFIX = LONG[:14]                                    # a value LONG begins with: the longer must win where both start
OTHER = "tokA_" + "Hh2Jj3Kk4Ll5Mm6Nn7Pp8Qq9"    # 24 after the prefix, as the stand-in scanner reads
MASK_SCRIPT = ROOT / "scripts" / "release" / "mask_finding.py"
SCAN_SCRIPT = ROOT / "scripts" / "release" / "second_scan.py"


def _tier(root: Path) -> Path:
    t = root / "a-names"
    (t / "data" / "entire").mkdir(parents=True)
    rows = [_row("a", f'{{"type":"user","message":"use {LONG} now"}}\n', {}),
            _row("b", '{"type":"user","message":"nothing here"}\n', {})]
    rows[0]["session_key"], rows[1]["session_key"] = "entire:a", "entire:b"
    rows[0]["redactions"]["secrets"] = 2
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), str(t / "data" / "entire" / "part-000-00000.parquet"))
    msgs = events.finish([events.row(role="user", turn_type="user_prompt", content=f"key {LONG} and {OTHER}",
                                     kind="text"),
                          events.row(role="user", turn_type="user_prompt", content=f"part {INNER} alone",
                                     kind="text")],
                         {"source": "entire", "session_key": "entire:a"})
    w = conv_build._Writer(t / "conversations" / "entire" / "part-000.parquet", conv_build.SCHEMA)
    w.add(msgs)
    w.close()
    (t / "copied" / "ds").mkdir(parents=True)
    (t / "copied" / "ds" / "rows.jsonl").write_text(json.dumps({"text": f"{OTHER} twice {OTHER}"}) + "\n")
    pq.write_table(pa.table({"text": [f"x {LONG}", "plain"], "n": [1, 2]}), str(t / "copied" / "ds" / "t.parquet"))
    (t / "README.md").write_text("# card\n")
    (t / "changelog.json").write_text(json.dumps({"version": "2026-11-02"}))
    return t


def _linked_copy(src: Path, dst: Path) -> None:
    """As `cp -al`: the copy's files are the original's, hard-linked; the original's made read-only."""
    for p in sorted(src.rglob("*")):
        q = dst / p.relative_to(src)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            os.link(p, q)
            p.chmod(stat.S_IRUSR)


def _bytes(t: Path) -> dict:
    return {str(p.relative_to(t)): p.read_bytes() for p in sorted(t.rglob("*")) if p.is_file()}


def _values_file(path: Path, values: list[str]) -> Path:
    path.write_text("".join(json.dumps(v) + "\n" for v in values))
    return path


def _mask(tier: Path, values: Path, *extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(MASK_SCRIPT), str(tier), "--values", str(values), "--label",
                           "trufflehog: test", *extra], capture_output=True, text=True, env=env, cwd=tier.parent)


def _occurs(tier: Path, value: str) -> int:
    n = 0
    for p in tier.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix == ".parquet":
            n += json.dumps(pq.read_table(p).to_pylist()).count(value)
        else:
            n += p.read_text(encoding="utf-8", errors="replace").count(value)
    return n


def test_values_are_masked_everywhere_on_the_copy_only(tmp_path: Path) -> None:
    orig = _tier(tmp_path / "orig")
    copy = tmp_path / "copy" / "a-names"
    _linked_copy(orig, copy)
    before = _bytes(orig)
    values = _values_file(tmp_path / "values.jsonl", [INNER, OTHER, LONG, PREFIX])

    dry = _mask(copy, values)
    assert dry.returncode == 0, dry.stderr
    assert _bytes(copy) == before
    got = json.loads(dry.stdout.split("would mask: ", 1)[1].splitlines()[0])
    # LONG: data files 1, conversations 1, copied parquet 1; OTHER: conversations 1, copied jsonl 2; INNER alone: 1
    assert got["occurrences in message rows"] == 3 and got["occurrences in copied files"] == 3
    assert got["occurrences in session rows"] == 1 and got["sessions"] == 1 and got["values"] == 4

    run = _mask(copy, values, "--write", "--procs", "2")                # workers, as on the real tier
    assert run.returncode == 0, run.stdout + run.stderr
    assert "after: 0 of 4 values still occur" in run.stdout
    for v in (LONG, OTHER, INNER, LONG[-6:]):                      # LONG's tail: no part of LONG is left
        assert v not in run.stdout + run.stderr
        assert _occurs(copy, v) == 0
    assert _bytes(orig) == before                                   # the originals untouched
    assert _occurs(orig, LONG) > 0
    assert _occurs(copy, "[REDACTED]") >= 7
    s = {r["session_key"]: r["redactions"]["secrets"] for r in pq.read_table(copy / "data").to_pylist()}
    assert s == {"entire:a": 2 + 4, "entire:b": 0}                   # 1 in its files, 3 in its message rows
    log = json.loads((copy / "changelog.json").read_text())["masked_after_release_check"]
    assert len(log) == 1 and log[0]["rule"] == "trufflehog: test" and log[0]["values"] == 4
    assert "a second scanner" in log[0]["how"]


def test_a_short_value_is_refused(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    before = _bytes(tier)
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, "abc123"]), "--write")
    assert run.returncode != 0 and "refused" in run.stdout + run.stderr
    assert _bytes(tier) == before


def test_a_mask_word_is_refused(tmp_path: Path) -> None:
    """The bare word REDACTED (a scanner's find where a source had already masked a password) would mask every mask
    again; with fewer than 8 other characters a value holding it is no secret."""
    tier = _tier(tmp_path)
    before = _bytes(tier)
    for word in ("REDACTED", "token=REDACTED", "xx[REDACTED", "Redacted]"):
        run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, word]), "--write")
        assert run.returncode != 0 and "refused: 1 values" in run.stdout + run.stderr, word
        assert _bytes(tier) == before
    ok = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, "client=4417823901REDACTED"]))
    assert ok.returncode == 0, ok.stderr                    # a value with more than a mask word in it is kept


def test_each_value_is_counted_where_masking_reaches(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    before = _bytes(tier)
    out = tmp_path / "per.json"
    sha = lambda v: hashlib.sha256(v.encode()).hexdigest()
    for procs in ("1", "2"):
        run = _mask(tier, _values_file(tmp_path / "v.jsonl", [INNER, OTHER, LONG, PREFIX]), "--per-value", str(out),
                    "--procs", procs)
        assert run.returncode == 0, run.stderr
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
        got = {r["sha256"]: r["occurrences"] for r in json.loads(out.read_text())["by value"]}
        # LONG: a session file, a message, a copied table; OTHER: a message, twice in a copied file; INNER alone once
        # and inside LONG three times, PREFIX inside LONG three times (a value inside a longer one counted in both)
        assert got == {sha(LONG): 3, sha(OTHER): 3, sha(INNER): 4, sha(PREFIX): 3}, procs
        assert 'values by occurrences: {"0": 0, "1": 0, "2-10": 4' in run.stdout
        for v in (LONG, OTHER, INNER):
            assert v not in run.stdout + run.stderr + out.read_text()
    assert _bytes(tier) == before


FAKE_TH = '''#!{python}
"""A stand-in for TruffleHog: each tokA_ value in the files named, as TruffleHog's JSON lines."""
import json, re, sys
for path in [a for a in sys.argv[2:] if not a.startswith("--")]:
    for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        for m in re.finditer(r"tokA_[A-Za-z0-9]{{24}}", line):
            print(json.dumps({{"DetectorName": "Fake", "DecoderName": "PLAIN", "Verified": False, "Raw": m.group(0),
                              "SourceMetadata": {{"Data": {{"Filesystem": {{"file": path, "line": n}}}}}}}}))
'''


def _scan(tmp_path: Path, tier: Path, want: list[str], files: list[str]) -> tuple[subprocess.CompletedProcess, Path]:
    th = tmp_path / "fake-trufflehog"
    th.write_text(FAKE_TH.format(python=sys.executable))
    th.chmod(0o700)
    (tmp_path / "want.txt").write_text("".join(hashlib.sha256(v.encode()).hexdigest() + "\n" for v in want))
    (tmp_path / "files.txt").write_text("".join(f + "\n" for f in files))
    out = tmp_path / "values.jsonl"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
           "ERRATA_DATA_ROOT": str(tmp_path / "data"), "ERRATA_STATE_ROOT": str(tmp_path / "state")}
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "HF_TOKEN"):
        env.pop(k, None)
    run = subprocess.run([sys.executable, str(SCAN_SCRIPT), str(tier), str(tmp_path / "out.json"),
                          str(tmp_path / "hits.jsonl"), "--trufflehog", str(th), "--procs", "2",
                          "--files", str(tmp_path / "files.txt"), "--want", str(tmp_path / "want.txt"),
                          "--values-out", str(out)], capture_output=True, text=True, env=env, cwd=tmp_path)
    return run, out


def test_the_second_scan_writes_only_the_wanted_values_privately(tmp_path: Path) -> None:
    tier = _tier(tmp_path / "t")
    conv = "conversations/entire/part-000.parquet"
    run, out = _scan(tmp_path, tier, [OTHER], [conv])
    assert run.returncode == 0, run.stdout + run.stderr
    assert [json.loads(l) for l in out.read_text().splitlines()] == [OTHER]
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    for v in (LONG, OTHER):
        assert v not in run.stdout + run.stderr
        assert v not in (tmp_path / "hits.jsonl").read_text() + (tmp_path / "out.json").read_text()
    summary = json.loads((tmp_path / "out.json").read_text())
    assert summary["units"] == 1 and summary["of_units"] == 1
    hits = [json.loads(l) for l in (tmp_path / "hits.jsonl").read_text().splitlines()]
    assert {h["published_file"] for h in hits} == {conv}
    assert {h["sha256"] for h in hits} == {hashlib.sha256(v.encode()).hexdigest() for v in (LONG, OTHER)}
    assert not list(tmp_path.glob("hits.jsonl.parts"))


def test_the_second_scan_fails_when_a_wanted_value_is_not_found_again(tmp_path: Path) -> None:
    tier = _tier(tmp_path / "t")
    run, out = _scan(tmp_path, tier, [OTHER, LONG], ["copied/ds/rows.jsonl"])   # LONG is not in that file
    assert run.returncode == 1, run.stdout + run.stderr
    assert "not found again: 1" in run.stdout
    assert [json.loads(l) for l in out.read_text().splitlines()] == [OTHER]


def test_masked_values_are_gone_from_a_second_scan(tmp_path: Path) -> None:
    tier = _tier(tmp_path / "t")
    run, out = _scan(tmp_path, tier, [OTHER, LONG], ["conversations/entire/part-000.parquet"])
    assert run.returncode == 0, run.stdout + run.stderr
    assert _mask(tier, out, "--write").returncode == 0
    shutil.rmtree(tmp_path / "data", ignore_errors=True)
    again, _ = _scan(tmp_path, tier, [], [str(p.relative_to(tier)) for p in tier.rglob("*") if p.is_file()])
    assert json.loads((tmp_path / "out.json").read_text())["findings_logged"] == 0, again.stdout


def test_a_value_left_where_masking_does_not_reach_fails(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    (tier / "work_labels").mkdir()
    (tier / "work_labels" / "meta.json").write_text(json.dumps({"note": OTHER}))   # outside what is masked
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, OTHER]), "--write")
    assert run.returncode == 1 and "after: 1 of 2 values still occur in the tier (1 times)" in run.stdout, run.stdout
    assert OTHER not in run.stdout + run.stderr


def test_the_one_finding_mode_still_masks_what_gitleaks_finds(tmp_path: Path) -> None:
    from test_check import _gitleaks

    if not _gitleaks():
        import pytest
        pytest.skip("gitleaks is not here")
    tier = _tier(tmp_path)
    key = "Xr5Tb8Nq2Lm7Kv4Pz9Wc"           # found as a generic-api-key, as on 2026-11-04; in no other value here
    (tier / "copied" / "ds" / "key.jsonl").write_text("export API_KEY=\"" + key + "\"\n")
    (tier / "copied" / "ds" / "again.jsonl").write_text(json.dumps({"text": "and " + key}) + "\n")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    rule = subprocess.run([sys.executable, str(MASK_SCRIPT), str(tier), "copied/ds/key.jsonl", "generic-api-key"],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert rule.returncode == 0 and '"occurrences in copied files": 2' in rule.stdout, rule.stdout + rule.stderr
    run = subprocess.run([sys.executable, str(MASK_SCRIPT), str(tier), "copied/ds/key.jsonl", "generic-api-key",
                          "--write"],
                         capture_output=True, text=True, env=env, cwd=tmp_path)
    assert run.returncode == 0 and "the value occurs 0 times in the tier" in run.stdout, run.stdout + run.stderr
    assert key not in run.stdout + run.stderr and _occurs(tier, key) == 0
    assert json.loads((tier / "changelog.json").read_text())["masked_after_release_check"][0]["found_in"] == \
        "copied/ds/key.jsonl"


def test_a_value_that_is_also_an_identifier_is_left_out(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    ident = "tokA_" + "Ww1Xx2Yy3Zz4Aa5Bb6Cc7Dd8"
    msgs = events.finish([events.row(role="assistant", turn_type="tool_call", content=f"called with {ident}",
                                     kind="tool_call", tool_call_id=ident)],
                         {"source": "entire", "session_key": "entire:b"})
    w = conv_build._Writer(tier / "conversations" / "entire" / "part-001.parquet", conv_build.SCHEMA)
    w.add(msgs)
    w.close()
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, ident]), "--write", "--procs", "3")
    assert run.returncode == 0, run.stdout + run.stderr
    got = json.loads(run.stdout.split("masked: ", 1)[1].splitlines()[0])
    assert got["values left out: they stand in an identifier column"] == 1 and got["values"] == 1
    assert _occurs(tier, LONG) == 0
    t = pq.read_table(tier / "conversations" / "entire" / "part-001.parquet").to_pylist()
    assert t[0]["tool_call_id"] == ident and ident in t[0]["content"]      # left whole, the join intact
    assert ident not in run.stdout + run.stderr


def _nested(tier: Path, rows: list) -> Path:
    """A copied table shaped as OpenHands feedback's: its text in a list of event structs."""
    ev = pa.struct([("content", pa.string()), ("extras", pa.string()), ("id", pa.int64()), ("event_id", pa.string())])
    p = tier / "copied" / "oh" / "data" / "train.parquet"
    p.parent.mkdir(parents=True)
    schema = pa.schema([("version", pa.string()), ("trajectory", pa.list_(ev))])
    pq.write_table(pa.table({"version": ["1"] * len(rows), "trajectory": rows}, schema=schema), str(p))
    return p


def test_text_nested_in_a_copied_table_is_masked(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    ident = "tokA_" + "Ee1Ff2Gg3Hh4Ii5Jj6Kk7Ll8"
    p = _nested(tier, [[{"content": f"run {LONG}", "extras": json.dumps({"cmd": f"echo {OTHER}"}), "id": 1,
                         "event_id": "e1"},
                        {"content": None, "extras": "{}", "id": 2, "event_id": ident}],
                       None,
                       [{"content": f"see {ident} and {OTHER}", "extras": "", "id": 3, "event_id": "e3"}]])
    before = pq.read_table(p)
    values = _values_file(tmp_path / "v.jsonl", [LONG, OTHER, ident])
    dry = _mask(tier, values)
    got = json.loads(dry.stdout.split("would mask: ", 1)[1].splitlines()[0])
    assert got["values left out: they stand in an identifier column"] == 1     # ident, in a nested `event_id`
    # copied: OTHER twice in rows.jsonl, LONG once in t.parquet; nested: LONG once, OTHER twice
    assert got["occurrences in copied files"] == 6 and got["copied Parquet files"] == 2, got

    run = _mask(tier, values, "--write", "--procs", "2")
    assert run.returncode == 0 and "after: 0 of 2 values still occur" in run.stdout, run.stdout + run.stderr
    assert _occurs(tier, LONG) == _occurs(tier, OTHER) == 0
    t = pq.read_table(p)
    rows, old = t.to_pylist(), before.to_pylist()
    assert t.schema == before.schema and rows[1]["trajectory"] is None
    assert rows[0]["trajectory"][0] == {**old[0]["trajectory"][0], "content": "run [REDACTED]",
                                        "extras": json.dumps({"cmd": "echo [REDACTED]"})}
    assert rows[0]["trajectory"][1] == old[0]["trajectory"][1]
    assert rows[2]["trajectory"][0]["content"] == f"see {ident} and [REDACTED]"   # an identifier's value left out


def test_the_count_after_the_mask_reads_nested_text(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("mask_finding", MASK_SCRIPT)
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    p = _nested(tmp_path, [[{"content": "x", "extras": f"a {LONG}", "id": 1, "event_id": OTHER}], None, []])
    assert mf.left_in(mf.Matcher([LONG, OTHER]), p) == (2, {LONG, OTHER})


SORT_SCRIPT = ROOT / "scripts" / "release" / "sort_hits.py"
CONTEXT_SCRIPT = ROOT / "scripts" / "release" / "hit_contexts.py"


def test_findings_are_sorted_by_what_their_values_look_like(tmp_path: Path) -> None:
    def hit(det, sha, prefix="", ph=None, f="conversations/entire/part-000.parquet"):
        return {"detector": det, "sha256": sha * 64, "prefix": prefix, "placeholder_password": ph, "len": 40,
                "shape": "a", "published_file": f, "line": 1, "decoder": "PLAIN", "verified": False}
    rows = [hit("Github", "a", "ghp_"), hit("Github", "a", "ghp_", f="copied/ds/rows.jsonl"), hit("Github", "b"),
            hit("Postgres", "c", "postgres://", True), hit("Postgres", "d", "postgres://", False),
            hit("NpmToken", "e"), hit("PrivateKey", "f"), hit("NpmToken", "g", "npm_")]
    (tmp_path / "hits.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    run = subprocess.run([sys.executable, str(SORT_SCRIPT), str(tmp_path / "hits.jsonl"), str(tmp_path / "out.json"),
                          "--want-class", "provider format,connection string", "--want", str(tmp_path / "w.txt"),
                          "--files", str(tmp_path / "f.txt")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    s = json.loads((tmp_path / "out.json").read_text())
    assert s["findings"] == 8 and s["distinct values"] == 7
    assert s["by class (distinct values)"] == {"provider format": 3, "near a service name": 2, "placeholder": 1,
                                               "connection string": 1}
    assert (tmp_path / "w.txt").read_text().split() == sorted(c * 64 for c in "adfg")
    assert (tmp_path / "f.txt").read_text().split() == ["conversations/entire/part-000.parquet", "copied/ds/rows.jsonl"]


def test_contexts_show_where_a_value_stands_never_the_value(tmp_path: Path) -> None:
    tier = _tier(tmp_path / "t")
    run, out = _scan(tmp_path, tier, [OTHER, LONG], ["conversations/entire/part-000.parquet", "copied/ds/rows.jsonl"])
    assert run.returncode == 0, run.stdout + run.stderr
    files = tmp_path / "files.txt"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    ctx = subprocess.run([sys.executable, str(CONTEXT_SCRIPT), str(tier), str(out), str(tmp_path / "hits.jsonl"),
                          str(files), "--per", "2"], capture_output=True, text=True, env=env)
    assert ctx.returncode == 0, ctx.stderr
    for v in (LONG, OTHER, INNER):
        assert v not in ctx.stdout
    assert "key «aaaa_" in ctx.stdout and "Fake" in ctx.stdout and "not found" not in ctx.stdout
    assert ctx.stdout.count("\n") == 3      # LONG once in the files named, OTHER twice (of its three: --per 2)


def test_a_copied_file_keeps_every_byte_but_the_values(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    raw = ("first line\n" + json.dumps({"k": "x " + OTHER}) + "\n\n" + "ünïcode ok " + LONG + "\r\nlast").encode()
    (tier / "copied" / "ds" / "multi.jsonl").write_bytes(raw)
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, OTHER]), "--write", "--procs", "2")
    assert run.returncode == 0, run.stdout + run.stderr
    want = raw.replace(LONG.encode(), b"[REDACTED]").replace(OTHER.encode(), b"[REDACTED]")
    assert (tier / "copied" / "ds" / "multi.jsonl").read_bytes() == want


def test_patterns_are_grouped_by_size_and_mask_as_one_pattern_would() -> None:
    """Long values go in small patterns (RE2 slows on large ones): every group within CHUNK values and CHARS
    characters (a value longer than CHARS alone), as full as the limits allow, in the longest-first order; the masked
    text and counts equal one pattern holding every value."""
    sys.path.insert(0, str(ROOT / "scripts" / "release"))
    import mask_finding as mf

    vals = [LONG, PREFIX, INNER, OTHER, "w" * 40_000] + [f"v{i:07d}-" + "x" * (i % 50) for i in range(1000)]
    small = mf.Matcher(vals)

    class One(mf.Matcher):
        CHUNK, CHARS = 10 ** 6, 10 ** 9

    one = One(vals)
    assert len(one.patterns) == 1 and len(small.patterns) > 3
    assert [v for g in small.groups for v in g] == small.values == one.values
    for g in small.groups:
        assert len(g) <= small.CHUNK and (sum(map(len, g)) <= small.CHARS or len(g) == 1)
    for g, h in zip(small.groups, small.groups[1:]):
        assert len(g) == small.CHUNK or sum(map(len, g)) + len(h[0]) > small.CHARS   # as full as allowed
    arr = pa.array([f"a {LONG} b {INNER} c {OTHER} d {PREFIX}", "v0000003-xxx and v0000999-" + "x" * 49 + " end",
                    None, "plain", "w" * 40_001])
    got, want = small.mask(arr), one.mask(arr)
    assert got[0].to_pylist() == want[0].to_pylist() and got[1].to_pylist() == want[1].to_pylist()
    assert got[1].to_pylist() == [4, 2, 0, 0, 1]


def test_each_value_is_counted_in_its_own_group() -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "release"))
    import mask_finding as mf

    filler = "z" * 20_000                                   # longer than CHARS: a group of its own, first
    m = mf.Matcher([filler, LONG, OTHER, INNER], per_value=True)
    assert len(m.groups) == 2 and m.groups[0] == [filler]
    m.mask(pa.array([f"{LONG} and {OTHER}", f"{INNER} alone", None]))
    assert m.per == {LONG: 1, OTHER: 1, INNER: 2}           # INNER inside LONG too: one group, counted in both


def test_a_map_column_holding_text_is_refused(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    m = pa.array([[("k", f"v {LONG}")]], pa.map_(pa.string(), pa.string()))
    pq.write_table(pa.table({"meta": m}), str(tier / "copied" / "ds" / "m.parquet"))
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG]))
    assert run.returncode != 0 and "map column" in run.stderr + run.stdout, run.stdout + run.stderr
    assert LONG not in run.stdout + run.stderr


def test_a_copied_files_column_of_another_shape_is_nested_text(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    p = tier / "copied" / "ds" / "f.parquet"
    pq.write_table(pa.table({"files": [[f"a {LONG}", "b"], None]}), str(p))
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG]), "--write")
    assert run.returncode == 0 and "after: 0 of 1 values still occur" in run.stdout, run.stdout + run.stderr
    assert pq.read_table(p).to_pylist() == [{"files": ["a [REDACTED]", "b"]}, {"files": None}]


def test_a_value_with_a_line_break_is_masked_in_a_copied_text_file(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    two = "jdbc:postgresql://db:5432/app\nuser=me password=Zq8vN3xLw7Pk"
    f = tier / "copied" / "ds" / "notes.txt"
    f.write_text(f"see\n{two}\nend\n")
    dry = _mask(tier, _values_file(tmp_path / "v.jsonl", [two]))
    assert json.loads(dry.stdout.split("would mask: ", 1)[1].splitlines()[0])["occurrences in copied files"] == 1
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [two]), "--write")
    assert run.returncode == 0 and "after: 0 of 1 values still occur" in run.stdout, run.stdout + run.stderr
    assert f.read_text() == "see\n[REDACTED]\nend\n"


def test_masking_keeps_the_numbers_that_describe_the_text(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG, OTHER]), "--write")
    assert run.returncode == 0, run.stdout + run.stderr
    masked = [f for r in pq.read_table(tier / "data").to_pylist() for f in r["files"] or [] if "[REDACTED]" in f["content"]]
    assert masked                                                   # the test's rows carry a dummy digest otherwise
    for f in masked:
        data = f["content"].encode()
        assert f["bytes"] == len(data) and f["sha256"] == hashlib.sha256(data).hexdigest()
    rows = [r for r in pq.read_table(tier / "conversations" / "entire" / "part-000.parquet").to_pylist()
            if "[REDACTED]" in (r["content"] or "")]
    assert rows
    for r in rows:
        assert (r["word_count"], r["char_count"]) == (len(r["content"].split()), len(r["content"]))


def test_a_copied_tables_own_counts_are_left_as_copied(tmp_path: Path) -> None:
    tier = _tier(tmp_path)
    p = tier / "copied" / "ds" / "c.parquet"
    pq.write_table(pa.table({"content": [f"say {LONG}"], "word_count": [99], "char_count": [99]}), str(p))
    run = _mask(tier, _values_file(tmp_path / "v.jsonl", [LONG]), "--write")
    assert run.returncode == 0, run.stdout + run.stderr
    assert pq.read_table(p).to_pylist() == [{"content": "say [REDACTED]", "word_count": 99, "char_count": 99}]
