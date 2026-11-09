"""Copied datasets: which are copied, which linked, and why, with stand-in Hugging Face objects (no network); and
the refusals of `publish`.

    python -m pytest tests/test_hfcopy.py     (needs huggingface_hub and pyarrow; run where the collector runs)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import hfcopy_build, publish  # noqa: E402
from collect.config import Config  # noqa: E402
from collect.ledger import Ledger  # noqa: E402
from collect.sources import Context, hfcopy  # noqa: E402


def _info(ds_id: str, created: str, licence: str | None = "mit", private: bool = False, digest: str = "d1") -> NS:
    return NS(id=ds_id, sha="a" * 40, created_at=created, last_modified=created, author=ds_id.split("/")[0],
              private=private, gated=False, card_data={"license": licence} if licence else {}, tags=[],
              siblings=[NS(rfilename="README.md", size=10, lfs=None, blob_id="r"),
                        NS(rfilename="data/conversations.jsonl", size=100, lfs=NS(sha256=digest), blob_id="b")])


def test_the_copy_kept_is_the_earliest_that_can_be_copied() -> None:
    infos = {"a/first": _info("a/first", "2026-01-01", licence=None), "b/second": _info("b/second", "2026-02-01"),
             "c/third": _info("c/third", "2026-03-01"), "d/private": _info("d/private", "2025-12-01", private=True)}
    same = hfcopy.identical(infos, "dataclaw")
    assert same == {"c/third": "b/second"}, same  # not a/first (no licence), never d/private
    assert hfcopy.decide("dataclaw", "a/first", infos["a/first"], same.get("a/first"))["reason"] == "its card states no licence"


def test_a_failed_first_download_is_linked_with_its_reason_not_a_crash(tmp_path: Path) -> None:
    cfg = Config(data=tmp_path / "data", state=tmp_path / "state")
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ctx = Context(config=cfg, ledger=ledger, github=None, cpu=None, log=lambda m: None)
    api = mock.MagicMock()
    api.dataset_info.return_value = _info("OpenHands/openhands-feedback", "2026-01-01")
    with mock.patch("huggingface_hub.HfApi", return_value=api), \
            mock.patch("huggingface_hub.snapshot_download", side_effect=RuntimeError("403: accept the terms")):
        stats = hfcopy.run(ctx, "openhands-feedback")
    assert stats["errors"] == 1
    entry = ledger.get_state("openhands-feedback", "dataset:OpenHands/openhands-feedback")
    assert entry["id"] == "OpenHands/openhands-feedback" and entry["error"] and entry["files"] == []
    st = hfcopy_build.stage(cfg, ledger, tmp_path / "stage", log=lambda m: None)
    assert "openhands-feedback" in st.linked and "could not be read" in st.linked["openhands-feedback"][1]


def test_publish_refuses_a_build_with_failed_sources_or_without_licence_lookups(tmp_path: Path) -> None:
    cfg = Config(data=tmp_path / "data", state=tmp_path / "state", hf_token="not-a-real-token")
    v = tmp_path / "data" / "build" / "2026-10-03"
    v.mkdir(parents=True)
    for changelog, why in (({"version": v.name, "failed_sources": ["entire"], "licences_looked_up": True}, "failed"),
                           ({"version": v.name, "licences_looked_up": True}, "no record"),
                           ({"version": v.name, "failed_sources": [], "licences_looked_up": False}, "licences")):
        (v / "changelog.json").write_text(json.dumps(changelog))
        try:
            publish.upload(cfg, Ledger(tmp_path / "ledger.sqlite"), v, changelog, log=lambda m: None)
        except RuntimeError as e:
            assert why in str(e), (why, str(e))
        else:
            raise AssertionError(f"published a build that should be refused ({why})")


def test_copied_text_is_staged_with_its_private_keys_masked(tmp_path: Path) -> None:
    from collect.util import sha256_file

    cfg = Config(data=tmp_path / "data", state=tmp_path / "state")
    ledger = Ledger(tmp_path / "ledger.sqlite")
    local = tmp_path / "download" / "conversations.jsonl"
    local.parent.mkdir(parents=True)
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIE" + "Ab" * 40 + "\n-----END RSA PRIVATE KEY-----"
    local.write_text(json.dumps({"session_id": "s1", "messages": [{"content": "the key:\n" + key}]}) + "\n")
    ledger.set_state("dataclaw", "dataset:x/ds", {
        "id": "x/ds", "url": "https://huggingface.co/datasets/x/ds", "revision": "a" * 40, "license": "mit",
        "copied": True, "files": [{"path": "conversations.jsonl", "local": str(local), "bytes": local.stat().st_size,
                                   "sha256": sha256_file(local)}]})
    st = hfcopy_build.stage(cfg, ledger, tmp_path / "stage", log=lambda m: None)
    (item,) = st.items
    staged = item["staged"].read_text()
    assert "MIIE" not in staged and item["pem"] == {"private-key": 1}, (staged, item["pem"])
    assert json.loads(staged)["messages"][0]["content"] == "the key:\n[REDACTED]"


def test_a_views_parts_read_in_spans_are_the_same_parts(tmp_path):
    """The check shares a large copied table's parts among its workers (view_span): any split gives the parts
    view_parts reads at the build's starts, line for line, part boundaries mid-row included (2026-10-05)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pytest

    from collect import hfcopy_build

    path = tmp_path / "t.parquet"
    rows = [{"a": f"a{i}", "b": None if i % 4 == 0 else f"b{i}", "c": [f"c{i}", "x"] if i % 3 else []} for i in range(21)]
    table = pa.Table.from_pylist(rows)
    with pq.ParquetWriter(path, table.schema) as w:
        for lo, hi in ((0, 7), (7, 12), (12, 21)):   # three row groups
            w.write_table(table.slice(lo, hi - lo))
    cols = ["a", "b", "c"]
    lines = 21 * 3
    starts = [0, 4, 9, 20, 33, 40, 61]   # parts that begin mid-row
    whole = list(hfcopy_build.view_parts(str(path), cols, starts, lines))
    assert sum(len(x) for x in whole) == lines and len(whole) == len(starts)
    for split in ([(0, 7)], [(0, 2), (2, 3), (3, 7)], [(0, 1), (1, 6), (6, 7)], [(i, i + 1) for i in range(7)]):
        got = [part for a, b in split for part in hfcopy_build.view_span(str(path), cols, starts, lines, a, b)]
        assert got == whole, split
    with pytest.raises(RuntimeError, match="ends at line"):   # a published view shorter than the build's
        list(hfcopy_build.view_span(str(path), cols, starts[:-1] + [70], 75, 6, 7))


def test_the_check_shares_a_large_copied_tables_parts_among_units(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from collect import check

    path = tmp_path / "big.parquet"
    pq.write_table(pa.Table.from_pylist([{"a": "x" * 50, "b": str(i)} for i in range(40)]), path)
    got = {"starts": list(range(0, 80, 8)), "lines": 80}   # 10 parts of 8 lines
    monkeypatch.setattr(check, "SPLIT_BYTES", path.stat().st_size // 3)
    spans = check._spans(path, got)
    assert len(spans) > 1 and spans[0][0] == 0 and spans[-1][1] == 10
    assert all(x[1] == y[0] for x, y in zip(spans, spans[1:]))   # contiguous, each part once
    assert check._spans(path, {**got, "lines": 81}) == [None]   # lines not its cells: whole, so view_parts stops it
    assert check._spans(tmp_path / "small.jsonl", got) == [None] if (tmp_path / "small.jsonl").write_text("{}") else False
    monkeypatch.setattr(check, "SPLIT_BYTES", 1 << 40)
    assert check._spans(path, got) == [None]


def test_a_copy_seeded_from_a_published_version_carries_its_count_of_secrets_masked(tmp_path: Path) -> None:
    # scripts/seed_copied_from_version.py: the copy is that version's masked one, so this build finds only what that
    # masking left (version 2026-10-28 said 70 for SWE-chat v2, not 194,825)
    from collect.util import sha256_file

    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIE" + "Ab" * 40 + "\n-----END RSA PRIVATE KEY-----"
    for form, seed_log in (("published", {"copied_datasets": {"dataclaw/x__ds": {"secret_spans_redacted": 5}}}),
                           ("full", {"copied": {"dataclaw/x__ds": {"redactions": {"secrets": {"a": 3, "b": 2}}}}})):
        root = tmp_path / form
        seed = root / "published" / "2026-10-11"
        seed.mkdir(parents=True)
        (seed / "changelog.json").write_text(json.dumps(seed_log))
        cfg = Config(data=root / "data", state=root / "state")
        ledger = Ledger(root / "ledger.sqlite")
        local = root / "download" / "conversations.jsonl"
        local.parent.mkdir(parents=True)
        local.write_text(json.dumps({"session_id": "s1", "messages": [{"content": "the key:\n" + key}]}) + "\n")
        ledger.set_state("dataclaw", "dataset:x/ds", {
            "id": "x/ds", "url": "https://huggingface.co/datasets/x/ds", "revision": "a" * 40, "license": "mit",
            "copied": True, "seeded_from": str(seed),
            "files": [{"path": "conversations.jsonl", "local": str(local), "bytes": local.stat().st_size,
                       "sha256": sha256_file(local)}]})
        st = hfcopy_build.stage(cfg, ledger, root / "stage", log=lambda m: None)
        st.add_masked({})   # the scan found nothing more; the private key was masked as it was staged
        info = st.write(root / "out", None, 1, log=lambda m: None)["dataclaw/x__ds"]
        assert info["redactions"]["secrets"] == {"masked in version 2026-10-11, carried over": 5, "private-key": 1}
        assert info["seeded_from"] == "2026-10-11"
        assert ("; 6 secret spans redacted (5 of them in version 2026-10-11, whose masked copy this build started "
                "from, and 1 more), nothing else changed.") in info["note"], info["note"]
