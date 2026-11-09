"""collect.release: a built version as it is published at Duke (no card, the public changelog, a checksum list)."""

import hashlib
import json
import os

import pytest

from collect import release

FULL = {"version": "2026-10-11", "built_at": "2026-10-11T15:00:00Z",
        "scraped": {"aider": {"rows": 3, "duplicates": 1, "new": 1}, "entire": {"rows": 5, "duplicates": 0}},
        "conversations": {"tables": {"aider": {"rows": 10, "subagent_rows": 0, "sessions": 2},
                                     "entire": {"rows": 40, "subagent_rows": 4, "sessions": 5}}},
        "copied": {"swe-chat-v2": {"url": "https://h/s", "revision": "r1", "license": "odc-by", "copied": True,
                                   "license_line": "odc-by, copied from https://h/s (revision r1), by SALT", "rows": 5,
                                   "redactions": {"secrets": {"masked": 2}}},
                   "dataclaw/a__b": {"url": "https://h/a", "revision": "r2", "license": "mit", "copied": False,
                                     "license_line": "mit, copied from https://h/a (revision r2), by a",
                                     "reason": "its data files are byte for byte those of b/c, which is copied"}},
        "pushback_labels": {"model": "gpt-6-luna", "model_version": "2026-09-22", "date": "2026-10-11",
                            "reasoning_effort": "none", "instructions": "You classify one message.", "answer_format": {},
                            "input": "the message's text", "unit": "one call per distinct text", "rows": 11,
                            "rows_pushback": 3, "rows_not": 5, "rows_unread": {"content_filter": 1, "empty": 2},
                            "cost_usd_list_price": 0.31},
        "gaps": ["an internal note"], "relabelled_after_build": {"rows": 2}, "check_found_masked": {"strings": 1}}


def build(root, name, full, files):
    v = root / "build" / name
    for rel, data in files.items():
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_bytes(data)
    (v / "README.md").write_text("the card")
    (v / "changelog.json").write_text(json.dumps({**full, "version": name}))
    (v / "inputs.parquet").write_bytes(b"each session's fingerprint")   # the build's own: never published
    (v / "summaries.parquet").write_bytes(b"each session's conversation in digests")   # the build's own too
    return v


def test_a_version_published_has_no_card_the_public_changelog_and_a_list(tmp_path):
    v = build(tmp_path, "2026-10-11", FULL, {"data/aider/part-000.parquet": b"a", "conversations/aider/part-000.parquet": b"b",
                                             "copied/swe-chat-v2/s.jsonl": b"c"})
    out = tmp_path / "duke"
    got = release.prepare(v, out, procs=2, log=lambda m: None)
    d = out / "2026-10-11"
    assert not (d / "README.md").exists() and not (d / "inputs.parquet").exists()
    assert not (d / "summaries.parquet").exists()
    assert got["files"] == 4 and got["hashed"] == 4
    assert os.stat(d / "data/aider/part-000.parquet").st_ino == os.stat(v / "data/aider/part-000.parquet").st_ino
    pub = json.loads((d / "changelog.json").read_text())
    assert list(pub) == ["version", "built_at", "counts", "totals", "copied_datasets", "pushback_labels"]
    assert pub["counts"]["aider"] == {"sessions": 3, "kept": 2, "duplicates": 1, "conversation_rows": 10, "subagent_rows": 0}
    assert pub["totals"] == {"sessions": 8, "kept": 7, "duplicates": 1, "conversation_rows": 50, "subagent_rows": 4}
    assert pub["copied_datasets"]["swe-chat-v2"]["credit"] == ("Copied from https://h/s at revision r1, by SALT, under its "
                                                              "odc-by licence; 2 secret spans redacted, nothing else changed.")
    assert pub["copied_datasets"]["dataclaw/a__b"]["credit"].startswith("Linked, not copied: its data files are")
    assert pub["pushback_labels"]["no_label"] == 3 and "cost_usd_list_price" not in pub["pushback_labels"]
    assert pub["pushback_labels"]["no_label_why"] == "2 hold no text to label; 1 refused by the service's content filter"
    text = (d / "changelog.json").read_text()
    assert "an internal note" not in text and "relabelled" not in text and "check_found" not in text
    lines = (out / "errata-data-2026-10-11.sha256").read_text().splitlines()
    paths = [l.split("  ", 1)[1] for l in lines]
    assert paths == sorted(paths) and paths[0] == "2026-10-11/changelog.json" and len(lines) == 4
    for line in lines:
        digest, rel = line.split("  ", 1)
        assert digest == hashlib.sha256((out / rel).read_bytes()).hexdigest()
    assert json.loads((v / "changelog.json").read_text())["gaps"] == ["an internal note"]   # the build keeps its own
    with pytest.raises(FileExistsError):
        release.prepare(v, out, procs=2, log=lambda m: None)


def test_the_next_version_keeps_the_lines_of_files_it_shares_and_hashes_the_rest(tmp_path):
    first = build(tmp_path, "2026-10-11", FULL, {"data/aider/part-000.parquet": b"a", "conversations/aider/part-000.parquet": b"b",
                                                 "copied/swe-chat-v2/s.jsonl": b"c"})
    out = tmp_path / "duke"
    release.prepare(first, out, procs=2, log=lambda m: None)
    second = build(tmp_path, "2026-10-18", FULL, {"conversations/aider/part-000.parquet": b"b2",
                                                  "conversations/aider/part-001.parquet": b"new"})
    for rel in ("data/aider/part-000.parquet", "copied/swe-chat-v2/s.jsonl"):   # carried over: the same files
        (second / rel).parent.mkdir(parents=True, exist_ok=True)
        os.link(first / rel, second / rel)
    got = release.prepare(second, out, previous="2026-10-11", procs=2, log=lambda m: None)
    assert got["kept"] == 2 and got["hashed"] == 3 and got["files"] == 5
    for line in (out / "errata-data-2026-10-18.sha256").read_text().splitlines():
        digest, rel = line.split("  ", 1)
        assert rel.startswith("2026-10-18/") and digest == hashlib.sha256((out / rel).read_bytes()).hexdigest()


def test_a_changelog_of_another_version_is_refused(tmp_path):
    v = build(tmp_path, "2026-10-11", FULL, {"data/aider/part-000.parquet": b"a"})
    (v / "changelog.json").write_text(json.dumps({**FULL, "version": "2026-10-04"}))
    with pytest.raises(ValueError):
        release.prepare(v, tmp_path / "duke", procs=2, log=lambda m: None)


def test_labels_from_two_services_are_named_each_in_the_public_changelog(tmp_path):
    services = [{"service": "Azure OpenAI", "texts": 3}, {"service": "AWS Bedrock", "texts": 1}]
    full = {**FULL, "pushback_labels": {**FULL["pushback_labels"], "service": "Azure OpenAI, then AWS Bedrock",
                                        "services": services, "agreement": {"texts": 2, "same_label": 2}}}
    pb = release.public_changelog(full)["pushback_labels"]
    assert pb["service"] == "Azure OpenAI, then AWS Bedrock" and pb["services"] == services
    assert pb["agreement"] == {"texts": 2, "same_label": 2}
    assert release.public_changelog(FULL)["pushback_labels"]["service"] == "Azure OpenAI"   # one service, as before
