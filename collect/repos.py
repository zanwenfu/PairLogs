"""What GitHub says of each repository name a run looks up: its current name, whether it is a fork, when it was made.

A repository renamed or moved answers to its old names, and a fork holds copies of its parent's branches, so one
session can be read in two or three places. On 2026-10-25 SWE-chat's repository list, read as Entire's seed list, named
53 repositories by old names (41 of them read again under their current ones) and 92 forks; the copy kept was the
lowest name, so 2,098 kept sessions sat in a fork or under an old name, 824 of them with another licence. Discovery
reads each repository once, under its current name, and de-duplication keeps a session's copy in the repository under
its current name, then one that is not a fork, then the oldest.
"""

from __future__ import annotations

STATE = "github"   # the ledger's state rows for what GitHub said, `repo:<name as looked up>`


def current(meta: dict, name: str) -> str:
    """The name GitHub gives the repository `name` answers to now (`meta` from GitHub.repo_meta)."""
    return (meta.get(name) or {}).get("repo") or name


def once(meta: dict, names) -> list[str]:
    """Each repository once, under its current name."""
    return sorted({current(meta, n) for n in names})


def by_current(meta: dict) -> dict:
    """`meta` with each repository also under its current name."""
    return {**meta, **{m["repo"]: m for m in list(meta.values()) if m and m.get("repo")}}


def remember(ledger, meta: dict) -> None:
    for name, m in meta.items():
        if m:
            ledger.set_state(STATE, f"repo:{name}", {"current": m.get("repo"), "fork": bool(m.get("fork")),
                                                     "created_at": m.get("created_at")})


def known(ledger) -> dict[str, dict]:
    """What the runs so far were told of each repository name, by name."""
    return {k[len("repo:"):]: v for k, v in ledger.state_keys(STATE, "repo:").items() if isinstance(v, dict)}


def rank(places: dict, repo: str | None) -> tuple:
    """The order among alike copies of a session by where they were found (lower first): the repository under its
    current name, then one that is not a fork, then the oldest. A name GitHub was never asked about ranks as current."""
    p = places.get(repo or "") or {}
    return (bool(p.get("current")) and p.get("current") != repo, bool(p.get("fork")), p.get("created_at") or "9999")
