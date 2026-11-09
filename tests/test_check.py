"""The check of a built version fails on a secret or a run-machine path anywhere it publishes, and passes a clean one.

    python -m pytest tests/test_check.py     (needs pyarrow and the gitleaks the scan uses; run on the run machine)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import check  # noqa: E402
from collect.build import SCHEMA  # noqa: E402
from collect.config import Config  # noqa: E402
from collect.ledger import Ledger  # noqa: E402

TOKEN = "ghp_" + "Q7w8E9r0T1y2" * 3


def _gitleaks() -> bool:
    from collect import scan

    try:
        scan.gitleaks_path()
        return True
    except RuntimeError:
        return False


import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(not _gitleaks(), reason="the check runs gitleaks, which is not here")


def _row(source_id: str, content: str, facts: dict) -> dict:
    row = {f.name: None for f in SCHEMA}
    row.update(source="entire", source_id=source_id, models=[], also_in=[], source_facts=json.dumps(facts),
               redactions={"secrets": 0, "emails": 0, "home_users": 0},
               files=[{"path": "full.jsonl", "bytes": len(content), "sha256": "0" * 64, "content": content}])
    return row


def _version(root: Path, *, content: str, facts: dict, copied_line: str) -> Path:
    v = root / "build" / "2026-10-03"
    (v / "data" / "entire").mkdir(parents=True)
    rows = [_row("a", '{"type":"user","message":"hello"}\n', {}), _row("b", content, facts)]
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), str(v / "data" / "entire" / "part-000-00000.parquet"))
    (v / "copied" / "ds").mkdir(parents=True)
    (v / "copied" / "ds" / "rows.jsonl").write_text(copied_line + "\n")
    pq.write_table(pa.table({"text": ["plain words"], "n": [1]}), str(v / "copied" / "ds" / "t.parquet"))
    (v / "README.md").write_text("# card\n")
    (v / "changelog.json").write_text(json.dumps({"version": v.name, "built_at": "2026-10-03T00:00:00Z"}))
    return v


def _config(root: Path) -> Config:
    return Config(data=root / "data", state=root / "state")


def test_a_clean_version_passes(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    v = _version(tmp_path, content='{"type":"user","message":"run the tests"}\n', facts={"kind": "x"},
                 copied_line='{"text":"nothing here"}')
    led = Ledger(tmp_path / "ledger.sqlite")
    got = check.run(cfg, led, v, procs=2, log=lambda m: None)
    assert got["ok"], got
    assert got["rows"] == 2 and got["files"] >= 4
    assert check.passed(led, v)


def test_a_secret_or_a_machine_path_fails(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    machine = str((tmp_path / "data").resolve()) + "/raw/entire/x/full.jsonl"
    v = _version(tmp_path, content='{"type":"user","message":"my key ' + TOKEN + '"}\n',
                 facts={"parse_error": f"cannot read {machine}"}, copied_line='{"text":"token ' + TOKEN[::-1][:4] + TOKEN[4:] + '"}')
    led = Ledger(tmp_path / "ledger.sqlite")
    got = check.run(cfg, led, v, procs=2, log=lambda m: None)
    assert not got["ok"]
    assert got["machine_values"] == 1, got["machine_examples"]
    assert sum(got["findings"].values()) >= 1, got
    assert not check.passed(led, v)


def test_a_secret_in_a_copied_parquet_cell_fails(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    v = _version(tmp_path, content='{"type":"user","message":"ok"}\n', facts={}, copied_line='{"text":"fine"}')
    pq.write_table(pa.table({"text": ["export GITHUB_TOKEN=" + TOKEN], "at": pa.array([1_700_000_000_123_456_789],
                                                                                     pa.int64()).cast(pa.timestamp("ns"))}),
                   str(v / "copied" / "ds" / "t.parquet"))
    led = Ledger(tmp_path / "ledger.sqlite")
    got = check.run(cfg, led, v, procs=2, log=lambda m: None)
    assert not got["ok"] and sum(got["findings"].values()) >= 1, got


def test_a_rebuilt_version_needs_its_own_check(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    v = _version(tmp_path, content='{"type":"user","message":"ok"}\n', facts={}, copied_line='{"text":"fine"}')
    led = Ledger(tmp_path / "ledger.sqlite")
    assert check.run(cfg, led, v, procs=2, log=lambda m: None)["ok"]
    (v / "changelog.json").write_text(json.dumps({"version": v.name, "built_at": "2026-10-03T01:00:00Z"}))
    assert not check.passed(led, v)


def _conversations(v: Path, content: str) -> None:
    from collect import conv_build, events

    rows = events.finish([events.row(role="user", turn_type="user_prompt", content=content, kind="text")],
                         {"source": "entire", "session_key": "entire:a"})
    w = conv_build._Writer(v / "conversations" / "entire" / "part-000.parquet", conv_build.SCHEMA)
    w.add(rows)
    w.close()


def test_a_secret_or_a_key_in_the_conversations_fails(tmp_path: Path) -> None:
    for content, want in (('export API_KEY="Zq8vN3xLw7PkR2tYb5HsQ"', "generic-api-key"),
                          ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n", "private-key (structure)")):
        root = tmp_path / want.split()[0]
        v = _version(root, content='{"type":"user","message":"fine"}\n', facts={}, copied_line='{"text": "plain"}')
        _conversations(v, content)
        got = check.run(_config(root), Ledger(_config(root).ledger), v)
        assert not got["ok"] and any(want in k for k in got["findings"]), got["findings"]


def test_a_secret_only_a_files_decoded_text_shows_fails(tmp_path: Path) -> None:
    line = json.dumps({"type": "user", "message": {"content": 'export API_KEY="Zq8vN3xLw7PkR2tYb5HsQ"'}}) + "\n"
    v = _version(tmp_path, content=line, facts={}, copied_line='{"text": "plain"}')
    got = check.run(_config(tmp_path), Ledger(_config(tmp_path).ledger), v)
    assert not got["ok"] and "generic-api-key" in got["findings"], got["findings"]


def test_a_quoted_secret_in_a_copied_parquet_cell_fails(tmp_path: Path) -> None:
    v = _version(tmp_path, content='{"type":"user","message":"fine"}\n', facts={}, copied_line='{"text": "plain"}')
    pq.write_table(pa.table({"text": ['run: export API_KEY="Zq8vN3xLw7PkR2tYb5HsQ"'], "n": [1]}),
                   str(v / "copied" / "ds" / "t.parquet"))
    got = check.run(_config(tmp_path), Ledger(_config(tmp_path).ledger), v)
    assert not got["ok"] and "generic-api-key" in got["findings"], got["findings"]


def test_an_email_or_a_home_name_left_in_scraped_text_fails(tmp_path: Path) -> None:
    v = _version(tmp_path, content='{"type":"user","message":"mail jane.doe@corp-mail.io from /Users/jane/x"}\n',
                 facts={}, copied_line='{"text": "plain"}')
    got = check.run(_config(tmp_path), Ledger(_config(tmp_path).ledger), v)
    assert not got["ok"] and got["findings"].get("personal data (email or home-path name)") == 2, got["findings"]


def test_a_copied_parquet_file_is_read_in_the_parts_the_build_recorded(tmp_path: Path, monkeypatch) -> None:
    """The build keeps where each part of a copied Parquet file's view began (CopiedStage.view_starts, in the
    ledger); the check reads the published file at those lines, though masking made its cells shorter."""
    if not _gitleaks():
        pytest.skip("gitleaks is not here")
    cfg = _config(tmp_path)
    v = _version(tmp_path, content='{"type":"user","message":"ok"}\n', facts={}, copied_line='{"text":"fine"}')
    led = Ledger(tmp_path / "ledger.sqlite")
    led.set_state("view_starts", v.name, {"copied/ds/t.parquet": {"starts": [0], "lines": 1}})
    seen = []
    real = check.view_parts

    def spy(src, cols, starts=None, lines=None):
        seen.append((Path(src).name, starts, lines))
        return real(src, cols, starts, lines)

    monkeypatch.setattr(check, "view_parts", spy)
    assert check.run(cfg, led, v, procs=1, log=lambda m: None)["ok"]   # in this process, where the spy is
    assert seen == [("t.parquet", [0], 1)], seen
    led.set_state("view_starts", v.name, {"copied/ds/t.parquet": {"starts": [0], "lines": 2}})   # not the build's
    try:
        check.run(cfg, led, v, procs=2, log=lambda m: None)
        raise AssertionError("no refusal")
    except RuntimeError as e:
        assert "the build's" in str(e)


def test_workers_find_what_one_process_finds(tmp_path: Path, monkeypatch) -> None:
    """The check spread over workers, each scanning its file's text a piece at a time, finds what one process finds:
    the same counts, findings and bytes scanned, with every dump scanned alone too (the partner's parallel check of
    2026-10-04; gitleaks reads each dump as a file of its own)."""
    cfg = _config(tmp_path)
    machine = str((tmp_path / "data").resolve()) + "/raw/entire/x/full.jsonl"
    v = _version(tmp_path, content='{"type":"user","message":"my key ' + TOKEN + ', mail jane.doe@corp-mail.io from '
                 '/Users/jane/x"}\n', facts={"parse_error": f"cannot read {machine}"},
                 copied_line='{"text":"token ' + TOKEN + '"}')
    _conversations(v, 'export API_KEY="Zq8vN3xLw7PkR2tYb5HsQ"\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n')
    pq.write_table(pa.table({"text": ["export GITHUB_TOKEN=" + TOKEN], "n": [1]}), str(v / "copied" / "ds" / "t.parquet"))
    led = Ledger(tmp_path / "ledger.sqlite")

    def counts(got: dict) -> dict:
        return {k: got[k] for k in ("rows", "files", "conversation_rows", "scanned_bytes", "machine_values", "findings",
                                    "ok")} | {"examples": len(got["examples"]), "machine_examples": len(got["machine_examples"])}

    one = counts(check.run(cfg, led, v, procs=1, log=lambda m: None))
    many = counts(check.run(cfg, led, v, procs=3, log=lambda m: None))
    monkeypatch.setattr(check, "CHUNK_BYTES", 1)   # each worker scans every dump alone
    pieces = counts(check.run(cfg, led, v, procs=3, log=lambda m: None))
    assert one == many == pieces, (one, many, pieces)
    assert not one["ok"] and one["machine_values"] == 1, one
    assert {"personal data (email or home-path name)", "private-key (structure)", "generic-api-key"} <= set(one["findings"])
    assert not (cfg.data / "tmp" / "check").exists()   # every worker's dumps removed


def test_a_home_path_in_capitals_left_in_scraped_text_fails(tmp_path: Path) -> None:
    """Issue #11: the check's prefilter read `sers` case by case, while HOME_WINDOWS reads `users` in any case."""
    if not _gitleaks():
        pytest.skip("gitleaks is not here")
    cfg = _config(tmp_path)
    v = _version(tmp_path, content='{"type":"user","message":"open C:\\\\USERS\\\\JANEDOE\\\\notes.txt"}\n', facts={},
                 copied_line='{"text": "plain"}')
    got = check.run(cfg, Ledger(tmp_path / "ledger.sqlite"), v, procs=1, log=lambda m: None)
    assert not got["ok"] and got["findings"].get("personal data (email or home-path name)"), got["findings"]


def test_a_large_copied_table_split_among_units_checks_the_same(tmp_path: Path, monkeypatch) -> None:
    """A copied Parquet file's parts shared among units (check._spans, view_span) give the check the same text as the
    file read whole: the same files counted, bytes scanned and findings (2026-10-05)."""
    results = []
    for split in (False, True):
        root = tmp_path / ("split" if split else "whole")
        cfg = _config(root)
        v = _version(root, content='{"type":"user","message":"ok"}\n', facts={}, copied_line='{"text":"fine"}')
        texts = [f"line {i} plain words" for i in range(120)]
        texts[97] = "export GITHUB_TOKEN=" + TOKEN   # near the end: a later span's
        pq.write_table(pa.table({"text": texts, "note": [f"n{i}" for i in range(120)]}),
                       str(v / "copied" / "ds" / "t.parquet"), row_group_size=25)
        led = Ledger(root / "ledger.sqlite")
        led.set_state("view_starts", v.name, {"copied/ds/t.parquet": {"starts": list(range(0, 240, 30)), "lines": 240}})
        monkeypatch.setattr(check, "SPLIT_BYTES", 64 if split else 1 << 40)
        if split:
            assert len(check._spans(v / "copied" / "ds" / "t.parquet", {"starts": list(range(0, 240, 30)), "lines": 240})) > 1
        got = check.run(cfg, led, v, procs=2, log=lambda m: None)
        results.append((got["files"], got["scanned_bytes"], sum(got["findings"].values()), got["ok"]))
    assert results[0] == results[1] and results[0][2] >= 1 and not results[0][3], results


def test_a_check_works_in_a_folder_of_its_own(tmp_path: Path) -> None:
    """Two checks shared one work folder and each removed it, under the other's units, at its start and its end: one
    crashed (2026-10-05). A run removes only its own folder."""
    cfg = _config(tmp_path)
    v = _version(tmp_path, content='{"type":"user","message":"run the tests"}\n', facts={}, copied_line='{"text":"ok"}')
    other = cfg.data / "tmp" / "check-2026-09-30-1"   # another version's run, under way
    (other / "unit-0000001").mkdir(parents=True)
    (other / "unit-0000001" / "dump.txt").write_text("being scanned")
    shared = cfg.data / "tmp" / "check"
    (shared / "unit-0000002").mkdir(parents=True)
    got = check.run(cfg, Ledger(tmp_path / "ledger.sqlite"), v, procs=2, log=lambda m: None)
    assert got["ok"] and (other / "unit-0000001" / "dump.txt").read_text() == "being scanned" and shared.exists()
    assert not list((cfg.data / "tmp").glob(f"check-{v.name}-*"))   # its own, removed
