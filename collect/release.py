"""A built version as it is published to a shared folder (the first versions went to a university cluster): the version's folder less what is
the build's own record (the dataset card README.md, and the full changelog.json, which stays with the build), with a
changelog that holds what a user needs, and a checksum list beside the folder.

- The public changelog: the version and when it was built; counts per source and in total; each copied dataset's
  source, revision, author, licence and credit line (its licence asks for one); how `pushback` was labelled. The
  hand fixes' records, the run's cost, the checks' numbers and the known gaps' notes stay in the full one.
- The list: sha256sum's own format, one line per file in the order of their paths, each path relative to the
  folder's parent, so `sha256sum -c` run beside the folder checks every file (/data/regression/manifest.py's form).
  A file hard-linked to the same file of the version before (the same inode) keeps that version's line: the weekly
  run hashes only what is new.
- Every file is a hard link to the build's, so the published folder takes no space of its own."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .util import process_pool

OURS = ("README.md", "changelog.json", "inputs.parquet", "summaries.parquet")   # the build's own record, at the top
# Why a typed row has no pushback label, by the label store's answer (scripts/weekly/label.py REFUSED)
UNREAD = {"content_filter": "refused by the service's content filter", "cyber_policy": "refused by the service's policy",
          "refused (model)": "refused by the model", "empty": "hold no text to label"}


def list_name(version: str) -> str:
    return f"errata-data-{version}.sha256"


def copied_credits(full_copied: dict) -> dict:
    """Each copied dataset's source, revision, author, licence and credit line, from the full changelog's `copied`."""
    copied = {}
    for name, e in sorted(full_copied.items()):
        line = e.get("license_line") or ""
        author = line.rsplit(", by ", 1)[1] if ", by " in line else None
        entry = {"url": e["url"], "revision": e["revision"], "license": e["license"], "author": author,
                 "copied": e["copied"]}
        if e["copied"]:
            n = sum((e.get("redactions") or {}).get("secrets", {}).values())
            entry.update({"rows": e["rows"], "secret_spans_redacted": n,
                          "credit": f"Copied from {e['url']} at revision {e['revision']}, by {author}, under its "
                                    f"{e['license']} licence; {n:,} secret span{'' if n == 1 else 's'} redacted, "
                                    "nothing else changed."})
        else:
            entry["credit"] = f"Linked, not copied: {e['reason']}. See {e['url']}."
        copied[name] = entry
    return copied


def public_changelog(full: dict) -> dict:
    """The changelog a user needs, from the build's full one."""
    counts, tables = {}, full["conversations"]["tables"]
    for src, s in sorted(full["scraped"].items()):
        t = tables[src]
        kept = s["rows"] - s["duplicates"]
        if kept != t["sessions"]:
            raise ValueError(f"{src}: {kept} sessions kept by the session table, {t['sessions']} by the conversations")
        counts[src] = {"sessions": s["rows"], "kept": kept, "duplicates": s["duplicates"],
                       "conversation_rows": t["rows"], "subagent_rows": t["subagent_rows"]}
    totals = {k: sum(c[k] for c in counts.values()) for k in next(iter(counts.values()))}
    out = {"version": full["version"], "built_at": full["built_at"], "counts": counts, "totals": totals,
           "copied_datasets": copied_credits(full["copied"])}
    pb = full.get("pushback_labels")
    if pb:
        pushback = {k: pb[k] for k in ("model", "model_version", "date", "reasoning_effort", "instructions",
                                       "answer_format", "input", "unit")}
        # A store written by more than one service names each, with their agreement on texts both read (2026-10-28).
        pushback["service"] = pb.get("service") or "Azure OpenAI"
        pushback.update({k: pb[k] for k in ("services", "agreement", "carried") if pb.get(k)})
        # Each reason with its count, still as text (as published before): "refused by the content filter" stood for
        # all of them, and of version 2026-10-11's 1,794 typed rows with no label 1,740 hold no text (2026-10-12).
        pushback.update({"typed_rows": pb["rows"], "pushback": pb["rows_pushback"], "not_pushback": pb["rows_not"],
                         "no_label": sum(pb["rows_unread"].values()),
                         "no_label_why": "; ".join(f"{n:,} {UNREAD.get(k, k)}" for k, n in
                                                   sorted(pb["rows_unread"].items(), key=lambda kv: -kv[1]))})
        if pushback["typed_rows"] != pushback["pushback"] + pushback["not_pushback"] + pushback["no_label"]:
            raise ValueError("pushback counts do not add up to the typed rows")
        out["pushback_labels"] = pushback
    return out


def _digest(path: str) -> tuple[str, str, int]:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 22):
            h.update(chunk)
    return h.hexdigest(), path, os.stat(path).st_size


def prepare(version_dir: Path, out_root: Path, *, previous: str | None = None, procs: int = 8, log=print) -> dict:
    """`out_root/<version>`: the version's files hard-linked, less OURS, with the public changelog; and
    `out_root/errata-data-<version>.sha256`. `previous`: the version published before, under the same root, whose
    list's lines are kept for files that are the same file (inode) as its own."""
    version_dir = version_dir.resolve()
    name = version_dir.name
    dest = out_root / name
    if dest.exists():
        raise FileExistsError(f"{dest} exists already")
    full = json.loads((version_dir / "changelog.json").read_text())
    if full.get("version") != name:
        raise ValueError(f"{version_dir}: its changelog is version {full.get('version')!r}")
    files = 0
    for p in sorted(version_dir.rglob("*")):
        rel = p.relative_to(version_dir)
        if not p.is_file() or str(rel) in OURS:
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        os.link(p, dest / rel)
        files += 1
    (dest / "changelog.json").write_text(json.dumps(public_changelog(full), indent=1, ensure_ascii=False) + "\n")
    kept = {}   # path relative to the version folder -> the previous list's digest, for the same file
    if previous:
        prev_dir = out_root / previous
        for line in (out_root / list_name(previous)).read_text().splitlines():
            digest, rel = line.split("  ", 1)
            rel = rel.split("/", 1)[1]
            mine, theirs = dest / rel, prev_dir / rel
            if mine.exists() and theirs.exists() and os.stat(mine).st_ino == os.stat(theirs).st_ino:
                kept[rel] = digest
    paths = sorted(str(p) for p in dest.rglob("*") if p.is_file())
    todo = [p for p in paths if str(Path(p).relative_to(dest)) not in kept]
    digests, total = {}, 0
    with process_pool(procs) as pool:   # workers from a fork server: a forked one can inherit a held lock
        for h, p, size in pool.map(_digest, todo, chunksize=16):
            digests[p] = h
            total += size
    with open(out_root / list_name(name), "w") as out:
        for p in paths:
            rel = str(Path(p).relative_to(dest))
            out.write(f"{kept.get(rel) or digests[p]}  {name}/{rel}\n")
    log(f"release: {name}: {files + 1:,} files ({len(kept):,} lines kept from {previous}, {len(todo):,} hashed, "
        f"{total:,} bytes read) -> {out_root / list_name(name)}")
    return {"dir": dest, "list": out_root / list_name(name), "files": files + 1, "hashed": len(todo), "kept": len(kept)}
