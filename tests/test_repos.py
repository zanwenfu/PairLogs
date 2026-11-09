"""collect.repos: a session's copies under old names and in forks (2026-10-25: SWE-chat's repository list as Entire's
seed list brought 53 old names and 92 forks; the copy kept was the lowest name)."""

from __future__ import annotations

import pytest

from collect import dedup, relabel, repos
from collect.config import Config
from collect.ledger import Ledger
from collect.sources import Context, aider, entire, specstory


def meta(current, fork=False, created="2025-01-01T00:00:00Z"):
    return {"repo": current, "fork": fork, "created_at": created, "repo_license": "mit", "repo_language": None,
            "repo_url": f"https://github.com/{current}"}


GITHUB = {"o/old": meta("o/new"), "O/NEW": meta("o/new"), "o/new": meta("o/new"),
          "f/cli": meta("f/cli", fork=True), "m/mirror": meta("m/mirror", created="2026-01-01T00:00:00Z"),
          "a/orig": meta("a/orig", created="2025-06-01T00:00:00Z"), "old/cp": meta("new/cp"), "new/cp": meta("new/cp")}


class FakeGitHub:
    def __init__(self):
        self.asked = []

    def repo_meta(self, names):
        self.asked.append(sorted(names))
        return {n: GITHUB.get(n) for n in names}


def ctx(tmp_path, github=None):
    cfg = Config(data=tmp_path / "data", state=tmp_path / "state")
    return Context(config=cfg, ledger=Ledger(tmp_path / "ledger.sqlite"), github=github, cpu=None, log=lambda m: None)


def row(place, size=100, tools=True):
    return {"source": "entire", "source_id": f"{place}:s1", "repo": place, "dedup_key": "claude-code:s1",
            "content_sha256": place, "bytes": size, "has_tool_results": tools}


def kept(rows, places):
    dedup.assign(rows, places)
    return [r["repo"] for r in rows if not r["duplicate_of"]]


def test_the_copy_kept_is_under_the_current_name_then_not_a_fork_then_the_oldest():
    places = {n: {"current": m["repo"], "fork": m["fork"], "created_at": m["created_at"]} for n, m in GITHUB.items()}
    # ASCII order alone would keep "O/NEW", then "a/orig", "f/cli", "m/mirror", "o/new", "o/old"
    assert kept([row(p) for p in ("o/old", "O/NEW", "f/cli", "m/mirror", "o/new")], places) == ["o/new"]
    assert kept([row(p) for p in ("f/cli", "m/mirror", "a/orig")], places) == ["a/orig"]
    assert kept([row(p) for p in ("f/cli", "m/mirror")], places) == ["m/mirror"]
    # what a copy holds still comes first: a fork's fuller copy is kept
    assert kept([row("f/cli", size=200), row("o/new")], places) == ["f/cli"]
    assert kept([row("f/cli"), row("o/new", tools=False)], places) == ["f/cli"]
    # with nothing known of the names, the order is as it was: the lower name
    assert kept([row(p) for p in ("o/old", "f/cli")], {}) == ["f/cli"]


def test_the_ledgers_dedup_reads_what_the_runs_were_told(tmp_path):
    c = ctx(tmp_path)
    for p in ("O/NEW", "o/new", "f/cli"):
        c.ledger.upsert(row(p))
    repos.remember(c.ledger, GITHUB)
    dedup.run(c.ledger, log=lambda m: None)
    got = {r["repo"]: r.get("duplicate_of") for r in c.ledger.rows("entire")}
    assert got == {"o/new": None, "O/NEW": "entire:o/new:s1", "f/cli": "entire:o/new:s1"}


def test_relabel_remembers_what_github_said_of_every_rows_repository(tmp_path):
    c = ctx(tmp_path, FakeGitHub())
    c.ledger.upsert(row("o/old"))
    relabel.repos(c, "entire")
    assert repos.known(c.ledger) == {"o/old": {"current": "o/new", "fork": False, "created_at": "2025-01-01T00:00:00Z"}}


def test_entire_reads_each_repository_and_place_once_under_its_current_name(tmp_path, monkeypatch):
    c = ctx(tmp_path, FakeGitHub())
    monkeypatch.setattr(entire, "discover", lambda ctx: {"commits": [{"repo": "a/orig", "sha": "s1"}]})
    monkeypatch.setattr(entire, "more_repos", lambda ctx: {"settings": {"O/NEW"}, "seeds": {"o/old", "o/new", "f/cli"}})
    probed, fetched = [], []

    def probe(repo):
        probed.append(repo)
        places = {repo: "d"}
        if repo == "a/orig":   # its settings name a checkpoint repository by an old name, and it is probed itself
            places["old/cp"] = "cp"
        if repo == "f/cli":
            places["new/cp"] = "cp"
        return {"repo": repo, "sources": places, "error": None}

    monkeypatch.setattr(entire, "probe", probe)
    monkeypatch.setattr(entire, "fetch_source", lambda ctx, s, m, found: fetched.append((s, (m or {}).get("repo")))
                        or {"skipped": {}})
    monkeypatch.setattr(relabel, "recount", lambda ctx, name, facts: 0)
    monkeypatch.setattr(relabel, "repos", lambda ctx, name: 0)
    stats = entire.run(c)
    assert sorted(probed) == ["a/orig", "f/cli", "o/new"]
    assert sorted(fetched) == [("a/orig", "a/orig"), ("f/cli", "f/cli"), ("new/cp", "new/cp"), ("o/new", "o/new")]
    assert stats["sources"] == 4


@pytest.mark.parametrize("source", [specstory, aider])
def test_specstory_and_aider_read_a_renamed_repository_once(tmp_path, monkeypatch, source):
    c = ctx(tmp_path, FakeGitHub())
    c.ledger.upsert({"source": source.NAME, "source_id": "o/new:f.md", "content_sha256": "1"})
    read = []
    monkeypatch.setattr(source, "search_files", lambda ctx, name, q: [{"repo": "o/old", "path": "p", "url": "u"},
                                                                     {"repo": "O/NEW", "path": "p", "url": "u"}])
    monkeypatch.setattr(source, "search_gaps", lambda ctx, name, q: {})
    monkeypatch.setattr(source, "read_repo", lambda ctx, name, repo, wanted: read.append(repo) or ("h", {}))
    monkeypatch.setattr(relabel, "recount", lambda ctx, name, facts: 0)
    monkeypatch.setattr(relabel, "repos", lambda ctx, name: 0)
    stats = source.run(c)
    assert read == ["o/new"] and stats["repos"] == 1


def test_the_build_keeps_the_copy_the_same_rule_keeps(tmp_path):
    from collect import build

    c = ctx(tmp_path)
    for p in ("O/NEW", "f/cli", "o/new"):
        c.ledger.upsert(row(p))
    repos.remember(c.ledger, GITHUB)
    rows_by_source, pairs, left_out = build.current_rows(c.config, c.ledger, None)
    assert [r["repo"] for r in rows_by_source["entire"] if not r["duplicate_of"]] == ["o/new"]


def test_near_copies_order_alike_sessions_by_the_same_rule():
    places = {n: {"current": m["repo"], "fork": m["fork"], "created_at": m["created_at"]} for n, m in GITHUB.items()}
    rows = [dict(row(p), source_id=f"{p}:{i}", dedup_key=None) for i, p in enumerate(("O/NEW", "f/cli", "o/new"))]
    for r in rows:
        r["duplicate_of"], r["also_in"] = None, []
    summary = {"typed": [1, 2, 3], "texts": ["a", "b"], "tools": [], "chars": 300}
    summaries = {f"entire:{r['source_id']}": summary for r in rows}
    dedup.near_copies(rows, summaries, lambda r: "one scope", places)
    assert [r["repo"] for r in rows if not r["duplicate_of"]] == ["o/new"]
