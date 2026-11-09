"""Datasets copied in from Hugging Face, as they are, at a recorded revision.

- `swe-chat-v2`: SALT-NLP/SWE-chat (gated: the owner accepts its conditions on Hugging Face first; ODC-BY).
- `dataclaw`: every dataset tagged `dataclaw` (each a person's own Claude Code or Codex history, published with the
  DataClaw tool, which sets the MIT licence).
- `openhands-feedback`: OpenHands/openhands-feedback (MIT; feedback its authors shared for redistribution).

A dataset whose licence forbids changes (any no-derivatives licence) or that states no licence is linked, not
copied, with the reason. A dataset is downloaded again only when its revision changes. What each dataset's card says
about where its data came from is kept verbatim (`provenance_quote`), with no judgement of whether it is real.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..util import safe_name, sha256_file
from . import Context

DATASETS = {"swe-chat-v2": ["SALT-NLP/SWE-chat"], "openhands-feedback": ["OpenHands/openhands-feedback"]}
GATED_OK = {"swe-chat-v2"}  # gated datasets whose terms the owner read and accepted
TAGS = {"dataclaw": "dataclaw"}
NO_DERIVATIVES = re.compile(r"(^|[-_])nd([-_]|$)", re.I)


def _card_text(path: Path) -> str:
    try:
        text = path.read_text(errors="replace")
    except FileNotFoundError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end > 0 else text
    return text.strip()


def provenance_quote(card: str, limit: int = 900) -> str:
    """The card's own words on where its data came from: its first paragraphs, verbatim, cut at a sentence."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", card) if p.strip() and not p.strip().startswith(("#", "|", "```", "<"))]
    text = ""
    for p in paras:
        if len(text) + len(p) > limit:
            break
        text += (" " if text else "") + " ".join(p.split())
    if not text and paras:
        text = " ".join(paras[0].split())[:limit]
    return text


def run(ctx: Context, name: str) -> dict:
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi(token=ctx.config.hf_token)
    if name in TAGS:
        ids = sorted(d.id for d in api.list_datasets(filter=TAGS[name]))
    else:
        ids = DATASETS[name]
    if ctx.only:
        ids = [i for i in ids if i in ctx.only]
    if ctx.limit:
        ids = ids[:ctx.limit]
    stats = {"datasets": len(ids), "new": 0, "changed": 0, "same": 0, "linked": 0, "errors": 0}
    infos, failed = lookup(api, ids)
    same_as = identical(infos, name)
    if ctx.dry_run:
        return preview(ctx, name, ids, infos, failed, same_as, stats)
    for ds_id in ids:
        key = f"dataset:{ds_id}"
        if ds_id in failed:
            # The previous copy stays as it was: a failed lookup must not drop a dataset from the next version.
            stats["errors"] += 1
            prev = ctx.ledger.get_state(name, key) or {"id": ds_id}
            ctx.ledger.set_state(name, key, {**prev, "error": failed[ds_id]})
            ctx.log(f"{name}: {ds_id}: cannot read its information: {failed[ds_id][:160]}")
            continue
        info = infos[ds_id]
        entry = decide(name, ds_id, info, same_as.get(ds_id))
        prev = ctx.ledger.get_state(name, key) or {}
        if not entry["copied"]:
            stats["linked"] += 1
            ctx.ledger.set_state(name, key, {**entry, "files": []})
            continue
        old_dirs = [d for d in (ctx.config.copied / name / safe_name(ds_id)).glob("*") if d.name != info.sha]
        if prev.get("revision") == info.sha and prev.get("files") and all(Path(f["local"]).exists()
                                                                          for f in prev["files"]):
            stats["same"] += 1
            continue
        dest = ctx.config.copied / name / safe_name(ds_id) / info.sha
        try:
            snapshot_download(repo_id=ds_id, repo_type="dataset", revision=info.sha, local_dir=str(dest),
                              token=ctx.config.hf_token, max_workers=8)
        except Exception as e:
            stats["errors"] += 1
            # The previous copy stays, if there is one; a first download that fails keeps the dataset's id and decision,
            # so the build links it with the reason (an entry with no id once stopped every build).
            ctx.ledger.set_state(name, key, {**(prev or {**entry, "files": []}), "error": f"{type(e).__name__}: {str(e)[:200]}"})
            ctx.log(f"{name}: {ds_id}: download failed: {type(e).__name__}: {str(e)[:160]}")
            continue
        files = []
        for p in sorted(dest.rglob("*")):
            if p.is_file() and ".cache" not in p.relative_to(dest).parts:
                files.append({"path": str(p.relative_to(dest)), "local": str(p), "bytes": p.stat().st_size,
                              "sha256": sha256_file(p)})
        entry["files"] = files
        entry["provenance_quote"] = provenance_quote(_card_text(dest / "README.md"))
        entry["card_configs"] = _configs(dest / "README.md")
        ctx.ledger.set_state(name, key, entry)
        for d in old_dirs:  # an older revision's files, no longer published
            import shutil

            shutil.rmtree(d, ignore_errors=True)
        stats["changed" if prev.get("revision") else "new"] += 1
        ctx.log(f"{name}: {ds_id} at {info.sha[:10]}: {len(files)} files, "
                f"{sum(f['bytes'] for f in files) / 1e6:,.1f} MB")
    return stats


def lookup(api, ids: list[str]) -> tuple[dict, dict[str, str]]:
    """Each dataset's information with its files' digests; and the ones that could not be read, with why."""
    infos, failed = {}, {}
    for ds_id in ids:
        try:
            infos[ds_id] = api.dataset_info(ds_id, files_metadata=True)
        except Exception as e:
            failed[ds_id] = f"{type(e).__name__}: {str(e)[:200]}"
    return infos, failed


def identical(infos: dict, name: str) -> dict[str, str]:
    """Datasets whose data files are byte for byte another's (every file but the card and dot-files, by digest),
    each mapped to the earliest-created of its group that can itself be copied (licensed, not private, not gated
    unread), which alone is copied. 19 of the 49 DataClaw datasets on 2026-10-03 were such copies (18 of one
    person's data), which copied would have put the same sessions in the collection up to 18 times. A dataset that
    cannot be copied keeps its own reason; none is named as another's copy."""
    groups: dict[tuple, list[str]] = {}
    for ds_id, info in infos.items():
        digests = sorted(
            (sib.lfs.sha256 if getattr(sib, "lfs", None) else sib.blob_id) or f"size:{sib.size}"
            for sib in info.siblings or []
            if not sib.rfilename.startswith(".") and "/." not in sib.rfilename and sib.rfilename != "README.md")
        if digests:
            groups.setdefault(tuple(digests), []).append(ds_id)
    out = {}
    for members in groups.values():
        eligible = [d for d in members if decide(name, d, infos[d])["copied"]]
        if len(eligible) > 1:
            first = min(eligible, key=lambda d: (str(infos[d].created_at or ""), d))
            out.update({d: first for d in eligible if d != first})
    return out


def decide(name: str, ds_id: str, info, same_as: str | None = None) -> dict:
    """A dataset's entry: what it is, and whether it is copied or linked (with the reason)."""
    card = info.card_data or {}
    licence = (card.get("license") if hasattr(card, "get") else None) or next(
        (t.split(":", 1)[1] for t in (info.tags or []) if t.startswith("license:")), None)
    licence = licence if isinstance(licence, str) else (licence[0] if isinstance(licence, list) and licence else None)
    entry = {"id": ds_id, "url": f"https://huggingface.co/datasets/{ds_id}", "revision": info.sha,
             "last_modified": str(info.last_modified), "license": licence, "gated": info.gated or False,
             "author": info.author}
    if getattr(info, "private", False):
        # A token can read its owner's private datasets; none of them goes into a public dataset.
        entry.update(copied=False, reason="private")
    elif info.gated and name not in GATED_OK:
        entry.update(copied=False, reason="gated by its owner; only datasets whose terms were read are copied")
    elif not licence:
        entry.update(copied=False, reason="its card states no licence")
    elif NO_DERIVATIVES.search(licence):
        entry.update(copied=False, reason=f"its licence ({licence}) forbids changes, and secrets must be redacted")
    elif same_as:
        entry.update(copied=False, identical_to=same_as,
                     reason=f"its data files are byte for byte those of {same_as}, which is copied")
    else:
        entry["copied"] = True
    return entry


def preview(ctx: Context, name: str, ids: list[str], infos: dict, failed: dict, same_as: dict, stats: dict) -> dict:
    """What a real run would do, read without downloading: each dataset copied or linked and why; for Parquet files,
    from their footers alone, the rows, the bytes once read (about what the build's text views take on disk), and
    any column type the scan cannot read as text, which would make a real run link the dataset instead."""
    from huggingface_hub import HfFileSystem
    import pyarrow.parquet as pq

    from ..hfcopy_build import _supported

    fs = HfFileSystem(token=ctx.config.hf_token)
    out = []
    for ds_id in ids:
        if ds_id in failed:
            stats["errors"] += 1
            out.append({"id": ds_id, "error": failed[ds_id]})
            ctx.log(f"{name}: {ds_id}: cannot read its information: {failed[ds_id][:160]}")
            continue
        info = infos[ds_id]
        entry = decide(name, ds_id, info, same_as.get(ds_id))
        row = {"id": ds_id, "copied": entry["copied"], "reason": entry.get("reason"), "license": entry["license"],
               "files": 0, "download_bytes": 0, "text_bytes": 0, "parquet_files": 0, "parquet_rows": 0,
               "parquet_bytes_read": 0, "unsupported": []}
        for sib in info.siblings or []:
            if sib.rfilename.startswith(".") or "/." in sib.rfilename:
                continue
            row["files"] += 1
            row["download_bytes"] += sib.size or 0
            if not sib.rfilename.endswith(".parquet"):
                row["text_bytes"] += sib.size or 0
                continue
            row["parquet_files"] += 1
            if not entry["copied"] or row.get("unreadable"):
                continue
            try:
                with fs.open(f"datasets/{ds_id}@{info.sha}/{sib.rfilename}", "rb") as fh:
                    pf = pq.ParquetFile(fh)
                    md, schema = pf.metadata, pf.schema_arrow
            except Exception as e:
                row["unreadable"] = (f"{type(e).__name__}: {str(e)[:120]} (a gated dataset needs its terms accepted "
                                     f"by the token's account)")
                continue
            row["parquet_rows"] += md.num_rows
            row["parquet_bytes_read"] += sum(md.row_group(i).total_byte_size for i in range(md.num_row_groups))
            row["unsupported"] += [f"{sib.rfilename}: {f.name} ({f.type})" for f in schema if not _supported(f.type)]
        copied = entry["copied"] and not row["unsupported"]
        stats["new" if copied else "linked"] += 1
        out.append(row)
        why = "" if copied else f" ({entry.get('reason') or 'column types the scan cannot read: ' + '; '.join(row['unsupported'][:3])})"
        ctx.log(f"{name}: {ds_id}: {'copied' if copied else 'linked'}{why}; {row['files']:,} files, "
                f"{row['download_bytes'] / 1e9:,.2f} GB"
                + (f"; Parquet {row['parquet_files']} files, {row['parquet_rows']:,} rows, "
                   f"{row['parquet_bytes_read'] / 1e9:,.2f} GB once read" if row["parquet_rows"] else "")
                + (f"; FOOTERS UNREADABLE: {row['unreadable']}" if row.get("unreadable") else ""))
    copied_rows = [r for r in out if r.get("copied") and not r.get("unsupported")]
    stats["preview"] = out
    stats["download_gb"] = round(sum(r["download_bytes"] for r in copied_rows) / 1e9, 2)
    # The build stages each text file once more, and each Parquet file as a text view about its size once read.
    stats["stage_gb_estimate"] = round(sum(r["text_bytes"] + r["parquet_bytes_read"] for r in copied_rows) / 1e9, 2)
    stats["footers_unreadable"] = [r["id"] for r in out if r.get("unreadable")]
    return stats


def _configs(readme: Path) -> list[dict]:
    """The `configs` a dataset card declares (name and data files), so the copy keeps the same subsets."""
    try:
        text = readme.read_text(errors="replace")
    except FileNotFoundError:
        return []
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    try:
        import yaml  # huggingface_hub depends on PyYAML

        meta = yaml.safe_load(text[3:end]) or {}
    except Exception:
        return []
    out = []
    for c in meta.get("configs") or []:
        if isinstance(c, dict) and c.get("config_name"):
            files = c.get("data_files")
            patterns = []
            if isinstance(files, str):
                patterns = [files]
            elif isinstance(files, list):
                for f in files:
                    if isinstance(f, str):
                        patterns.append(f)
                    elif isinstance(f, dict):
                        path = f.get("path")
                        patterns += path if isinstance(path, list) else [path] if path else []
            out.append({"name": c["config_name"], "data_files": patterns})
    return out
