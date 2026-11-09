"""A copied Parquet file rebuilt after masking: the masked strings change, nothing else does.

    python -m pytest tests/test_copied_rebuild.py     (needs pyarrow; run where the collector runs)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import hfcopy_build as hb  # noqa: E402

SCHEMA = pa.schema([
    ("id", pa.int64()),
    ("text", pa.string()),
    ("big", pa.large_string()),
    ("turns", pa.list_(pa.struct([("role", pa.string()), ("content", pa.string()), ("at", pa.timestamp("ns"))]))),
    ("tags", pa.map_(pa.string(), pa.string())),
    ("at", pa.timestamp("ns", tz="UTC")),
    ("score", pa.float64()),
])
TOKEN = "ghp_" + "A1b2C3d4" * 4 + "ZZZZ"


def _rows(n: int) -> list[dict]:
    out = []
    for i in range(n):
        ns = 1_700_000_000_123_456_789 + i  # nanoseconds a round trip through Python's datetime would lose
        out.append({
            "id": i, "text": f"row {i} says hello", "big": "x" * 50 + (f" key {TOKEN} " if i == 21 else " ") + "y",
            "turns": [{"role": "user", "content": f"ask {i}" + (f" {TOKEN}" if i == 3 else ""), "at": ns},
                      {"role": "assistant", "content": "ok", "at": ns + 1}],
            "tags": [("k", f"v{i}"), ("token", TOKEN if i == 40 else "none")],
            "at": ns, "score": i / 3,
        })
    return out


def _write(path: Path, rows: list[dict]) -> None:
    table = pa.Table.from_pylist([{**r, "turns": r["turns"]} for r in rows], schema=SCHEMA)
    # Times as integers into the arrays, so they keep their nanoseconds.
    at = pa.array([r["at"] for r in rows], type=pa.int64()).cast(pa.timestamp("ns", tz="UTC"))
    table = table.set_column(SCHEMA.get_field_index("at"), SCHEMA.field("at"), at)
    pq.write_table(table, str(path), row_group_size=17)


def _view(src: Path, prefix: Path) -> list[Path]:
    """The view's parts, as the build stages them."""
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    return [Path(p) for p in hb._write_view((str(src), cols, str(prefix)))[0]]


def _view_and_mask(src: Path, prefix: Path) -> tuple[int, list[Path]]:
    parts, n = _view(src, prefix), 0
    for part in parts:
        text = part.read_text()
        n += text.count(TOKEN)
        part.write_text(text.replace(TOKEN, "[REDACTED]"))
    return n, parts


def test_masked_cells_change_and_nothing_else(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hb, "VIEW_LINES", 32)   # several parts from a small file, as a large one makes them
    src, dest = tmp_path / "src.parquet", tmp_path / "out.parquet"
    rows = _rows(60)
    _write(src, rows)
    n, parts = _view_and_mask(src, tmp_path / "v")
    assert n == 3 and len(parts) > 1  # 60 rows x 4 text columns: several parts
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    changed = hb._rebuild_parquet(str(src), cols, parts, dest)
    assert changed == 3
    a, b = pq.read_table(src), pq.read_table(dest)
    assert a.schema.equals(b.schema, check_metadata=True)
    assert a.num_rows == b.num_rows == 60
    # Every column but the three masked cells' columns is exactly the same array.
    for name in ("id", "text", "at", "score"):
        assert a.column(name).equals(b.column(name)), name
    # Nanoseconds kept inside the rebuilt struct column too.
    at_a = pa.compute.list_flatten(a.column("turns")).combine_chunks().field("at")
    at_b = pa.compute.list_flatten(b.column("turns")).combine_chunks().field("at")
    assert at_a.equals(at_b)
    plain = lambda t: pa.table({n: hb._as_plain(t.column(n)) for n in t.column_names})  # noqa: E731
    got = plain(b).to_pylist()
    assert TOKEN not in json.dumps(got, default=str)
    assert "[REDACTED]" in got[21]["big"] and got[21]["big"].startswith("x" * 50)
    assert got[3]["turns"][0]["content"] == "ask 3 [REDACTED]"
    assert dict(got[40]["tags"])["token"] == "[REDACTED]"
    # Unmasked rows of rebuilt columns read back equal.
    want = plain(a).to_pylist()
    for i in range(60):
        if i not in (3, 21, 40):
            assert got[i] == want[i], i


def test_unchanged_file_is_linked_not_copied(tmp_path: Path) -> None:
    src, dest = tmp_path / "src.parquet", tmp_path / "out.parquet"
    rows = [r for r in _rows(60) if r["id"] not in (3, 21, 40)]
    _write(src, rows)
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    assert hb._rebuild_parquet(str(src), cols, _view(src, tmp_path / "v"), dest) == 0
    assert os.stat(src).st_ino == os.stat(dest).st_ino


def test_a_masked_line_that_breaks_its_json_stops_the_build(tmp_path: Path) -> None:
    src, dest = tmp_path / "src.parquet", tmp_path / "out.parquet"
    _write(src, _rows(30))
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    parts = _view(src, tmp_path / "v")
    lines = parts[0].read_text().split("\n")
    lines[5] = lines[5][:-3]  # cut: no longer JSON
    parts[0].write_text("\n".join(lines))
    try:
        hb._rebuild_parquet(str(src), cols, parts, dest)
    except RuntimeError as e:
        assert "no longer reads back" in str(e)
    else:
        raise AssertionError("a broken line was accepted")


def test_unsupported_types_are_named() -> None:
    assert hb._supported(SCHEMA.field("turns").type) and hb._supported(SCHEMA.field("tags").type)
    for t in (pa.binary(), pa.dictionary(pa.int32(), pa.string()), pa.list_(pa.large_binary())):
        assert not hb._supported(t), t


def test_views_in_parts_rebuild_and_keys_masked_as_staged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hb, "VIEW_LINES", 32)   # several parts from a small file, as a large one makes them
    src, dest = tmp_path / "src.parquet", tmp_path / "out.parquet"
    rows = _rows(60)
    rows[50]["big"] = "cat key.pem\n-----BEGIN RSA PRIVATE KEY-----\n" + "Ab" * 40 + "\n-----END RSA PRIVATE KEY-----"
    _write(src, rows)
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    parts, pem, starts, total = hb._write_view((str(src), cols, str(tmp_path / "v")))
    assert len(parts) == -(-60 * len(cols) // hb.VIEW_LINES) > 1 and pem == {"private-key": 1}, (len(parts), pem)
    assert [len(p) for p in hb.view_parts(str(src), cols)] == [sum(1 for _ in open(p)) for p in parts]
    changed = hb._rebuild_parquet(str(src), cols, [Path(p) for p in parts], dest)
    got = pq.read_table(dest).column("big").to_pylist()
    assert changed == 1 and got[50] == "cat key.pem\n[REDACTED]" and got[49] == rows[49]["big"], got[50]


def test_a_large_json_lines_file_is_staged_a_line_at_a_time(tmp_path: Path) -> None:
    key = ("-----BEGIN RSA PRIVATE KEY-----\\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\\n"
           "-----END RSA PRIVATE KEY-----")
    src = tmp_path / "conversations.jsonl"
    src.write_text(json.dumps({"messages": [{"role": "user", "content": "plain"}]}) + "\n"
                   + json.dumps({"messages": [{"role": "user", "content": "my key " + key}]}) + "\n")
    staged = tmp_path / "staged.jsonl.txt"
    ok, replaced, keys = hb._stage_lines(src, staged)
    text = staged.read_text()
    assert (ok, replaced, keys) == (True, 0, {"private-key": 1})
    assert "MIIEowIBAAKCAQEAu1SU1" not in text and text.count("\n") == 2 and '"plain"' in text
    assert all(json.loads(line) for line in text.splitlines())   # every record still reads
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b'{"a": 1}\n' + bytes(range(0, 256)) * 50 + b"\n")
    assert hb._stage_lines(bad, tmp_path / "bad.txt")[0] is False and not (tmp_path / "bad.txt.lines").exists()


def test_a_file_whose_masking_cannot_keep_its_json_is_left_out_with_why(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    from collect import build, scan

    raw = tmp_path / "full.jsonl"
    raw.write_text('{"a": "x"}\n')
    def broken(*a, **k):
        raise RuntimeError("redact: masking broke JSON line 1 (Invalid \\escape)")
    monkeypatch.setattr(scan, "redact", broken)
    staged, counts, utf8 = build._stage((str(raw), str(tmp_path / "s.jsonl.txt"), True,
                                         hashlib.sha256(raw.read_bytes()).hexdigest()))
    assert utf8 is False and counts["left_out"].startswith("masking it: redact: masking broke JSON")
    assert not (tmp_path / "s.jsonl.txt").exists()


def test_a_part_ends_before_a_line_past_its_size_and_the_check_reads_the_builds_parts(tmp_path: Path,
                                                                                      monkeypatch) -> None:
    """SWE-chat's commits table holds cells of 100 to 200 MB: a part of 4,096 lines took a worker to 50 GB. A part
    now ends before a line that would take it past VIEW_CHARS, a longer line is a part alone, and the check splits
    the published file at the lines the build's parts began, though its masked cells are shorter."""
    monkeypatch.setattr(hb, "VIEW_LINES", 32)
    monkeypatch.setattr(hb, "VIEW_CHARS", 2000)
    rows = _rows(60)
    for i in (10, 11, 30):
        rows[i]["big"] = "z" * 3000   # a cell past the size: a part alone
    src = tmp_path / "src.parquet"
    _write(src, rows)
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    got, _, starts, total = hb._write_view((str(src), cols, str(tmp_path / "v")))
    sizes = [sum(1 for _ in open(p)) for p in got]
    assert total == sum(sizes) == 60 * len(cols)
    assert starts == [sum(sizes[:k]) for k in range(len(sizes))] and sum(sizes) == 60 * len(cols)
    alone = [k for k, p in enumerate(got) if any(len(x) > 2000 for x in open(p).read().splitlines())]
    assert len(alone) == 3 and all(sizes[k] == 1 for k in alone), sizes
    assert all(sum(len(x) + 1 for x in open(got[k]).read().splitlines()) <= 2001 for k in range(len(got)) if k not in alone)
    # A size where masking moves a cut: the published file read without the build's starts splits elsewhere.
    for chars in range(200, 2000, 3):
        monkeypatch.setattr(hb, "VIEW_CHARS", chars)
        got, _, starts, total = hb._write_view((str(src), cols, str(tmp_path / f"w{chars}")))
        paths = [Path(p) for p in got]
        sizes = [sum(1 for _ in open(p)) for p in paths]
        lines = [x for p in paths for x in p.read_text().splitlines()]
        for part in paths:   # masked as the scan masks: the token's cells grow shorter
            part.write_text(part.read_text().replace(TOKEN, "[REDACTED]"))
        dest = tmp_path / f"out{chars}.parquet"
        assert hb._rebuild_parquet(str(src), cols, paths, dest) == 3
        if [len(p) for p in hb.view_parts(str(dest), cols)] != sizes:
            break
    else:
        raise AssertionError("no size where masking moves a cut")
    assert [len(p) for p in hb.view_parts(str(dest), cols, starts, total)] == sizes
    assert [x for p in hb.view_parts(str(dest), cols, starts) for x in p] == [x.replace(TOKEN, "[REDACTED]") for x in lines]


def test_rows_are_read_fewer_at_a_time_where_they_are_large(tmp_path: Path, monkeypatch) -> None:
    rows = _rows(40)
    src = tmp_path / "src.parquet"
    _write(src, rows)
    assert hb._batch_rows(pq.ParquetFile(src)) == hb.BATCH
    for r in rows:
        r["big"] = os.urandom(100_000).hex()   # rows of about 200 KB, each its own (a dictionary would hold one)
    mid = tmp_path / "mid.parquet"
    _write(mid, rows)
    assert 1 < hb._batch_rows(pq.ParquetFile(mid)) < hb.BATCH
    monkeypatch.setattr(hb, "BATCH_BYTES", 1)
    assert hb._batch_rows(pq.ParquetFile(src)) == 1


def test_a_published_view_whose_line_count_is_not_the_builds_stops_the_check(tmp_path: Path, monkeypatch) -> None:
    """The partner's review: with recorded starts, a published file with more lines than the build saw would make
    its last part the whole rest of the file. It stops the check instead, before the part grows."""
    monkeypatch.setattr(hb, "VIEW_LINES", 32)
    src = tmp_path / "src.parquet"
    _write(src, _rows(20))
    cols = hb._text_columns(pq.ParquetFile(src).schema_arrow)
    got, _, starts, total = hb._write_view((str(src), cols, str(tmp_path / "v")))
    assert [len(p) for p in hb.view_parts(str(src), cols, starts, total)] == [sum(1 for _ in open(p)) for p in got]
    more = tmp_path / "more.parquet"
    _write(more, _rows(60))
    seen = []
    try:
        for part in hb.view_parts(str(more), cols, starts, total):
            seen.append(len(part))
        raise AssertionError("no refusal")
    except RuntimeError as e:
        assert "more lines" in str(e) and sum(seen) <= total, (e, seen)
    fewer = tmp_path / "fewer.parquet"
    _write(fewer, _rows(5))
    try:
        list(hb.view_parts(str(fewer), cols, starts, total))
        raise AssertionError("no refusal")
    except RuntimeError as e:
        assert "the build's" in str(e)
    try:
        list(hb.view_parts(str(src), cols, [], 0))   # an empty view recorded, a file with lines published
        raise AssertionError("no refusal")
    except RuntimeError as e:
        assert "more lines" in str(e)
