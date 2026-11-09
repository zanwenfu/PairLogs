"""collect.sources.entire.more_repos: the settings-file search and the seed list, beside trailer commits."""

from __future__ import annotations

from collect.config import Config
from collect.ledger import Ledger
from collect.sources import Context, entire


class FakeGitHub:
    def __init__(self, items):
        self.items, self.queries = items, []

    def search(self, kind, q, *, page=1, per_page=100, **params):
        self.queries.append((kind, q, page))
        return {"total_count": len(self.items), "incomplete_results": False,
                "items": self.items if page == 1 else []}


def ctx(tmp_path, github):
    cfg = Config(data=tmp_path / "data", state=tmp_path / "state")
    return Context(config=cfg, ledger=Ledger(tmp_path / "ledger.sqlite"), github=github, cpu=None, log=lambda m: None)


def item(repo, fork=False):
    return {"repository": {"full_name": repo, "fork": fork}, "path": ".entire/settings.json", "html_url": "u"}


def test_settings_search_finds_repositories_and_skips_forks(tmp_path):
    gh = FakeGitHub([item("a/one"), item("b/two"), item("c/fork-of-one", fork=True)])
    got = entire.more_repos(ctx(tmp_path, gh))
    assert got["settings"] == {"a/one", "b/two"} and got["seeds"] == set()
    assert gh.queries and gh.queries[0][0] == "code" and entire.SETTINGS_QUERY in gh.queries[0][1]


def test_seed_list_one_repository_a_line(tmp_path):
    c = ctx(tmp_path, None)   # no token: no search, the seed list still read
    entire.seed_file(c).parent.mkdir(parents=True)
    entire.seed_file(c).write_text("# from SWE-chat v2's sessions\no/a\n\n  o/b  \nnot-a-repo\nx/y/z\n")
    got = entire.more_repos(c)
    assert got == {"settings": set(), "seeds": {"o/a", "o/b"}}
