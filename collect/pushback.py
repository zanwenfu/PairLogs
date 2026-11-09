"""`pushback` by text: each typed row's label looked up by its text's id, so a text labelled once, in any version, is
never sent to the model again, and a version's rows can be labelled after it is built (#22: by text, not by
(session_key, turn_number), which a later build can number differently).

A text's id is the first 24 hex characters of the SHA-256 of the row's content as published, as
scripts/pushback/extract_typed.py computes it. The store is a JSON-lines file of the model's results, one line per id:
`label` 1 (pushback), 0 (not), or null when the text was refused and has no label. A later line for an id replaces an
earlier one (a retry), so new results are appended."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def text_id(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="surrogateescape")).hexdigest()[:24]


def answers(store: Path) -> dict[str, str]:
    """id -> the result's `answer` (`pushback`, `not_pushback`, or why there is no label: `content_filter`, ...)."""
    out: dict[str, str] = {}
    if store.exists():
        with open(store, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    out[r["id"]] = r.get("answer")
    return out


def load(store: Path) -> dict[str, int | None]:
    """id -> the model's label: 1, 0, or None for a text it refused. A later line for an id wins."""
    out: dict[str, int | None] = {}
    if store.exists():
        with open(store, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    out[r["id"]] = r.get("label")
    return out


def _write_like(table: pa.Table, original: Path, out: Path) -> None:
    """Written as the original was: its compression and its row groups' size."""
    md = pq.ParquetFile(original).metadata
    codec = md.row_group(0).column(0).compression.lower() if md.num_row_groups else "zstd"
    rows = max(md.row_group(i).num_rows for i in range(md.num_row_groups)) if md.num_row_groups else None
    pq.write_table(table, out, compression=codec, row_group_size=rows)


def apply(files: list[Path], labels: dict[str, int | None], *, write: bool = True) -> dict:
    """Each conversation file's `pushback` set from `labels` by its typed rows' text: true for 1, false for 0; empty
    for a text the model refused, a text with no result yet, and every row not typed. Only a file whose column
    changes is written again. With `write` false nothing is written: `differ` then counts the rows whose column
    would change. Returns the counts, `missing` (the texts with no result yet, id -> text: to send to the model) and
    `refused` (each refused text's rows, id -> rows: the labelling record counts them by the reason given)."""
    counts, missing, refused = Counter(), {}, Counter()
    for path in files:
        t = pq.read_table(path, columns=["typed_by_developer", "content", "pushback"])
        new = []
        for typed, text, old in zip(t["typed_by_developer"].to_pylist(), t["content"].to_pylist(),
                                    t["pushback"].to_pylist()):
            value = None
            if typed:
                i = text_id(text)
                if i not in labels:
                    missing.setdefault(i, text or "")
                    counts["typed, no result yet"] += 1
                elif labels[i] is None:
                    counts["typed, refused"] += 1
                    refused[i] += 1
                else:
                    value = bool(labels[i])
                    counts["pushback" if value else "not pushback"] += 1
            if value != old:
                counts["differ"] += 1
            new.append(value)
        if write and new != t["pushback"].to_pylist():
            t = pq.read_table(path)   # the whole file, written again with the column changed
            field = t.schema.field("pushback")
            t = t.set_column(t.schema.get_field_index("pushback"), field, pa.array(new, field.type))
            tmp = path.with_name(path.name + ".pushback-tmp")
            _write_like(t, path, tmp)
            os.replace(tmp, path)
            counts["files written"] += 1
    return {"counts": dict(counts), "missing": missing, "refused": dict(refused)}
