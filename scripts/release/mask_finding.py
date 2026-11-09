"""A value the release check found in a tier, masked by hand as the build masks a finding: `[REDACTED]` wherever it
occurs in the tier (the copied datasets' files, every text column of the message tables, the session tables' text
columns and their files' content), each file written beside and moved over (a hard-linked copy keeps its original's
files). The value is found again by the scan itself, in the one file named, and never printed or passed on a command
line. The sessions' `redactions.secrets` count the occurrences masked in their rows; the tier's changelog records the
counts (`masked_after_release_check`). Without --write, counts only. After writing, it checks that the value occurs
nowhere in the tier and that the named file scans clean.

python mask_finding.py TIER FILE RULE [--write]     (FILE relative to TIER; RULE the scan's rule, e.g. generic-api-key)
ERRATA_GITLEAKS, ERRATA_NOT_SECRETS, ERRATA_KNOWN_SECRETS as for the check.

python mask_finding.py TIER --values VALUES.jsonl --label LABEL [--write] [--procs N]
Many values at once, as second_scan.py --values-out wrote them (one JSON string a line, never printed), each masked
the same way (the longest first); LABEL says in the changelog what found them. After writing, it checks that no value
occurs anywhere in the tier; the tier is then scanned again by both scanners. --procs N: each pass (identifiers, masking,
the check after) over the tier's files in N worker processes. Only text is masked: the message
tables' free-text columns (as the build scans them), the session files' content and the copied datasets' files; a
value that also stands in an identifier column (a session, turn or tool-call id, a path or key in a copied table) is
left out, counted, since there it is no secret and masking it would break the tables' joins. A value that is only a
mask word (REDACTED, ours or a source's own, with fewer than 8 other characters) is refused: masking it would mask
every mask again. --per-value OUT.json (with --values): how often each value occurs where masking reaches, by its
sha256 (a value inside a longer one counted in both), to find values common enough to be no secret; mode 600.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from collect import scan

STRING = (pa.string(), pa.large_string())
FREE = {"content", "tool_input_json", "command", "file_path", "pattern"}   # conv_build.SCANNED: the messages' text
IDLIKE = re.compile(r"(^|_)(id|ids|uuid|key|pk|sha256|hash|url|path|file)$", re.I)   # a copied table's identifiers
WORD = re.compile(r"\[?redacted\]?", re.I)   # a mask word: ours, [REDACTED], or a source's own


def unsafe(value: str) -> bool:
    """Too short to mask safely: under 8 characters, holding our mask, or under 8 without its mask words."""
    return len(value) < 8 or scan.MASK in value or len(WORD.sub("", value)) < 8


def find_value(path: Path, rule: str) -> str:
    work = Path(tempfile.mkdtemp(prefix="mask-finding-"))
    try:
        fs = [f for fl in scan.find_all([path], work, 1).values() for f in fl]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if len(fs) != 1 or fs[0]["rule"] != rule:
        sys.exit(f"refused: {path.name} has {len(fs)} findings ({sorted({f['rule'] for f in fs})}), not one {rule}")
    value = fs[0]["secret"]
    if unsafe(value):
        sys.exit("refused: the value is too short to mask safely")
    return value


def replace_file(path: Path, write) -> None:
    tmp = path.with_name(path.name + ".mask-tmp")
    write(tmp)
    os.replace(tmp, path)


def write_like(table: pa.Table, original: Path, tmp: Path) -> None:
    md = pq.ParquetFile(original).metadata
    codec = md.row_group(0).column(0).compression.lower() if md.num_row_groups else "zstd"
    size = max(md.row_group(i).num_rows for i in range(md.num_row_groups)) if md.num_row_groups else None
    pq.write_table(table, tmp, compression=codec, row_group_size=size)


def has_text(t: pa.DataType) -> bool:
    """A type that holds strings, at any depth (as hfcopy_build._text_columns reads a copied table)."""
    if t in STRING:
        return True
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return has_text(t.value_type)
    if pa.types.is_struct(t):
        return any(has_text(t.field(i).type) for i in range(t.num_fields))
    if pa.types.is_map(t):
        return has_text(t.key_type) or has_text(t.item_type)
    return False


def map_text(t: pa.DataType) -> bool:
    """A map holding strings, at any depth: its entries come back as tuples, which masking does not walk."""
    if pa.types.is_map(t):
        return has_text(t)
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return map_text(t.value_type)
    if pa.types.is_struct(t):
        return any(map_text(t.field(i).type) for i in range(t.num_fields))
    return False


def refuse_maps(path: Path, schema: pa.Schema) -> None:
    bad = [f.name for f in schema if map_text(f.type)]
    if bad:
        sys.exit(f"refused: {path.name} holds text in a map column ({', '.join(bad)}), which masking cannot read")


def session_files(schema: pa.Schema) -> bool:
    """A session table's `files` (a list of structs with `content`), read by `contents`; any other `files` column is
    nested text like any other."""
    if "files" not in schema.names:
        return False
    t = schema.field("files").type
    return (pa.types.is_list(t) or pa.types.is_large_list(t)) and pa.types.is_struct(t.value_type) \
        and t.value_type.get_field_index("content") >= 0


def nested_columns(rel: Path, schema: pa.Schema, only_text: bool) -> list[str]:
    """The columns that hold strings inside lists or structs (OpenHands feedback's `trajectory`, a list of events),
    which masking reads string by string: none of a message or session table's with many values (a session table's
    `files` is read by `contents`), a copied table's but its identifiers. Missed until 2026-11-05: the 319-value run
    left 31 of its values whole in `trajectory`, and its count after the mask missed them too (TruffleHog found them)."""
    top = rel.parts[0] if rel.parts else ""
    if only_text and top in ("conversations", "conversations_subagents", "data"):
        return []
    files = session_files(schema)
    return [f.name for f in schema if f.type not in STRING and not (files and f.name == "files") and has_text(f.type)
            and not (only_text and IDLIKE.search(f.name))]


def nested_strings(x, keep, name: str | None = None):
    """The strings inside a nested value (a row's cell as to_pylist gives it) whose field name `keep` accepts, in
    order; a list's items carry their list's name."""
    if isinstance(x, str):
        if keep(name):
            yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from nested_strings(v, keep, k)
    elif isinstance(x, list):
        for v in x:
            yield from nested_strings(v, keep, name)


def put_strings(x, new, keep, name: str | None = None):
    """The nested value with the strings nested_strings gives replaced, in order, by those `new` yields."""
    if isinstance(x, str):
        return next(new) if keep(name) else x
    if isinstance(x, dict):
        return {k: put_strings(v, new, keep, k) for k, v in x.items()}
    if isinstance(x, list):
        return [put_strings(v, new, keep, name) for v in x]
    return x


def masked_name(only_text: bool):
    """Which nested field names masking changes: all (one finding), or all but an identifier's (many values)."""
    return lambda name: not (only_text and name and IDLIKE.search(name))


def text_columns(rel: Path, schema: pa.Schema, only_text: bool) -> set:
    """The string columns masking may change: all of them (one finding), or only text (many values): the message
    tables' free text, none of a session table's (its text is in `files`), a copied table's but its identifiers."""
    names = {f.name for f in schema if f.type in STRING}
    if not only_text:
        return names
    top = rel.parts[0] if rel.parts else ""   # rel: the file's path within the tier
    if top in ("conversations", "conversations_subagents"):
        return names & FREE
    if top == "data":
        return set()
    return {n for n in names if not IDLIKE.search(n)}


class Matcher:
    """The values as RE2 alternations of quoted literals (the longest first, a few hundred to a pattern), so a column
    is counted and masked in one pass per pattern, not one per value: Arrow's regex kernels on text and on bytes. A
    pattern holds at most CHUNK values and CHARS characters of them: 400 of the longest values (60 to 591 characters)
    in one pattern ran 16 times slower than in groups of 50 (measured 11-05 on tier A), the 36-character ones not.
    A limit, stated: within one pattern a match starts at the leftmost place any value starts, so a value that overlaps
    the start of a longer one in the same pattern is masked from its own start and leaves the longer one's tail; the
    scan after the mask is what finds such tails (11-05: 18 strings first seen after masking, none a key)."""

    CHUNK = 400
    CHARS = 15_000

    def __init__(self, values: list[str], per_value: bool = False):
        if any("\\E" in v for v in values):
            sys.exit("refused: a value holds \\E, which ends a quoted literal")
        self.values = sorted(values, key=lambda v: (-len(v), v))
        self.groups: list[list[str]] = []
        size = 0
        for v in self.values:      # in order, so the longer values are always masked first
            if not self.groups or len(self.groups[-1]) >= self.CHUNK or size + len(v) > self.CHARS:
                self.groups.append([])
                size = 0
            self.groups[-1].append(v)
            size += len(v)
        self.patterns = ["|".join("\\Q" + v + "\\E" for v in g) for g in self.groups]
        self.per = Counter() if per_value else None   # each value's occurrences, when asked
        # a value holding a line break, matched in a copied text file's whole bytes (its lines cannot hold it)
        self.multiline = [(v, v.encode("utf-8", "surrogatepass")) for v in self.values if "\n" in v]

    def mask_bytes(self, raw: bytes) -> tuple[bytes, int]:
        """A copied text file's bytes with the line-break values masked, the longest first, and how many."""
        n = 0
        for v, b in self.multiline:
            k = raw.count(b)
            if k:
                n += k
                raw = raw.replace(b, scan.MASK.encode())
                if self.per is not None:
                    self.per[v] += k
        return raw, n

    def which_bytes(self, raw: bytes) -> set:
        return {v for v, b in self.multiline if b in raw}

    def mask(self, arr):
        """(the array with every value masked, the occurrences masked per row)."""
        counts = None
        for i, pat in enumerate(self.patterns):
            c = pc.fill_null(pc.count_substring_regex(arr, pat), 0)
            if pc.sum(c).as_py():
                if self.per is not None:
                    hit = arr.filter(pc.greater(c, 0))
                    for v in self.groups[i]:
                        n = pc.sum(pc.count_substring(hit, v)).as_py()
                        if n:
                            self.per[v] += n
                arr = pc.replace_substring_regex(arr, pat, scan.MASK)
            counts = c if counts is None else pc.add(counts, c)
        return arr, counts

    def which(self, arr) -> set:
        """The values that occur in the array."""
        hit = None
        for pat in self.patterns:
            h = pc.fill_null(pc.match_substring_regex(arr, pat), False)
            hit = h if hit is None else pc.or_(hit, h)
        rows = [x for x in arr.filter(hit).to_pylist() if x is not None] if hit is not None else []
        if rows and isinstance(rows[0], bytes):
            rows = [x.decode("utf-8", "replace") for x in rows]
        return {v for v in self.values if any(v in x for x in rows)}


def identifiers(tier: Path, m: Matcher, p: Path) -> set:
    """The values that stand in a column masking leaves alone (an identifier) in one of the tier's tables."""
    t = pq.read_table(p)
    refuse_maps(p, t.schema)
    keep = text_columns(p.relative_to(tier), t.schema, True)
    found = set()
    for f in t.schema:
        if f.type in STRING and f.name not in keep:
            found |= m.which(t[f.name])
    for name in nested_columns(p.relative_to(tier), t.schema, True):   # a nested field under an identifier's name
        ids = [s for r in t[name].to_pylist() for s in nested_strings(r, lambda n: bool(n and IDLIKE.search(n)))]
        if ids:
            found |= m.which(pa.array(ids, pa.large_string()))
    return found


def contents(t: pa.Table) -> tuple[list, list]:
    """The `files` list's contents, flat, with where each came from (row, index in the row's list)."""
    where, text = [], []
    for r, fl in enumerate(t["files"].to_pylist()):
        for j, f in enumerate(fl or []):
            where.append((r, j))
            text.append(f.get("content"))
    return where, text


def mask_table(path: Path, m: Matcher, write: bool, per_session: Counter, only_text: bool = False,
               tier: Path | None = None) -> int:
    """The string columns `text_columns` names, and the `files` list's `content`; returns the occurrences masked (the
    values the longest first, so a value inside another is counted where it stands alone)."""
    t = pq.read_table(path)
    refuse_maps(path, t.schema)
    total, cols = 0, {}
    keys = t["session_key"].to_pylist() if "session_key" in t.schema.names else None
    allowed = text_columns(path.relative_to(tier) if tier else Path(path.name), t.schema, only_text)
    for i, field in enumerate(t.schema):
        if field.type in STRING and field.name in allowed:
            col, counts = m.mask(t[field.name])
            n = pc.sum(counts).as_py() or 0
            if n:
                total += n
                cols[i] = col
                if keys is not None:
                    for k, c in zip(keys, counts.to_pylist()):
                        per_session[k] += c
                top = (path.relative_to(tier) if tier else Path(path.name)).parts[0]
                if field.name == "content" and top in ("conversations", "conversations_subagents") \
                        and {"word_count", "char_count"} <= set(t.schema.names):   # never a copied table's own
                    # a masked row's counts, as the build sets them (conv_build: len(text.split()), len(text))
                    wc, cc = t["word_count"].to_pylist(), t["char_count"].to_pylist()
                    for r, (c, x) in enumerate(zip(counts.to_pylist(), col.to_pylist())):
                        if c:
                            wc[r], cc[r] = len((x or "").split()), len(x or "")
                    for name, v in (("word_count", wc), ("char_count", cc)):
                        j = t.schema.get_field_index(name)
                        cols[j] = pa.array(v, t.schema.field(j).type)
    keep = masked_name(only_text)
    for name in nested_columns(path.relative_to(tier) if tier else Path(path.name), t.schema, only_text):
        rows = t[name].to_pylist()
        owner = [r for r, x in enumerate(rows) for _ in nested_strings(x, keep)]
        if not owner:
            continue
        new, counts = m.mask(pa.array([s for x in rows for s in nested_strings(x, keep)], pa.large_string()))
        n = pc.sum(counts).as_py() or 0
        if n:
            total += n
            it = iter(new.to_pylist())
            cols[t.schema.get_field_index(name)] = pa.array([put_strings(x, it, keep) for x in rows],
                                                            t.schema.field(name).type)
            if keys is not None:
                for r, c in zip(owner, counts.to_pylist()):
                    per_session[keys[r]] += c
    if session_files(t.schema):
        where, text = contents(t)
        if where:
            new, counts = m.mask(pa.array(text, pa.large_string()))
            counts = counts.to_pylist()
            if sum(counts):
                files = t["files"].to_pylist()
                new = new.to_pylist()
                for (r, j), c, x in zip(where, counts, new):
                    if c:
                        data = x.encode("utf-8", "surrogatepass")   # the file's size and digest as published
                        files[r][j].update(content=x, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                        total += c
                        if keys is not None:
                            per_session[keys[r]] += c
                cols[t.schema.get_field_index("files")] = pa.array(files, t.schema.field("files").type)
    if total and write:
        for i, col in cols.items():
            t = t.set_column(i, t.schema.field(i), col)
        replace_file(path, lambda tmp: write_like(t, path, tmp))
    return total


def set_redactions(path: Path, per_session: Counter, write: bool) -> int:
    t = pq.read_table(path)
    if "redactions" not in t.schema.names:
        return 0
    keys = t["session_key"].to_pylist()
    if not any(per_session.get(k) for k in keys):
        return 0
    red = t["redactions"].to_pylist()
    n = 0
    for i, k in enumerate(keys):
        if per_session.get(k):
            red[i] = {**(red[i] or {}), "secrets": ((red[i] or {}).get("secrets") or 0) + per_session[k]}
            n += 1
    if write:
        j = t.schema.get_field_index("redactions")
        t = t.set_column(j, t.schema.field(j), pa.array(red, t.schema.field(j).type))
        replace_file(path, lambda tmp: write_like(t, path, tmp))
    return n


def left_in(m: Matcher, p: Path) -> tuple[int, set]:
    """The values' occurrences in one file of the tier, and which values occur."""
    if p.suffix == ".parquet":
        t = pq.read_table(p)
        refuse_maps(p, t.schema)
        arrays = [t[f.name] for f in t.schema if f.type in STRING]
        files = session_files(t.schema)
        if files:
            arrays.append(pa.array(contents(t)[1], pa.large_string()))
        for f in t.schema:   # every string inside a list or struct, whatever its name
            if f.type not in STRING and not (files and f.name == "files") and has_text(f.type):
                arrays.append(pa.array([s for x in t[f.name].to_pylist() for s in nested_strings(x, lambda n: True)],
                                       pa.large_string()))
        total, left = 0, set()
    else:
        raw = p.read_bytes()
        arrays = [lines(raw)]
        left = m.which_bytes(raw)
        total = sum(raw.count(b) for _, b in m.multiline)
    for arr in arrays:
        n = pc.sum(m.mask(arr)[1]).as_py() or 0
        if n:
            total += n
            left |= m.which(arr)
    return total, left


W = {}   # each worker's matcher and settings, set by _init


def lines(raw: bytes):
    """A file's bytes as an array of its lines (no value spans a line, and a single array value is kept far below the
    regex engine's 2 GB limit); b"\\n".join of the lines gives the bytes back exactly."""
    return pa.array(raw.split(b"\n"), pa.large_binary())


def _init(values: list[str], tier: str, only_text: bool, write: bool, per_value: bool = False) -> None:
    W.update(m=Matcher(values, per_value), tier=Path(tier), only_text=only_text, write=write)


def _ident(p: str) -> set:
    return identifiers(W["tier"], W["m"], Path(p))


def _left(p: str) -> tuple[int, set]:
    return left_in(W["m"], Path(p))


def _mask(job: tuple[str, str]) -> tuple[str, int, dict, dict]:
    kind, p = job
    p = Path(p)
    per_session = Counter()
    if kind == "copied file":
        raw, c0 = W["m"].mask_bytes(p.read_bytes())
        new, counts = W["m"].mask(lines(raw))
        c = c0 + (pc.sum(counts).as_py() or 0)
        if c and W["write"]:
            raw = b"\n".join(new.to_pylist())
            replace_file(p, lambda tmp: tmp.write_bytes(raw))
    else:
        c = mask_table(p, W["m"], W["write"], per_session, W["only_text"], W["tier"])
    per = W["m"].per
    got = dict(per) if per is not None else {}
    if per is not None:
        per.clear()
    return kind, c, dict(per_session), got


def run(fn, items: list, values: list[str], tier: Path, only_text: bool, write: bool, procs: int,
        per_value: bool = False) -> list:
    """fn over items, in this process (procs 1) or in a pool of fresh workers, each with its own matcher."""
    size = lambda x: os.path.getsize(x if isinstance(x, str) else x[1])
    items = sorted(items, key=size, reverse=True)   # the largest first: no straggler at the end
    if procs <= 1:
        _init(values, str(tier), only_text, write, per_value)
        return [fn(x) for x in items]
    ctx = multiprocessing.get_context("forkserver")
    with ProcessPoolExecutor(procs, mp_context=ctx, initializer=_init,
                             initargs=(values, str(tier), only_text, write, per_value)) as ex:
        return list(ex.map(fn, items, chunksize=1))


def read_values(path: Path) -> list[str]:
    values = sorted({json.loads(l) for l in open(path, encoding="utf-8") if l.strip()}, key=lambda v: (-len(v), v))
    if not values:
        sys.exit("refused: no values")
    if any(unsafe(v) for v in values):
        sys.exit(f"refused: {sum(1 for v in values if unsafe(v))} values too short to mask safely (under 8 characters, "
                 "or a mask word)")
    return values


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", type=Path)
    ap.add_argument("file", nargs="?")
    ap.add_argument("rule", nargs="?")
    ap.add_argument("--values", type=Path)
    ap.add_argument("--label")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument("--per-value", type=Path)
    a = ap.parse_args()
    if (a.values is None) == (a.file is None) or (a.file is None) != (a.rule is None) or bool(a.values) != bool(a.label):
        ap.error("either FILE RULE, or --values VALUES --label LABEL")
    if a.per_value and not a.values:
        ap.error("--per-value goes with --values")
    tier, rel, rule, write = a.tier, a.file, a.rule or a.label, a.write
    values = read_values(a.values) if a.values else [find_value(tier / rel, rule)]
    got = Counter()
    only_text = bool(a.values)
    tables = [str(p) for p in sorted(tier.rglob("*.parquet"))]
    if only_text:
        ids = set().union(*run(_ident, tables, values, tier, True, False, a.procs))
        got["values left out: they stand in an identifier column"] = len(ids)
        values = [v for v in values if v not in ids]
        if not values:
            print("nothing to mask: " + json.dumps(dict(got)))
            return 0
    for p in tables:   # a map column holding text is refused before anything is written
        refuse_maps(Path(p), pq.read_schema(p))
    m = Matcher(values)
    jobs = [("copied file" if p.suffix != ".parquet" else "copied table", str(p))
            for p in sorted((tier / "copied").rglob("*")) if p.is_file()]
    jobs += [("message table", str(p)) for sub in ("conversations", "conversations_subagents")
             for p in sorted((tier / sub).rglob("*.parquet"))]
    jobs += [("session table", str(p)) for p in sorted((tier / "data").rglob("*.parquet"))]
    per_session, per_value = Counter(), Counter()
    for kind, c, ps, pv in run(_mask, jobs, values, tier, only_text, write, a.procs, bool(a.per_value)):
        per_session.update(ps)
        per_value.update(pv)
        if kind in ("copied file", "copied table"):
            got["copied files" if kind == "copied file" else "copied Parquet files"] += bool(c)
            got["occurrences in copied files"] += c
        elif kind == "message table":
            got["message files"] += bool(c)
            got["occurrences in message rows"] += c
        else:
            got["session files"] += bool(c)
            got["occurrences in session rows"] += c
    got["sessions"] = sum(1 for v in per_session.values() if v)
    for p in sorted((tier / "data").rglob("*.parquet")):
        got["sessions' redactions counted"] += set_redactions(p, per_session, write)
    if a.values:
        got["values"] = len(values)
    print(("masked: " if write else "would mask: ") + json.dumps(dict(got)))
    if a.per_value:
        sha = lambda v: hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest()
        rows = sorted(({"sha256": sha(v), "occurrences": per_value[v]} for v in values), key=lambda r: -r["occurrences"])
        fd = os.open(a.per_value, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"values": len(values), "by value": rows}, f)
        bands = Counter("0" if n == 0 else "1" if n == 1 else "2-10" if n <= 10 else "11-100" if n <= 100
                        else "101-1000" if n <= 1000 else "over 1000" for n in (per_value[v] for v in values))
        print("values by occurrences: " + json.dumps({k: bands[k] for k in ("0", "1", "2-10", "11-100", "101-1000",
                                                                            "over 1000")}))
    if not write:
        return 0
    log = tier / "changelog.json"
    c = json.loads(log.read_text())
    c.setdefault("masked_after_release_check", []).append({
        "rule": rule, "found_in": rel if rel else "the files second_scan.py named",
        "how": ("the release check over the tier as published found it" if rel else
                "a second scanner over the tier as published found them") +
               "; masked as the build masks a finding, [REDACTED] wherever it occurs in the tier",
        **dict(got), "masked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    replace_file(log, lambda tmp: tmp.write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n"))
    every = [str(p) for p in sorted(tier.rglob("*")) if p.is_file()]
    found = run(_left, every, values, tier, only_text, False, a.procs)
    times, which = sum(t for t, _ in found), set().union(*(w for _, w in found))
    if a.values:
        print(f"after: {len(which)} of {len(values)} values still occur in the tier ({times} times)")
        return 0 if not times else 1
    left = times
    work = Path(tempfile.mkdtemp(prefix="mask-finding-"))
    try:
        again = sum(len(fl) for fl in scan.find_all([tier / rel], work, 1).values())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"after: the value occurs {left} times in the tier; {rel} scans with {again} findings")
    return 0 if left == 0 and again == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
