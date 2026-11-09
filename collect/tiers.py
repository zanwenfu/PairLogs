"""The two release tiers of a published version: full text where licences allow it, pointers for the rest.

- **Tier A, the full text.** Every session whose repository carries a licence we recognise (Entire, SpecStory,
  Aider), and every session of a copied dataset whose licence lets anyone share it (DataClaw, OpenHands feedback:
  MIT). Their session rows (raw `files` included), and their rows in `conversations` and `conversations_subagents`.
  A copy of a tier-A session (`duplicate_of`) keeps its session row when its own source would be in tier A too.
- **Tier B, pointers.** One row for every other kept session, SWE-chat v2's included: where it was found and how to
  fetch it again (repository, commit, checkpoint, source id, the raw files' paths, sizes and sha256), and every other
  place it was found (`also_in`), never its text.
  The pipeline rebuilds them from their sources: `collect update --only-file repositories.txt`; SWE-chat v2's sessions
  come from SWE-chat's own release, under its own terms.

Beside them, labels without text (tier_labels): tier B's message labels, SWE-chat's prompts, owners' types.

Nothing else changes: rows, columns and masking are the version's own. Each input file maps to one output file of the
same name, written only when it keeps a row, so memory stays bounded by one file.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import tier_labels
from .release import copied_credits

GITHUB_SOURCES = ("entire", "specstory", "aider")
SHAREABLE_COPIED = ("dataclaw", "openhands-feedback")   # MIT; SWE-chat v2 is gated by its authors: pointers only
NOT_A_LICENCE = ("", "none", "unrecognized")
POINTER_COLUMNS = ("session_key", "source", "session_id", "source_id", "repo", "repo_url", "repo_license",
                   "found_url", "found_commit", "checkpoint_pk", "strategy", "agent", "models", "started_at",
                   "ended_at", "n_user_messages", "n_tool_calls")
TABLES = ("conversations", "conversations_subagents")
ALSO_IN = pa.list_(pa.struct([("source", pa.string()), ("source_id", pa.string()), ("found_url", pa.string())]))


def shareable(source: str | None, licence: str | None) -> bool:
    """May this session's text go into tier A, by its source and its repository's licence?"""
    if source in SHAREABLE_COPIED:
        return True
    return source in GITHUB_SOURCES and licence is not None and licence not in NOT_A_LICENCE


def _sessions(version: Path) -> pa.Table:
    cols = ["session_key", "source", "repo_license", "duplicate_of"]
    parts = [pq.read_table(f, columns=cols) for f in sorted(version.glob("data/*/*.parquet"))]
    return pa.concat_tables(parts, promote_options="default") if parts else pa.table({c: [] for c in cols})


def tier_a_keys(sessions: pa.Table) -> tuple[set[str], set[str]]:
    """(kept sessions in tier A, copies kept with them): a copy rides with its kept session when it is shareable."""
    rows = sessions.to_pylist()
    kept = {r["session_key"] for r in rows if r["duplicate_of"] is None and shareable(r["source"], r["repo_license"])}
    copies = {r["session_key"] for r in rows
              if r["duplicate_of"] in kept and shareable(r["source"], r["repo_license"])}
    return kept, copies


def _filter_file(src: Path, dst: Path, column: str, keys: set[str]) -> int:
    table = pq.read_table(src)
    if not len(table):
        return 0
    kept = table.filter(pc.is_in(table[column], value_set=pa.array(sorted(keys), type=table[column].type)))
    if len(kept):
        dst.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(kept, dst, compression="zstd")
    return len(kept)


def _pointers(version: Path, in_a: set[str]) -> pa.Table:
    """Tier B: every kept session not in tier A, as where to find it again; raw files by path, size and hash only."""
    out = []
    for f in sorted(version.glob("data/*/*.parquet")):
        names = pq.read_schema(f).names
        table = pq.read_table(f, columns=[c for c in (*POINTER_COLUMNS, "duplicate_of", "files", "also_in")
                                          if c in names])
        keep = pc.and_(pc.is_null(table["duplicate_of"]),
                       pc.invert(pc.is_in(table["session_key"], value_set=pa.array(sorted(in_a), type=pa.string()))))
        table = table.filter(keep)
        if not len(table):
            continue
        files = [[{"path": x["path"], "bytes": x["bytes"], "sha256": x["sha256"]} for x in (fs or [])]
                 for fs in table["files"].to_pylist()] if "files" in names else [[] for _ in range(len(table))]
        cols = {c: table[c] for c in POINTER_COLUMNS if c in table.column_names}
        cols["files"] = pa.array(files, type=pa.list_(pa.struct([("path", pa.string()), ("bytes", pa.int64()),
                                                                 ("sha256", pa.string())])))
        # every other place the session was found (2026-11-03): a part of it saved there under the same session id
        # is listed even where its text is not kept (about 0.9% of kept sessions in version 2026-11-02)
        also = [[{"source": x["source"], "source_id": x["source_id"], "found_url": x.get("found_url")}
                 for x in (a or [])] for a in table["also_in"].to_pylist()] if "also_in" in names \
            else [[] for _ in range(len(table))]
        cols["also_in"] = pa.array(also, type=ALSO_IN)
        out.append(pa.table(cols))
    return pa.concat_tables(out, promote_options="default") if out else pa.table({"session_key": pa.array([], pa.string())})


def write(version: Path, out: Path, log=print, *, signals: dict | None = None,
          swe_prompts: Path | None = None) -> dict:
    """Write tier A under out/a and tier B under out/b from a published version folder, with the labels beside them
    (tier_labels.add: tier B's message labels always; owners' types with `signals`, login -> GitHub's profile facts;
    SWE-chat's prompts with `swe_prompts`, the matching's output); return the counts."""
    version, out = Path(version), Path(out)
    if not (version / "changelog.json").exists():
        raise FileNotFoundError(f"{version} is not a published version (no changelog.json)")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty: tiers are written once, into an empty folder")
    sessions = _sessions(version)
    kept, copies = tier_a_keys(sessions)
    counts = {"a": {"sessions": len(kept), "copies": len(copies), "rows": {}}, "b": {}}
    for f in sorted(version.glob("data/*/*.parquet")):
        _filter_file(f, out / "a" / f.relative_to(version), "session_key", kept | copies)
    for t in TABLES:
        n = 0
        for f in sorted(version.glob(f"{t}/*/*.parquet")):
            n += _filter_file(f, out / "a" / f.relative_to(version), "session_key", kept)
        counts["a"]["rows"][t] = n
        log(f"tiers: {t}: {n:,} rows in tier A")
    for name in SHAREABLE_COPIED:   # the copied datasets' own files, as released, where their licence allows it
        for f in sorted((version / "copied" / name).rglob("*")) if (version / "copied" / name).exists() else ():
            if f.is_file():
                dst = out / "a" / f.relative_to(version)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(f.read_bytes())
    pointers = _pointers(version, kept)
    (out / "b").mkdir(parents=True, exist_ok=True)
    pq.write_table(pointers, out / "b" / "pointers.parquet", compression="zstd")
    repos = sorted({r for r, s in zip(pointers["repo"].to_pylist() if "repo" in pointers.column_names else [],
                                      pointers["source"].to_pylist() if "source" in pointers.column_names else [])
                    if r and s in GITHUB_SOURCES})
    (out / "b" / "repositories.txt").write_text("".join(f"{r}\n" for r in repos))
    by_source: dict[str, int] = {}
    for s in pointers["source"].to_pylist() if "source" in pointers.column_names else []:
        by_source[s] = by_source.get(s, 0) + 1
    also = pointers["also_in"].to_pylist() if "also_in" in pointers.column_names else []
    counts["b"] = {"sessions": len(pointers), "by_source": dict(sorted(by_source.items())), "repositories": len(repos),
                   "also_in": {"sessions": sum(1 for a in also if a), "places": sum(len(a) for a in also)}}
    full = json.loads((version / "changelog.json").read_text())
    # A build's changelog holds `copied`, a published one `copied_datasets`; both key a dataset as <source>/<name>.
    # Each tier credits the copied datasets it carries: tier A the shareable ones, tier B the rest (2026-11-03).
    credits = full.get("copied_datasets") or copied_credits(full.get("copied") or {})
    for tier in ("a", "b"):
        note = {"version": full.get("version"), "tier": tier.upper(), "counts": counts[tier],
                "copied_datasets": {k: v for k, v in credits.items()
                                    if (k.split("/")[0] in SHAREABLE_COPIED) == (tier == "a")},
                "rule": ("tier A: sessions whose repository licence is recognised, and the MIT copied datasets"
                         if tier == "a" else
                         "tier B: pointers to every other kept session; rebuild with "
                         "`collect update --only-file repositories.txt`; SWE-chat v2 from SWE-chat's own release")}
        (out / tier / "changelog.json").write_text(json.dumps(note, indent=1) + "\n")
    try:   # owners' ages are counted on the version's own date
        on = date.fromisoformat(str(full.get("version")))
    except ValueError:
        on = None
    counts["labels"] = tier_labels.add(version, {"a": out / "a", "b": out / "b"}, signals=signals,
                                       swe_prompts=swe_prompts, on=on, log=log)
    log(f"tiers: A {len(kept):,} sessions (+{len(copies):,} copies), B {len(pointers):,} pointers")
    return counts
