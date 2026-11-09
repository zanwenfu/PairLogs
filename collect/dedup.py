"""Exact deduplication within and across sources, at the end of every update. Nothing is deleted.

Rows sharing a `dedup_key` are copies of one session: the agent's own session id, namespaced by agent
(`claude-code:<uuid>`), wherever a source records it, else `sha256:<content>`. One copy keeps the content:

1. the one with tool results, then
2. the larger, then
3. the source that holds the agent's own transcript (Entire) before a rendering of it (SpecStory, Aider), then
4. the repository under its current name, then one that is not a fork, then the oldest (repos.rank), then
5. the lower `source:source_id`, so the choice is the same on every run.

Every other copy keeps its row, with `duplicate_of` naming the kept one and no files of its own when published; the
kept row's `also_in` lists every other place the session was found. A session seen again with more turns under the
same `source_id` is not a duplicate: it is the same row, updated by the fetch.

Near-copies, one conversation stored as two sessions under different ids, are matched by the build from the
conversations themselves (near_copies): a copy is a duplicate of the fuller one, as an exact copy is. Copied datasets
are never changed; their matches are recorded by the build in a side table.

A source that records no agent keys a session by its raw line, so one session copied into two of its exports, or
twice into one, keeps two rows under one session id. Where the session ids are the same and the whole conversations
are the same (same_session_copies), anywhere, the copy is a duplicate too.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import repos
from .ledger import Ledger

# The agreed tiebreak after tool results and size: the agent's own transcript first (Entire, then SWE-chat's copy of
# Entire's), then renderings (SpecStory, Aider), then other datasets' exports.
SOURCE_ORDER = {"entire": 0, "swe-chat-v2": 1, "specstory": 2, "aider": 3, "dataclaw": 4, "openhands-feedback": 5}


def _ref(row: dict) -> str:
    return f"{row['source']}:{row['source_id']}"


def _rank(row: dict, places: dict | None = None) -> tuple:
    return (not row.get("has_tool_results"), -(row.get("bytes") or 0), SOURCE_ORDER.get(row["source"], 9),
            *repos.rank(places or {}, row.get("repo")), _ref(row))


def assign(rows: list[dict], places: dict | None = None) -> Counter:
    """The same choice over rows held in memory (the scraped rows with the copied datasets' sessions, at build time):
    `duplicate_of` and `also_in` set on each; duplicate rows by pair of sources. `places`: repos.known."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        row["duplicate_of"], row["also_in"] = None, []
        if row.get("dedup_key"):
            groups[row["dedup_key"]].append(row)
    pairs: Counter = Counter()
    for group in groups.values():
        group.sort(key=lambda r: _rank(r, places))
        kept = group[0]
        for o in group[1:]:
            o["duplicate_of"] = _ref(kept)
            pairs[f"{kept['source']}~{o['source']}"] += 1
        for row in group:
            row["also_in"] = [{"source": x["source"], "source_id": x["source_id"], "found_url": x.get("found_url")}
                              for x in group if x is not row]
    return pairs


def run(ledger: Ledger, log=print) -> dict:
    places = repos.known(ledger)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in ledger.rows():
        if row.get("dedup_key"):
            groups[row["dedup_key"]].append(row)
    pairs: Counter = Counter()
    duplicates = updated = 0
    for key, rows in groups.items():
        rows.sort(key=lambda r: _rank(r, places))
        kept = rows[0]
        others = rows[1:]
        want = {_ref(kept): {"duplicate_of": None,
                             "also_in": [{"source": o["source"], "source_id": o["source_id"],
                                          "found_url": o.get("found_url")} for o in others]}}
        for o in others:
            duplicates += 1
            pairs[f"{kept['source']}~{o['source']}"] += 1
            want[_ref(o)] = {"duplicate_of": _ref(kept),
                             "also_in": [{"source": x["source"], "source_id": x["source_id"],
                                          "found_url": x.get("found_url")} for x in rows if x is not o]}
        for row in rows:
            labels = want[_ref(row)]
            if row.get("duplicate_of") != labels["duplicate_of"] or (row.get("also_in") or []) != labels["also_in"]:
                ledger.update_labels(row["source"], row["source_id"], labels)
                updated += 1
    stats = {"keys": len(groups), "duplicates": duplicates, "pairs": dict(sorted(pairs.items())), "updated": updated}
    log(f"dedup: {duplicates:,} duplicate rows over {len(groups):,} sessions; pairs {stats['pairs']}")
    return stats


# -- Near-copies: one conversation stored as two kept sessions ---------------------------------------------------------
# Measured on version 2026-10-04 (2026-10-06): about 1,300 kept sessions held a conversation another kept session of the
# same repository (or copied export) holds: DataClaw's aggregations copying other exports again, raw and normalized,
# Entire's and SpecStory's saves of one chat. A session is a copy of a kept one in its scope when
# - its typed messages, in order, are the other's, or open the other's longer run (a command by its name); and
# - their agents wrote the same: MIN_TEXTS or more text replies in common, MIN_SHARE or more of the smaller side's (of
#   the copy's own when it is the earlier save); or its typed side is too long to be chance (LONG_MESSAGES or more
#   messages, LONG_CHARS or more characters) and the agents' sides do not differ: with fewer than THIN replies on the
#   smaller side (too few to tell; DataClaw's raw and normalized copies of sessions mostly of tool calls), a reply or
#   LONG_TOOLS tool calls in common; else MIN_TEXTS replies in common and LONG_SHARE of the smaller side (an export that
#   renders the replies anew: one chat's 35 typed messages alike, 8 of its 52 replies).
# Tool calls alone never tell where there are replies: two runs of `/review` read the same files and found different
# things (35 such runs merged before they were left out), and runs of 9 prompts alike shared 1 reply of 54 (2026-10-06).
# Separate runs of the same prompts stay apart, as one short message alike does. The fuller session keeps the content:
# the longer typed side, then the larger agent side, then _rank.
MIN_TEXTS, MIN_SHARE, LONG_MESSAGES, LONG_CHARS, LONG_TOOLS, LONG_SHARE, THIN = 2, 0.5, 3, 200, 3, 0.1, 3


def _prefix_keys(typed: list[int]):
    """A digest of each opening of a typed run, its length with it: an index of openings in linear memory."""
    h = 0
    for i, d in enumerate(typed, 1):
        h = hash((h, d))
        yield i, h


def near_copies(rows: list[dict], summaries: dict[str, dict], scope_of, places: dict | None = None) -> Counter:
    """Over every row of a build after assign: each kept row whose conversation a fuller kept row of its scope holds
    becomes a duplicate of it, with the copies of that copy; each group's rows list one another in also_in, as assign's
    do. `summaries` by `source:source_id` (conv_build.summarize); a row with none, or no typed message, is not matched.
    Returns the copies by pair of sources."""
    cand = []
    for r in rows:
        s = summaries.get(_ref(r))
        if r.get("duplicate_of") or not s or not s["typed"]:
            continue
        cand.append((r, s, set(s["texts"]), set(s["tools"])))
    cand.sort(key=lambda c: (-len(c[1]["typed"]), -(len(c[2]) + len(c[3])), _rank(c[0], places)))   # the fuller first
    index: dict[tuple, list[int]] = defaultdict(list)   # (scope, length, digest of the opening) -> candidates
    root: dict[int, int] = {}
    for i, (r, s, texts, tools) in enumerate(cand):
        scope, typed = scope_of(r), s["typed"]
        full = list(_prefix_keys(typed))[-1][1]
        long_typed = len(typed) >= LONG_MESSAGES and s["chars"] >= LONG_CHARS
        best, best_shared = None, -1
        for j in index.get((scope, len(typed), full), ()):
            o_typed, o_texts, o_tools = cand[j][1]["typed"], cand[j][2], cand[j][3]
            if o_typed[:len(typed)] != typed:   # a digest's collision
                continue
            shared = len(texts & o_texts)
            base = len(texts) if len(o_typed) > len(typed) else min(len(texts), len(o_texts))
            agree = shared >= MIN_TEXTS and shared / max(1, base) >= MIN_SHARE
            if not long_typed:
                alike = False
            elif min(len(texts), len(o_texts)) < THIN:
                alike = shared >= 1 or len(tools & o_tools) >= LONG_TOOLS
            else:
                alike = shared >= MIN_TEXTS and shared / max(1, base) >= LONG_SHARE
            if (agree or alike) and shared > best_shared:
                best, best_shared = j, shared
        if best is not None:
            root[i] = root.get(best, best)
        for n, h in _prefix_keys(typed):
            index[(scope, n, h)].append(i)
    groups: dict[int, list[dict]] = defaultdict(list)
    for i, k in root.items():
        groups[k].append(cand[i][0])
    pairs: Counter = Counter()
    if not groups:
        return pairs
    exact: dict[str, list[dict]] = defaultdict(list)   # assign's copies, by the row they copy
    for r in rows:
        if r.get("duplicate_of"):
            exact[r["duplicate_of"]].append(r)
    for k, copies in groups.items():
        kept = cand[k][0]
        members = [kept, *exact[_ref(kept)]]
        for c in sorted(copies, key=_ref):
            pairs[f"{kept['source']}~{c['source']}"] += 1
            members += [c, *exact[_ref(c)]]
        for m in members[1:]:
            m["duplicate_of"] = _ref(kept)
        for m in members:
            m["also_in"] = [{"source": x["source"], "source_id": x["source_id"], "found_url": x.get("found_url")}
                            for x in members if x is not m]
    return pairs


def same_session_copies(rows: list[dict], summaries: dict[str, dict], places: dict | None = None) -> Counter:
    """Over every row of a build after near_copies: kept rows with the same session id and the same whole conversation
    (summaries' `sid` and `whole`, conv_build.summarize) are one session copied twice, in any scope; one is kept by
    _rank, the others become its duplicates with their own copies, and each group's rows list one another in also_in.
    Ids alone do not tell (an export that records no agent reuses them: 49 of 56 such groups in version 2026-10-28 held
    different conversations), and a conversation alone does not (two people typing the same short request). Returns
    the copies by pair of sources."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        s = summaries.get(_ref(r))
        if r.get("duplicate_of") or not s or not s.get("sid") or s.get("whole") is None:
            continue
        groups[(s["sid"], s["whole"])].append(r)
    exact: dict[str, list[dict]] = defaultdict(list)   # the copies already made, by the row they copy
    for r in rows:
        if r.get("duplicate_of"):
            exact[r["duplicate_of"]].append(r)
    pairs: Counter = Counter()
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: _rank(r, places))
        kept = group[0]
        members = [kept, *exact[_ref(kept)]]
        for c in group[1:]:
            pairs[f"{kept['source']}~{c['source']}"] += 1
            members += [c, *exact[_ref(c)]]
        for m in members[1:]:
            m["duplicate_of"] = _ref(kept)
        for m in members:
            m["also_in"] = [{"source": x["source"], "source_id": x["source_id"], "found_url": x.get("found_url")}
                            for x in members if x is not m]
    return pairs
