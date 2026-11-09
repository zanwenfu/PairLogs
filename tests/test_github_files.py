"""collect.sources.github_files.fits: repository paths as local ones the disk accepts."""

from collect.sources.github_files import NAME_MAX, fits


def test_short_paths_are_kept_as_they_are():
    p = ".specstory/history/2026-04-13_06-16-02Z-添加一个功能.md"
    assert fits(p) == p


def test_a_name_over_the_limit_is_cut_hashed_and_keeps_its_extension():
    long = "2026-04-13Z-" + "添" * 250 + ".md"            # 765 bytes, as a Windows-made name can be
    other = "2026-04-13Z-" + "添" * 249 + "加.md"
    got, got2 = fits(".specstory/history/" + long), fits(".specstory/history/" + other)
    name = got.split("/")[-1]
    assert got.startswith(".specstory/history/2026-04-13Z-添") and name.endswith(".md")
    assert len(name.encode()) <= NAME_MAX and name.encode().decode() == name
    assert got != got2 and fits(".specstory/history/" + long) == got   # distinct names stay distinct; stable


# -- the known-repositories route ----------------------------------------------------------------------------------
import pytest

from collect import relabel
from collect.config import Config
from collect.ledger import Ledger
from collect.sources import Context, aider, github_files, specstory


class FakeGitHub:
    def repo_meta(self, repos):
        return {}


def ctx(tmp_path, github=None):
    cfg = Config(data=tmp_path / "data", state=tmp_path / "state")
    return Context(config=cfg, ledger=Ledger(tmp_path / "ledger.sqlite"), github=github, cpu=None, log=lambda m: None)


def test_known_repositories_come_from_the_ledger_and_the_seed_list(tmp_path):
    c = ctx(tmp_path)
    c.ledger.upsert({"source": "specstory", "source_id": "o/a:.specstory/history/x:y.md", "content_sha256": "1"})
    c.ledger.upsert({"source": "aider", "source_id": "o/z:.aider.chat.history.md", "content_sha256": "2"})
    github_files.seed_file(c, "specstory").parent.mkdir(parents=True)
    github_files.seed_file(c, "specstory").write_text("# a past version's\no/b\n  o/c  \nnot-a-repo\nx/y/z\n")
    assert github_files.known_repos(c, "specstory") == {"ledger": {"o/a"}, "seeds": {"o/b", "o/c"}}
    assert github_files.known_repos(c, "aider") == {"ledger": {"o/z"}, "seeds": set()}


@pytest.mark.parametrize("source", [specstory, aider])
def test_a_run_reads_the_known_repositories_beside_the_search(tmp_path, monkeypatch, source):
    c = ctx(tmp_path, FakeGitHub())
    c.ledger.upsert({"source": source.NAME, "source_id": "o/known:f.md", "content_sha256": "1"})
    github_files.seed_file(c, source.NAME).parent.mkdir(parents=True)
    github_files.seed_file(c, source.NAME).write_text("o/seed\n")
    read = []
    monkeypatch.setattr(source, "search_files", lambda ctx, name, q: [{"repo": "o/found", "path": "p", "url": "u"}])
    monkeypatch.setattr(source, "search_gaps", lambda ctx, name, q: {})
    monkeypatch.setattr(source, "read_repo", lambda ctx, name, repo, wanted: read.append(repo) or ("h", {}))
    monkeypatch.setattr(relabel, "recount", lambda ctx, name, facts: 0)
    monkeypatch.setattr(relabel, "repos", lambda ctx, name: 0)
    stats = source.run(c)
    assert sorted(read) == ["o/found", "o/known", "o/seed"] and stats["repos"] == 3
