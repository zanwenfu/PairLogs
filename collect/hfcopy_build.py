"""Copied datasets in a version: their files as they are, with every secret span the scan finds redacted and nothing
else changed, and their exact matches with the scraped subsets.

Only secrets are redacted in copied data, not emails or home-path names: each copied dataset is already published
under its own publisher's privacy handling (SWE-chat applied Presidio and TruffleHog; DataClaw redacts user names
and emails; OpenHands' contributors consented), and its card here must be able to say "N secret spans redacted,
nothing else changed".

A text file is staged as it is and scanned until clean like every other published file. A Parquet file is staged
as a text view, one line per row and text-bearing column, the cell as JSON; once the view is clean, every cell whose
line changed is rebuilt from the original cell, its strings taken from the masked line and every other value kept as
it was, so the file keeps its exact schema and values. A masked line that is no longer the cell's JSON stops the
build.
"""

from __future__ import annotations

import bisect
import itertools
import json
import os
import shutil
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config
from .ledger import Ledger
from .util import safe_name

COPIED = ("swe-chat-v2", "dataclaw", "openhands-feedback")
COPIED_PII = False  # also mask emails and home-path names in copied datasets (a design decision of 2026-10)
BATCH = 16  # rows per Parquet batch: a cell can hold a whole session, megabytes, and files are read side by side
# And fewer, down to one, where a row group's rows average more than BATCH_BYTES / BATCH: a row group from the end
# of SWE-chat's commits table, 50 rows, reads as 20 GB of Python objects. An average can hide a large row among
# small ones, so the bound is low (the partner's review).
BATCH_BYTES = 1 << 20
COPY_PROCS = 8  # copied Parquet files read at once: each holds a batch of rows in memory
# A view is staged in parts of this many lines (cells), and the check splits it the same way. 32 made SWE-chat's
# 11.6-million-row conversations table over 4 million files in one folder, past what ext4's index holds (the first
# full build stopped: "No space left on device", 2026-10-04); 4,096 makes some 34,000 of about 600 KB.
VIEW_LINES = 4096
# A part also ends before a line that would take it past this many characters, and a longer line is a part alone:
# SWE-chat's commits table holds cells of 100 to 200 MB, and 4,096 of its lines held in one part took a worker to
# 50 GB (the full-scale preflight, killed for memory, 2026-10-04).
VIEW_CHARS = 64 << 20
GROUP_BYTES = 64 << 20  # a rebuilt Parquet file's row groups, about this size (one per batch made 16-row groups)
MATCH_SCHEMA = pa.schema([("dataset", pa.string()), ("file", pa.string()), ("row", pa.int64()),
                          ("session_key", pa.string()), ("matched_row", pa.string())])
PROJECT_AGENT = {"codex": "codex", "claude": "claude-code", "claude-code": "claude-code", "cursor": "cursor",
                 "gemini": "gemini-cli", "gemini-cli": "gemini-cli", "opencode": "opencode", "pi": "pi"}


def entries(ledger: Ledger):
    for name in COPIED:
        for key, entry in sorted(ledger.state_keys(name, "dataset:").items()):
            if isinstance(entry, dict):
                yield name, entry


def _text_columns(schema: pa.Schema) -> list[str]:
    def has_text(t: pa.DataType) -> bool:
        if pa.types.is_string(t) or pa.types.is_large_string(t):
            return True
        if pa.types.is_list(t) or pa.types.is_large_list(t):
            return has_text(t.value_type)
        if pa.types.is_struct(t):
            return any(has_text(t.field(i).type) for i in range(t.num_fields))
        if pa.types.is_map(t):
            return has_text(t.key_type) or has_text(t.item_type)
        return False
    return [f.name for f in schema if has_text(f.type)]


def _plain(t: pa.DataType) -> pa.DataType:
    """The type with every time-like value as the integer it is stored as. Read into Python, a nanosecond time
    raises (pyarrow without pandas: "not safely convertible to microseconds") or loses its last digits; as an
    integer it reads back exactly, and casts back exactly."""
    if pa.types.is_timestamp(t) or pa.types.is_duration(t) or pa.types.is_date64(t) or pa.types.is_time64(t):
        return pa.int64()
    if pa.types.is_date32(t) or pa.types.is_time32(t):
        return pa.int32()
    if pa.types.is_list(t):
        return pa.list_(pa.field(t.value_field.name, _plain(t.value_type), t.value_field.nullable))
    if pa.types.is_large_list(t):
        return pa.large_list(pa.field(t.value_field.name, _plain(t.value_type), t.value_field.nullable))
    if pa.types.is_struct(t):
        return pa.struct([pa.field(t.field(i).name, _plain(t.field(i).type), t.field(i).nullable)
                          for i in range(t.num_fields)])
    if pa.types.is_map(t):
        return pa.map_(_plain(t.key_type), _plain(t.item_type))
    return t


def _as_plain(column):
    t = _plain(column.type)
    return column if t.equals(column.type) else column.cast(t)


def _batch_rows(pf: pq.ParquetFile) -> int:
    """Rows to read at a time: BATCH, or fewer where a row group's rows average more than BATCH_BYTES / BATCH."""
    md = pf.metadata
    per_row = max((md.row_group(i).total_byte_size / max(1, md.row_group(i).num_rows) for i in range(md.num_row_groups)),
                  default=0)
    return max(1, min(BATCH, int(BATCH_BYTES // max(per_row, 1))))


def _view_lines(path: str, cols: list[str]):
    """The view's lines, in order: for each row, for each text-bearing column, the cell as JSON."""
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(columns=cols, batch_size=_batch_rows(pf)):
        values = [_as_plain(batch.column(c)).to_pylist() for c in cols]
        for row in zip(*values):
            for v in row:
                yield json.dumps(v, ensure_ascii=False, default=str)


def view_span(src: str, cols: list[str], starts: list[int], lines: int, a: int, b: int):
    """Parts a to b - 1 of the view view_parts reads at `starts`, the same lines, read from the row group holding the
    first: a line is a row's cell (len(cols) lines a row, _view_lines), so a part's first line names its row. Lets the
    check share one large file's parts among its workers (SWE-chat's 9.4 GB commits table, 1,128 parts, held one
    worker over five hours, 2026-10-05). Each span must give exactly its lines, or the check stops."""
    k = len(cols)
    first, end = starts[a], (starts[b] if b < len(starts) else lines)
    pf = pq.ParquetFile(src)
    md = pf.metadata
    row, top = first // k, -(-end // k)
    groups, at = [], 0
    for g in range(md.num_row_groups):   # the row groups holding rows row .. top - 1
        n = md.row_group(g).num_rows
        if at + n > row and at < top:
            groups.append(g)
        at += n
    skip_rows = row - sum(md.row_group(g).num_rows for g in range(groups[0])) if groups else 0
    cuts = iter(starts[a + 1:b])
    cut = next(cuts, None)
    part: list[str] = []
    n = row * k
    for batch in pf.iter_batches(columns=cols, batch_size=_batch_rows(pf), row_groups=groups):
        values = [_as_plain(batch.column(c)).to_pylist() for c in cols]
        for cells in zip(*values):
            if skip_rows:
                skip_rows -= 1
                continue
            for v in cells:
                if n >= end:
                    break
                if n >= first:
                    if n == cut:
                        yield part
                        part, cut = [], next(cuts, None)
                    part.append(json.dumps(v, ensure_ascii=False, default=str))
                n += 1
            if n >= end:
                break
        if n >= end:
            break
    if n != end:
        raise RuntimeError(f"check refused: the view of {Path(src).name} ends at line {n:,}, its parts at {end:,}")
    if part:
        yield part


def view_parts(src: str, cols: list[str], starts: list[int] | None = None, lines: int | None = None):
    """The view's lines in parts: every VIEW_LINES lines, and sooner before a line that would take the part past
    VIEW_CHARS characters (a longer line is a part alone). A part is a bounded unit of work: SWE-chat's 6.4 GB
    conversations file would be one 20 GB view. The build stages each part as a file and records the line each
    begins at (CopiedStage.view_starts); the check reads the published file back at those lines (`starts`), as its
    masked cells may be shorter: gitleaks' findings depend on the text around them, so both scan the same text. A
    published view whose line count is not the build's (`lines`) stops the check before a part could grow unbounded."""
    cuts = iter(starts[1:]) if starts is not None else None
    cut = next(cuts, None) if cuts is not None else None
    part: list[str] = []
    size = 0
    n = -1
    for n, line in enumerate(_view_lines(src, cols) if cols else []):
        if lines is not None and n >= lines:
            raise RuntimeError(f"check refused: the view of {Path(src).name} has more lines than the build's {lines:,}")
        if cuts is not None:
            if n == cut:
                yield part
                part, cut = [], next(cuts, None)
        elif part and (len(part) == VIEW_LINES or size + len(line) > VIEW_CHARS):
            yield part
            part, size = [], 0
        part.append(line)
        size += len(line) + 1
    if lines is not None and n + 1 != lines:
        raise RuntimeError(f"check refused: the view of {Path(src).name} has {n + 1:,} lines, the build's {lines:,}")
    if part:
        yield part


def _supported(t: pa.DataType) -> bool:
    """Types whose every string the view shows as text and the merge can put back: strings, numbers, booleans,
    times, and lists, structs and maps of them. Binary, dictionary, string-view and union columns are not."""
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return True
    if (pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_boolean(t) or pa.types.is_temporal(t)
            or pa.types.is_decimal(t) or pa.types.is_null(t)):
        return True
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        return _supported(t.value_type)
    if pa.types.is_struct(t):
        return all(_supported(t.field(i).type) for i in range(t.num_fields))
    if pa.types.is_map(t):
        return _supported(t.key_type) and _supported(t.item_type)
    return False


def _merge(orig, masked):
    """The original value with its strings taken from the masked one; every other value as it was."""
    if isinstance(orig, tuple):  # a map's (key, value) pairs come back from JSON as lists
        if not isinstance(masked, list) or len(masked) != len(orig):
            raise ValueError("a pair changed shape")
        return tuple(_merge(o, m) for o, m in zip(orig, masked))
    if isinstance(orig, (bytes, bytearray)):
        raise ValueError("bytes cannot be put back from text")
    if isinstance(orig, str):
        if not isinstance(masked, str):
            raise ValueError("a string became something else")
        return masked
    if isinstance(orig, list):
        if not isinstance(masked, list) or len(masked) != len(orig):
            raise ValueError("a list changed shape")
        return [_merge(o, m) for o, m in zip(orig, masked)]
    if isinstance(orig, dict):
        if not isinstance(masked, dict) or set(masked) != set(orig):
            raise ValueError("a mapping changed shape")
        return {k: _merge(v, masked[k]) for k, v in orig.items()}
    return orig


class CopiedStage:
    def __init__(self):
        self.items: list[dict] = []  # one per copied file: dataset, file, kind, staged path, ...
        self.linked: dict[str, tuple[dict, str]] = {}  # datasets left out of this version, and why
        self.wanted: set[str] = set()  # every dataset this version copies, staged again or carried over (by label)

    def paths(self) -> list[Path]:
        return [p for i in self.items for p in (i["parts"] if i["kind"] == "parquet" else [i["staged"]])]

    def view_starts(self, out_dir: Path) -> dict[str, dict]:
        """Where each part of each Parquet file's view begins, and its lines in all, by the file's path in the version
        (written): the check splits the published file at the same lines (view_parts). Kept in the ledger, not
        published."""
        return {str(i["dest"].relative_to(out_dir)): {"starts": i["starts"], "lines": i["lines"]}
                for i in self.items if i["kind"] == "parquet"}

    def add_masked(self, masked: dict[str, Counter]) -> None:
        """The secrets masked in each item: the scan's, over its staged file or parts, and the private keys masked as
        it was staged."""
        for i in self.items:
            staged = i["parts"] if i["kind"] == "parquet" else [i["staged"]]
            i["secrets"] = sum((masked.get(str(p), Counter()) for p in staged), Counter(i.get("pem") or {}))

    def write(self, out_dir: Path, pool, procs: int, log=print) -> dict:
        out: dict[str, dict] = {}
        users = Counter(str(i["staged"]) for i in self.items if i["kind"] == "text")
        for label, (entry, reason) in self.linked.items():
            out[label] = {**_info(entry), "copied": False, "reason": reason,
                          "note": f"Linked, not copied: {reason}. See {entry.get('url')}."}
        for i in self.items:
            i["dest"] = out_dir / "copied" / i["name"] / safe_name(i["entry"]["id"]) / i["file"]["path"]
            i["dest"].parent.mkdir(parents=True, exist_ok=True)
        jobs = [(i["file"]["local"], i["cols"], [str(p) for p in i["parts"]], str(i["dest"])) for i in self.items
                if i["kind"] == "parquet"]
        rebuilt = dict(zip((j[3] for j in jobs), pool.map(_rebuild_job, jobs) if pool is not None and len(jobs) > 1
                           else map(_rebuild_job, jobs)))
        for i in self.items:
            name, entry, f, dest = i["name"], i["entry"], i["file"], i["dest"]
            info = out.setdefault(i["label"], _info(entry, i["label"]))
            if i["kind"] == "parquet":
                changed_cells, rows = rebuilt[str(dest)]
                info["cells_changed"] = info.get("cells_changed", 0) + changed_cells
                info["rows"] += rows
            else:
                # Files with the same content share one staged copy (a default .gitattributes in every dataset):
                # moved for its last user, copied for the others.
                users[str(i["staged"])] -= 1
                if users[str(i["staged"])]:
                    shutil.copyfile(i["staged"], dest)
                else:
                    shutil.move(str(i["staged"]), dest)
                if f["path"].endswith(".jsonl"):
                    with open(dest, "rb") as fh:
                        info["rows"] += sum(1 for line in fh if line.strip())
            info["redactions"]["secrets"] = dict(Counter(info["redactions"]["secrets"]) + i.get("secrets", Counter()))
        for label, info in out.items():
            if label in self.linked:  # its note says why it is linked; nothing of it is copied
                continue
            n = sum(info["redactions"]["secrets"].values())
            seed, carried = info.get("seeded_from"), info["redactions"]["secrets"].get(_carried_rule(info))
            info["note"] = (f"Copied from {info['url']} at revision {info['revision']}; {n:,} secret "
                            f"span{'' if n == 1 else 's'} redacted"
                            + ((f" ({carried:,} of them in version {seed}, whose masked copy this build started "
                                f"from, and {n - carried:,} more)" if n > carried else
                                f" (in version {seed}, whose masked copy this build started from)") if carried else "")
                            + f", nothing else changed. What its card says of its data: \"{info.get('provenance_quote')}\"")
            log(f"build: copied {label}: {info['rows']:,} rows, {n} secret spans redacted")
        out_matches = matches_in(self.items, out_dir)
        if out_matches:
            log(f"build: {out_matches:,} rows of copied datasets are sessions also in the scraped subsets")
        return out


def carried_secrets(entry: dict, label: str) -> int:
    """The secret spans masked by the published version a copy was seeded from (scripts/seed_copied_from_version.py):
    the copy is that version's masked one, so this build finds only what that version's masking left. From its
    changelog, published (release.public_changelog) or the build's own."""
    c = json.loads((Path(entry["seeded_from"]) / "changelog.json").read_text())
    if "copied_datasets" in c:
        return c["copied_datasets"][label]["secret_spans_redacted"]
    return sum(c["copied"][label]["redactions"]["secrets"].values())


def _carried_rule(info: dict) -> str:
    return f"masked in version {info.get('seeded_from')}, carried over"


def _info(entry: dict, label: str | None = None) -> dict:
    """A copied dataset's record in the changelog; a copy seeded from a published version (`label` given) starts with
    that version's count of secrets masked, carried over."""
    info = _base_info(entry)
    if label and entry.get("seeded_from"):
        info["seeded_from"] = Path(entry["seeded_from"]).name
        if n := carried_secrets(entry, label):
            info["redactions"]["secrets"][_carried_rule(info)] = n
    return info


def _base_info(entry: dict) -> dict:
    return {"id": entry["id"], "url": entry.get("url"), "revision": entry.get("revision"),
            "left_out": entry.get("_left_out", []), "error": entry.get("error"),
            "license": entry.get("license"), "copied": bool(entry.get("copied")), "reason": entry.get("reason"),
            "provenance_quote": entry.get("provenance_quote"), "rows": 0, "configs": entry.get("_configs", []),
            "redactions": {"secrets": {}, "emails": 0, "home_users": 0},
            "license_line": (f"{entry.get('license')}, copied from {entry.get('url')} (revision "
                             f"{(entry.get('revision') or '')[:12]}), by {entry.get('author')}")}


def _rebuild_job(job: tuple) -> tuple[int, int]:
    src, cols, parts, dest = job
    changed = _rebuild_parquet(src, cols, [Path(p) for p in parts], Path(dest))
    return changed, pq.ParquetFile(dest).metadata.num_rows


def _staged_lines(parts: list[Path]):
    for part in parts:
        with open(part, encoding="utf-8", errors="surrogateescape") as fh:
            for line in fh:
                yield line.rstrip("\n")


def _rebuild_parquet(src: str, cols: list[str], parts: list[Path], dest: Path) -> int:
    """The Parquet file with every cell whose view line changed rebuilt from the masked line; the cells changed.

    Only a changed cell's column is rebuilt, in the batches holding a changed cell; every other column is written
    from the original arrays, so its values (nanosecond times included) are exactly as they were. A file with no
    change is linked, not copied: same bytes, no second copy on disk.
    """
    # Where lines changed, not the lines: a changed cell of SWE-chat's commits table reads as up to 200 MB. Each
    # batch's masked lines are read again as it is rebuilt.
    changed = [n for n, (old, new) in enumerate(zip(_view_lines(src, cols) if cols else iter(()), _staged_lines(parts)))
               if new != old]
    if not changed:
        try:
            os.link(src, dest)
        except OSError:
            shutil.copyfile(src, dest)
        return 0
    pf = pq.ParquetFile(src)
    schema = pf.schema_arrow
    writer = pq.ParquetWriter(str(dest), schema, compression="zstd")
    base, width = 0, len(cols)
    keys = changed   # in order
    masked = _staged_lines(parts)
    buf: list[pa.Table] = []
    buf_bytes = 0
    for batch in pf.iter_batches(batch_size=_batch_rows(pf)):
        n = batch.num_rows
        lo, hi = bisect.bisect_left(keys, base * width), bisect.bisect_left(keys, (base + n) * width)
        lines = list(itertools.islice(masked, n * width))   # this batch's masked lines, in order
        if len(lines) < n * width:
            raise RuntimeError(f"build refused: the masked view of {Path(src).name} has fewer lines than the file")
        table = pa.Table.from_batches([batch], schema=schema)
        by_col: dict[str, list[tuple[int, str]]] = {}
        for k in keys[lo:hi]:
            r, c = divmod(k, width)
            by_col.setdefault(cols[c], []).append((r - base, lines[k - base * width]))
        for col, cells in by_col.items():
            i = schema.get_field_index(col)
            kind, plain = schema.field(i).type, _plain(schema.field(i).type)
            values = _as_plain(table.column(i)).to_pylist()
            for r, line in cells:
                try:
                    values[r] = _merge(values[r], json.loads(line))
                except (ValueError, json.JSONDecodeError) as e:
                    raise RuntimeError(f"build refused: a masked cell of {Path(src).name} (row {base + r}, {col}) "
                                       f"no longer reads back: {e}") from e
            rebuilt = pa.array(values, type=plain)
            table = table.set_column(i, schema.field(i), pa.chunked_array([rebuilt if plain.equals(kind)
                                                                             else rebuilt.cast(kind)]))
        buf.append(table)
        buf_bytes += table.nbytes
        if buf_bytes >= GROUP_BYTES:
            writer.write_table(pa.concat_tables(buf))
            buf, buf_bytes = [], 0
        base += n
    if buf:
        writer.write_table(pa.concat_tables(buf))
    writer.close()
    return len(changed)


def stage(cfg: Config, ledger: Ledger, stage_dir: Path, pool=None, log=print, only: set[str] | None = None) -> CopiedStage:
    """Every copied file, staged as it would be published: text as it is, a Parquet file as its view. `only`: the
    datasets (by label) an incremental build stages again; the others are carried over from the version before.
    Every dataset left out (linked) is recorded either way."""
    st = CopiedStage()
    views: list[tuple] = []
    texts: list[tuple] = []
    stage_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = stage_dir.resolve()
    from .build import removals

    removed = removals(cfg)
    for name, entry in entries(ledger):
        if not entry.get("id"):
            log(f"build: {name}: an entry with no dataset id is passed over: {str(entry)[:120]}")
            continue
        slug = safe_name(entry["id"])
        if not entry.get("copied"):
            # Listed in the card with why (a copy of another dataset, no licence, gated...); a private dataset,
            # which a token can see only because it is its owner's, is not named at all.
            if entry.get("reason") and entry.get("reason") != "private":
                st.linked[f"{name}/{slug}" if name == "dataclaw" else name] = (entry, entry["reason"])
            continue
        if f"dataset:{entry['id']}" in removed:
            st.linked[f"{name}/{slug}" if name == "dataclaw" else name] = (entry, "removed on request")
            continue
        label = f"{name}/{slug}" if name == "dataclaw" else name
        st.wanted.add(label)
        if only is not None and label not in only:
            continue
        entry["_configs"] = _configs_for(name, slug, label, entry)
        bad = [f["path"] for f in entry.get("files") or [] if f["path"].endswith(".parquet")
               and not all(_supported(fl.type) for fl in pq.ParquetFile(f["local"]).schema_arrow)]
        if bad:
            st.linked[label] = (entry, f"its files hold column types the scan cannot read as text ({bad[0]})")
            continue
        if entry.get("error") and not entry.get("files"):
            st.linked[label] = (entry, f"it could not be read this run ({entry['error'][:80]})")
            continue
        for f in entry.get("files") or []:
            if f["path"].endswith(".parquet"):
                cols = _text_columns(pq.ParquetFile(f["local"]).schema_arrow)
                prefix = str(stage_dir / f"{f['sha256']}.view")
                views.append((f["local"], cols, prefix))
                st.items.append({"name": name, "label": label, "entry": entry, "file": f, "kind": "parquet",
                                 "cols": cols, "prefix": prefix})
            else:
                from .build import kind_of

                staged = stage_dir / f"{f['sha256']}{kind_of(f['path'])}.txt"
                texts.append((name, label, entry, f, staged))
    # The text files, in the pool: private keys by their own structure (scan.pem_spans) as each is staged; personal
    # data only with COPIED_PII. A file shared by several datasets is staged once, and its keys counted for each. (A
    # loop in this process took most of the first full build's first hour over 22,039 files.)
    jobs = sorted({str(t[4]): (t[3]["local"], str(t[4])) for t in texts}.values())
    done_text = {r[0]: r for r in (pool.map(_stage_text, jobs, chunksize=4) if pool is not None and len(jobs) > 1
                                   else map(_stage_text, jobs))}
    for name, label, entry, f, staged in texts:
        _, ok, replaced, pem = done_text[str(staged)]
        if not ok:
            # Only text is published: the scan cannot read other encodings or binary files.
            entry.setdefault("_left_out", []).append(f["path"])
            continue
        if replaced:
            entry.setdefault("_replaced", {})[f["path"]] = replaced
        st.items.append({"name": name, "label": label, "entry": entry, "file": f, "kind": "text",
                         "staged": staged, "pem": pem})
    # The views, a file per worker (a few at a time: each holds a batch of rows); identical files are viewed once.
    todo = list({v[2]: v for v in views}.values())
    done = dict(zip((v[2] for v in todo), pool.map(_write_view, todo) if pool is not None and len(todo) > 1
                    else map(_write_view, todo)))
    for i in st.items:
        if i["kind"] == "parquet":
            paths, i["pem"], i["starts"], i["lines"] = done[i["prefix"]]
            i["parts"] = [Path(p) for p in paths]
    log(f"build: {len(st.items):,} copied files staged")
    return st


def _stage_text(job: tuple) -> tuple[str, bool, int, dict]:
    """A copied text file staged as it would be published: (staged path, whether it is text, broken sequences
    replaced, private keys masked). A large JSON-lines file a line at a time (_stage_lines): DataClaw's largest export
    is 3.3 GB. A file whose masking cannot keep valid JSON is left out, as one that is not text."""
    from . import scan
    from .build import STREAM_BYTES, as_text

    local, staged = Path(job[0]), Path(job[1])
    if local.stat().st_size > STREAM_BYTES and scan.json_like(staged.name) and scan.jsonl(local) and not COPIED_PII:
        ok, replaced, pem = _stage_lines(local, staged)
        return str(staged), ok, replaced, pem
    text, replaced = as_text(local.read_bytes())
    if text is None:
        return str(staged), False, 0, {}
    try:
        text, counts = scan.redact(text, [], pii=COPIED_PII, json_like=scan.json_like(staged.name))
    except RuntimeError:
        return str(staged), False, 0, {}
    staged.write_text(text, encoding="utf-8")
    return str(staged), True, replaced, counts["secrets"]


def _stage_lines(src: Path, staged: Path) -> tuple[bool, int, dict]:
    """A large JSON-lines file staged a line at a time: each line as text (build.as_text), then private keys masked by
    their structure (scan.redact_lines, a record holding a key's lines whole); whether it is text, the broken
    sequences replaced, the keys masked. Memory is a line's, not the file's: masking a file whole takes some 16 times
    its size, and DataClaw's largest export is 3.3 GB."""
    from . import scan
    from .build import as_text

    tmp = staged.with_name(staged.name + ".lines")
    replaced = 0
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for raw in fi:
            body, nl = (raw[:-1], b"\n") if raw.endswith(b"\n") else (raw, b"")
            text, rep = as_text(body)
            if text is None or rep < 0:   # a line that is not UTF-8 text: the file is left out whole, as it would be
                fo.close()
                tmp.unlink()
                return False, 0, {}
            replaced += rep
            fo.write(text.encode("utf-8") + nl)
    counts = scan.redact_lines(tmp, staged, [])
    tmp.unlink(missing_ok=True)
    return True, replaced, counts["secrets"]


def _write_view(job: tuple) -> tuple[list[str], dict, list[int], int]:
    """A Parquet file's view, in parts (view_parts), with every private key masked as it is written (scan.pem_spans):
    the part paths, the keys masked, the line each part begins at, and the lines in all."""
    from . import scan

    src, cols, prefix = job
    paths, pem, starts, at = [], Counter(), [], 0
    for n, part in enumerate(view_parts(src, cols)):
        starts.append(at)
        at += len(part)
        path = f"{prefix}.{n:05d}.jsonl"
        with open(path, "w", encoding="utf-8", errors="surrogateescape") as out:
            for line in part:
                if scan.PEM_HEADER.search(line) or scan.PEM_END.search(line):
                    line, counts = scan.redact(line, [], pii=False, json_like=True)
                    pem.update(counts["secrets"])
                out.write(line + "\n")
        paths.append(path)
    return paths, dict(pem), starts, at


def _configs_for(name: str, slug: str, label: str, entry: dict) -> list:
    configs = []
    for c in entry.get("card_configs") or []:
        configs.append((f"{label.replace('/', '.')}.{c['name']}", [f"copied/{name}/{slug}/{p}" for p in c["data_files"]]))
    if not configs:
        data = [f["path"] for f in entry.get("files") or []
                if f["path"].endswith((".parquet", ".jsonl")) and not f["path"].startswith(".")]
        # One viewer config only where the files are one kind (one file name): a dataset holding a manifest, its
        # normalized sessions and raw transcripts together read as one table fails the dataset viewer
        # (dharshanrai/optim-agent-corpus-harvest, 2026-10-05); its files stay downloadable.
        if data and len({p.rsplit("/", 1)[-1] for p in data}) == 1:
            configs.append((label.replace("/", "."), [f"copied/{name}/{slug}/{p}" for p in data]))
    return configs


def matches_in(items: list[dict], out_dir: Path) -> int:
    """Rows of copied files that are a scraped session, by the agent's own session id: copied/matches.parquet.
    Keys come from the version's own scraped subsets."""
    import pyarrow.dataset as ds

    keys = {}
    data = out_dir / "data"
    if data.exists():
        for sub in data.iterdir():
            t = ds.dataset(str(sub), format="parquet").to_table(columns=["dedup_key", "source", "source_id", "duplicate_of"])
            for r in t.to_pylist():
                if r["dedup_key"]:   # to the session kept: an exact copy shares its key, a near-copy has its own
                    keys[r["dedup_key"]] = r["duplicate_of"] or f"{r['source']}:{r['source_id']}"
    found = []
    for i in items:
        found += _matches(i["name"], i["entry"]["id"], i["file"], keys)
    if found:
        (out_dir / "copied").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(found, schema=MATCH_SCHEMA), str(out_dir / "copied" / "matches.parquet"))
    return len(found)


def _matches(name: str, ds_id: str, f: dict, keys: dict[str, str]) -> list[dict]:
    out = []
    path = Path(f["local"])
    if name == "dataclaw" and path.name.endswith(".jsonl"):
        with open(path, "rb") as fh:
            for i, line in enumerate(fh):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                agent = str(r.get("project") or "").split(":", 1)[0].lower()
                key = f"{PROJECT_AGENT.get(agent, agent)}:{r.get('session_id')}"
                if key in keys:
                    out.append({"dataset": ds_id, "file": f["path"], "row": i, "session_key": key,
                                "matched_row": keys[key]})
    elif name == "swe-chat-v2" and path.suffix == ".parquet" and "session" in path.stem:
        pf = pq.ParquetFile(path)
        if {"session_id", "agent"} <= set(pf.schema_arrow.names):
            from .sources.entire import namespace

            base = 0
            for batch in pf.iter_batches(columns=["session_id", "agent"], batch_size=8192):
                for j, r in enumerate(batch.to_pylist()):
                    key = f"{namespace(r['agent'])}:{r['session_id']}"
                    if key in keys:
                        out.append({"dataset": ds_id, "file": f["path"], "row": base + j, "session_key": key,
                                    "matched_row": keys[key]})
                base += batch.num_rows
    return out
