"""Labels shipped beside the release tiers, never text (2026-11-03: every label ships with the dataset).

- **Tier B's message labels** (`labels/<table>/<source>/<file>`): tier B holds pointers, so its sessions would carry
  none of our labels. For every row of tier B's sessions in `conversations` and `conversations_subagents`: the row's
  key and our labels (`typed_by_developer`, `actor`, `kind`, `is_error`, `copy_of`, `pushback`), and on typed rows the
  id of its text (collect.pushback.text_id), so a session rebuilt from its source takes our pushback labels by text, as
  collect.pushback.apply does. No text, tool input, command or path; none of SWE-chat's own labels (`prompt_intent`,
  `prompt_pushback`, `language`: SWE-chat's release holds them).
- **SWE-chat v2's labelled prompts** (`labels/swe_chat_prompts.parquet`, tier B): who wrote each prompt SWE-chat
  labels, by SWE-chat's own `turn_id`, from scripts/paper_e1/swe_prompts_who_typed.py; with our pushback label where
  the prompt is a typed row of ours.
- **Owners** (`repositories.parquet`, each tier): each repository the tier holds and its owner's type on GitHub, a
  person, an organisation, or not found. Account age, public repositories and university profiles appear only as
  totals in the tier's changelog, never per owner (by design, 2026-10-26: observable signals, in aggregate;
  2026-11-03: the owner's type per repository).

`add` writes these into tiers already written (by `tiers.write`, or a release's own folders) and changes nothing else
there but each tier's changelog, which gains their counts.
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .pushback import text_id

TABLES = ("conversations", "conversations_subagents")
ROW_KEYS = {"conversations": ("session_key", "turn_number"),
            "conversations_subagents": ("session_key", "task_pk", "turn_number")}
LABELS = ("typed_by_developer", "actor", "kind", "is_error", "copy_of", "pushback")
# who wrote a SWE-chat prompt: the matching's class -> the released value
WRITTEN_BY = {"typed by a person": "typed", "person action": "action", "program": "program", "unknown": "unknown"}
OWNER_TYPE = {"User": "person", "Organization": "organisation"}   # else "not found" (an account gone from GitHub)


def message_labels(version: Path, tier_b: Path, keys: set[str]) -> dict[str, int]:
    """labels/<table>/<source>/<file> for the rows of `keys`' sessions, one file for each of the version's files that
    holds one; returns the rows written by table."""
    rows = {}
    for t in TABLES:
        n = 0
        for f in sorted(Path(version).glob(f"{t}/*/*.parquet")):
            names = pq.read_schema(f).names
            cols = [c for c in (*ROW_KEYS[t], *LABELS) if c in names]
            table = pq.read_table(f, columns=[*cols, "content"])
            table = table.filter(pc.is_in(table["session_key"],
                                          value_set=pa.array(sorted(keys), type=table["session_key"].type)))
            if not len(table):
                continue
            ids = [None] * len(table)
            typed = (pc.indices_nonzero(pc.fill_null(table["typed_by_developer"], False)).to_pylist()
                     if "typed_by_developer" in cols else [])
            for i, text in zip(typed, table["content"].take(pa.array(typed, pa.int64())).to_pylist()):
                ids[i] = text_id(text)
            out = table.select(cols).append_column("text_id", pa.array(ids, pa.string()))
            dst = Path(tier_b) / "labels" / t / f.parent.name / f.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(out, dst, compression="zstd")
            n += len(out)
        rows[t] = n
    return rows


def swe_chat_prompts(src: Path, dst: Path) -> dict:
    """labels/swe_chat_prompts.parquet from the matching's output: SWE-chat's keys, who wrote the prompt, our label."""
    t = pq.read_table(src, columns=["turn_id", "session_id", "turn_number", "cls", "our_pb"])
    if pc.count_distinct(t["turn_id"]).as_py() != len(t):
        raise ValueError(f"{src}: turn_id repeats; it must name one SWE-chat prompt")
    unknown = set(t["cls"].to_pylist()) - set(WRITTEN_BY)
    if unknown:
        raise ValueError(f"{src}: classes not known: {sorted(unknown)}")
    written_by = [WRITTEN_BY[c] for c in t["cls"].to_pylist()]
    out = pa.table({"turn_id": t["turn_id"], "session_id": t["session_id"], "turn_number": t["turn_number"],
                    "written_by": pa.array(written_by, pa.string()), "pushback": t["our_pb"]})
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(out, dst, compression="zstd")
    by = {}
    for w in written_by:
        by[w] = by.get(w, 0) + 1
    return {"prompts": len(out), "written_by": dict(sorted(by.items()))}


def _repos(tier: Path, name: str) -> list[str]:
    files = [tier / "pointers.parquet"] if name == "b" else sorted(tier.glob("data/*/*.parquet"))
    out = set()
    for f in files:
        if f.exists() and "repo" in pq.read_schema(f).names:
            out |= {r for r in pq.read_table(f, columns=["repo"])["repo"].to_pylist() if r}
    return sorted(out)


def _years(created: str | None, on: date) -> float | None:
    if not created:
        return None
    return round((on - date.fromisoformat(created[:10])).days / 365.25, 2)


def owners(repos: list[str], signals: dict[str, dict], on: date) -> tuple[pa.Table, dict]:
    """(repositories table: repo, owner_type; the totals for the changelog). `signals`: login -> {type, created,
    public_repos, university}, as GitHub's profiles gave them; an owner missing from it is counted, not guessed."""
    kinds, totals = [], {"repositories": len(repos), "repositories_by_owner_type": {}, "owners": {},
                         "median_account_age_years": {}, "median_public_repositories": {}, "university_profiles": {},
                         "owners_not_looked_up": 0}
    seen: dict[str, str] = {}
    for repo in repos:
        login = repo.split("/")[0]
        s = signals.get(login)
        kind = OWNER_TYPE.get(s.get("type"), "not found") if s else "not looked up"
        kinds.append(kind)
        seen[login] = kind
        totals["repositories_by_owner_type"][kind] = totals["repositories_by_owner_type"].get(kind, 0) + 1
    for login, kind in seen.items():
        totals["owners"][kind] = totals["owners"].get(kind, 0) + 1
    totals["owners_not_looked_up"] = totals["owners"].pop("not looked up", 0)
    for kind in ("person", "organisation"):
        group = [signals[x] for x, k in seen.items() if k == kind]
        ages = [a for a in (_years(s.get("created"), on) for s in group) if a is not None]
        public = [s["public_repos"] for s in group if s.get("public_repos") is not None]
        if group:
            totals["median_account_age_years"][kind] = statistics.median(ages) if ages else None
            totals["median_public_repositories"][kind] = statistics.median(public) if public else None
            totals["university_profiles"][kind] = sum(1 for s in group if s.get("university"))
    for k in ("repositories_by_owner_type", "owners"):
        totals[k] = dict(sorted(totals[k].items()))
    return pa.table({"repo": pa.array(repos, pa.string()), "owner_type": pa.array(kinds, pa.string())}), totals


def add(version: Path, tiers: dict[str, Path], *, signals: dict[str, dict] | None = None,
        swe_prompts: Path | None = None, on: date | None = None, log=print) -> dict:
    """Write the labels above into tiers already written (`tiers`: {"a": folder, "b": folder}); each tier's changelog
    gains `labels` (what each table holds, and its counts). Written once: a tier that already has them is refused."""
    a, b = Path(tiers["a"]), Path(tiers["b"])
    for path in (b / "labels", a / "repositories.parquet", b / "repositories.parquet"):
        if path.exists():
            raise FileExistsError(f"{path} exists: the labels are written once")
    on = on or date.today()
    keys = set(pq.read_table(b / "pointers.parquet", columns=["session_key"])["session_key"].to_pylist())
    notes = {"a": {}, "b": {}}
    rows = message_labels(version, b, keys)
    notes["b"]["message_labels"] = {
        "files": "labels/conversations/<source>/, labels/conversations_subagents/<source>/",
        "holds": "for each row of tier B's sessions: its key and our labels (typed_by_developer, actor, kind, "
                 "is_error, copy_of, pushback), and text_id on typed rows (sha256 of the row's content, first 24 hex "
                 "characters) to join our pushback labels to a session rebuilt from its source; no text",
        "rows": rows}
    log(f"tier labels: B message labels {rows}")
    if swe_prompts is not None:
        got = swe_chat_prompts(Path(swe_prompts), b / "labels" / "swe_chat_prompts.parquet")
        notes["b"]["swe_chat_prompts"] = {
            "file": "labels/swe_chat_prompts.parquet",
            "holds": "who wrote each prompt SWE-chat v2 labels (typed by a person, a person's action, a program, or "
                     "unknown), by SWE-chat's own turn_id, with our pushback label where the prompt is a typed row of "
                     "ours; no text and none of SWE-chat's labels",
            **got}
        log(f"tier labels: SWE-chat prompts {got}")
    if signals is not None:
        for name, note in owner_notes({"a": a, "b": b}, signals, on, log).items():
            notes[name]["owners"] = note
    for name, tier in (("a", a), ("b", b)):
        if notes[name]:
            _note(tier, notes[name])
    return notes


def _note(tier: Path, labels: dict) -> None:
    path = tier / "changelog.json"
    note = json.loads(path.read_text())
    note["labels"] = {**note.get("labels", {}), **labels}
    tmp = path.with_name(path.name + ".labels-tmp")
    tmp.write_text(json.dumps(note, indent=1) + "\n")
    tmp.replace(path)


def owner_notes(tiers: dict[str, Path], signals: dict[str, dict], on: date, log=print) -> dict[str, dict]:
    """Each tier's repositories.parquet written (again, where it exists) from `signals`; returns each tier's
    changelog note on its owners. `owners_again` writes the notes too."""
    notes = {}
    for name, tier in tiers.items():
        tier = Path(tier)
        table, totals = owners(_repos(tier, name), signals, on)
        tmp = tier / "repositories.parquet.owners-tmp"
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(tier / "repositories.parquet")
        notes[name] = {
            "file": "repositories.parquet",
            "holds": "each repository the tier holds and its owner's type on GitHub (person, organisation, or not "
                     "found); account age, public repositories and university profiles as totals here only, "
                     f"never per owner; ages on {on.isoformat()}",
            **totals}
        log(f"tier labels: tier {name.upper()} owners {totals['owners']}, not looked up "
            f"{totals['owners_not_looked_up']}")
    return notes


def owners_again(tiers: dict[str, Path], signals: dict[str, dict], on: date, log=print) -> dict[str, dict]:
    """repositories.parquet and the changelog's `labels.owners` of tiers that have them, written again from a
    fuller lookup (2026-11-03: 18 owners of tier A's copies were missing from the first one)."""
    for tier in tiers.values():
        if not (Path(tier) / "repositories.parquet").exists():
            raise FileNotFoundError(f"{tier} has no repositories.parquet: use add")
    notes = owner_notes(tiers, signals, on, log)
    for name, tier in tiers.items():
        _note(Path(tier), {"owners": notes[name]})
    return notes
