"""The conversations tables' second scan: views a cell a line, findings mapped to their session, masks put back in
their cells (never in ids), and the split the check reads the same as the build's."""

import json
import tempfile
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from collect import conv_build, events
from collect.scan import MASK

TOKEN = "Zq8vN3xLw7PkR2tYb5Hs"


def rows_for(key: str, n: int) -> list[dict]:
    session = {"source": "entire", "session_key": key, "session_id": key.split(":")[-1], "agent": "x"}
    rows = [events.row(role="user", turn_type="user_prompt", content=f"line {i}", kind="text", actor="developer",
                       typed_by_developer=True, tool_call_id="toolu_01AbCdEfGhIjKlMnOpQrStUv") for i in range(n)]
    return events.finish(rows, session)


def write(path: Path, rows: list[dict]) -> None:
    w = conv_build._Writer(path, conv_build.SCHEMA)
    w.add(rows)
    w.close()


def test_a_cell_a_line_in_parts_of_whole_rows():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        write(src, rows_for("entire:o/r:s1", conv_build.PART_ROWS + 3))
        parts = list(conv_build.view_parts(str(src)))
        assert [len(p) for p in parts] == [conv_build.PART_ROWS * len(conv_build.SCANNED), 3 * len(conv_build.SCANNED)]
        assert parts[0][0] == "line 0" and parts[0][1] == ""   # content as it reads, then tool_input_json (None)
        assert not any("toolu_01" in line or "entire:o/r" in line for p in parts for line in p)   # ids are not scanned


def test_masks_go_back_to_their_cells_and_findings_to_their_session():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "conversations" / "entire" / "part-000.parquet"
        rows = rows_for("entire:o/r:s1", 2) + rows_for("entire:o/r:s2", 2)
        rows[3]["content"] = f'use api_key = "{TOKEN}" here'
        write(src, rows)
        before = pq.read_table(src).to_pylist()
        staged, _ = conv_build.stage_views([str(src)], Path(d) / "views")
        part = staged[0]["path"]
        line_no = next(i for i, line in enumerate(part.read_text().split("\n"), 1) if TOKEN in line)
        assert part.read_text().split("\n")[line_no - 1] == f'use api_key = "{TOKEN}" here'   # its quotes as written
        part.write_text(part.read_text().replace(TOKEN, MASK))   # as the scan masks it
        found = {str(part.resolve()): [{"rule": "generic-api-key", "secret": TOKEN, "line": line_no}]}
        assert list(conv_build.feedback(found, staged)) == ["entire:o/r:s2"]
        dest = Path(d) / "out" / "part-000.parquet"
        assert conv_build.rebuild(str(src), [part], dest) == 1
        got = pq.read_table(dest).to_pylist()
        assert got[3]["content"] == f'use api_key = "{MASK}" here'
        assert (got[3]["word_count"], got[3]["char_count"]) == (len(got[3]["content"].split()), len(got[3]["content"]))
        assert [r["session_key"] for r in got] == [r["session_key"] for r in rows]   # ids untouched
        assert got[:3] == before[:3]


def test_a_file_with_no_mask_is_moved_unchanged():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        write(src, rows_for("entire:o/r:s1", 3))
        before = pq.read_table(src)
        staged, _ = conv_build.stage_views([str(src)], Path(d) / "views")
        dest = Path(d) / "out.parquet"
        assert conv_build.rebuild(str(src), [s["path"] for s in staged], dest) == 0
        assert pq.read_table(dest).equals(before) and not src.exists()


def test_a_secret_fed_back_is_masked_where_the_files_hold_it_escaped():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "full.jsonl"
        f.write_text('{"a": "x"}\n{"b": "https:\\/\\/u:' + TOKEN + '@host"}\n')
        got = conv_build.mask_files([{"rule": "generic-api-key", "secret": "//u:" + TOKEN + "@host", "decoded": True}],
                                    [str(f)])
        assert [(x["line"], x["end_line"]) for x in got[str(f)]] == [(2, 2)]   # the raw file's own line, for redact_lines


def test_a_lone_surrogate_is_written_as_a_replacement_character():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        rows = rows_for("entire:o/r:s1", 1)
        rows[0]["content"] = "cut \ud83d"
        write(src, rows)
        assert pq.read_table(src).column("content").to_pylist() == ["cut �"]
        assert list(conv_build.view_parts(str(src)))[0][0] == "cut �"


KEY = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n"
       "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n-----END RSA PRIVATE KEY-----")


def test_a_cell_reads_back_whatever_it_holds():
    for v in [None, "", "a", "a\\b", "one\ntwo", "cr\rlf\n", "\\e", "\\n as written", 'q"uote', "x\\",
              "tab\tend", "nul\x00z"]:
        line = conv_build.encode(v)
        assert "\n" not in line and "\r" not in line and conv_build.decode(line) == v, v


def test_a_private_key_is_masked_by_its_structure_as_the_view_is_staged():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "conversations" / "entire" / "p.parquet"
        rows = rows_for("entire:o/r:s1", 2)
        rows[1]["content"] = "here is my key\n" + KEY + "\nuse it"
        write(src, rows)
        staged, keys = conv_build.stage_views([str(src)], Path(d) / "views")
        text = staged[0]["path"].read_text()
        assert "PRIVATE KEY" not in text and "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0" not in text
        assert keys == {"private-key": 1}
        dest = Path(d) / "out.parquet"
        assert conv_build.rebuild(str(src), [s["path"] for s in staged], dest) == 1
        got = pq.read_table(dest).column("content").to_pylist()[1]
        assert got.startswith("here is my key\n") and "MIIE" not in got and got.endswith(MASK + "\nuse it")


def test_a_part_that_lost_or_gained_a_line_stops_the_build():
    import pytest

    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        write(src, rows_for("entire:o/r:s1", 2))
        staged, _ = conv_build.stage_views([str(src)], Path(d) / "views")
        part = staged[0]["path"]
        part.write_text(part.read_text().replace("line 1", "line\n1", 1))
        with pytest.raises(RuntimeError, match="lines"):
            conv_build.rebuild(str(src), [part], Path(d) / "out.parquet")


def test_the_scan_finds_quoted_secrets_in_the_view():
    """With gitleaks, as the build runs it: secrets in code whose quotes a JSON line escapes are found in the view."""
    import pytest

    from collect import scan

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    cases = ['export API_KEY="Zq8vN3xLw7PkR2tYb5HsQ"', 'client = OpenAI(api_key="Zq8vN3xLw7PkR2tYb5HsQ")',
             '{"password": "Zq8vN3xLw7PkR2tYb5HsQ"}']
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        rows = rows_for("entire:o/r:s1", len(cases))
        for r, c in zip(rows, cases):
            r["content"] = "run this:\n" + c + "\nthanks"
        write(src, rows)
        staged, _ = conv_build.stage_views([str(src)], Path(d) / "views")
        found = scan.find_all([s["path"] for s in staged], Path(d) / "work", 2)
        lines = sorted({f["line"] for fs in found.values() for f in fs})
        assert lines == [1 + i * len(conv_build.SCANNED) for i in range(len(cases))], lines   # each content cell


def test_a_json_texts_strings_as_they_read_decoded():
    jsonl = '{"type": "user", "message": {"content": "say \\"hi\\"\\nnow"}}\n{"n": 1, "xs": ["a", ""]}\nnot json\n'
    assert list(conv_build.decoded_lines(jsonl)) == ["user", 'say "hi"\\nnow', "a"]   # values, not keys; one a line
    doc = '{\n  "messages": [{"content": "x = \\"y\\""}, {"content": "cut \\ud83d"}]\n}'
    assert list(conv_build.decoded_lines(doc)) == ['x = "y"', "cut \ufffd"]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "full.jsonl"
        p.write_text(jsonl)
        assert list(conv_build.decoded_lines(p)) == list(conv_build.decoded_lines(jsonl))


def test_a_secret_only_its_decoded_text_shows_is_masked_in_the_file():
    """With gitleaks, as the build runs it: a quoted secret in a JSON line, which the scan of the line misses."""
    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "abc.jsonl.txt"
        lines = [json.dumps({"type": "user", "message": {"content": "hello"}}),
                 json.dumps({"type": "user", "message": {"content": f'run: export API_KEY="{TOKEN}Q" now'}})]
        f.write_text("\n".join(lines) + "\n")
        assert not scan.find_all([f], Path(d) / "w0", 2)[str(f.resolve())]   # the line itself: nothing found
        with process_pool(2) as pool:
            masked, found = build._decoded([str(f.resolve())], Path(d), pool, 2, log=lambda *a: None)
        text = f.read_text()
        assert TOKEN not in text and MASK in text and sum(masked.values(), Counter())
        assert found == {str(f.resolve()): {TOKEN + "Q"}}
        assert [json.loads(x)["type"] for x in text.splitlines()] == ["user", "user"]   # every line still reads


CODEX_CALL = {"type": "response_item", "payload": {
    "type": "function_call", "name": "shell", "call_id": "c1",
    "arguments": json.dumps({"command": ["bash", "-lc", 'export API_KEY="' + TOKEN + 'Q" && ./deploy.sh']})}}


def test_a_string_that_is_json_is_read_as_json_too():
    got = list(conv_build.decoded_lines(json.dumps(CODEX_CALL) + "\n"))
    assert 'export API_KEY="' + TOKEN + 'Q" && ./deploy.sh' in got   # the command, its quotes bare
    assert any(line.startswith('{"command": ') for line in got)       # and the arguments whole, keys and all
    deep = json.dumps({"a": json.dumps({"b": json.dumps({"c": json.dumps({"d": json.dumps({"e": "x"})})})})})
    assert "x" not in list(conv_build.decoded_lines(deep + "\n"))      # no deeper than NESTED


def test_a_secret_in_a_tools_arguments_text_is_masked_in_the_file():
    """With gitleaks: Codex's arguments are a JSON string, so its command's quotes are escaped twice in the line."""
    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "codex.jsonl.txt"
        f.write_text(json.dumps(CODEX_CALL) + "\n")
        with process_pool(2) as pool:
            build._decoded([str(f.resolve())], Path(d), pool, 2, log=lambda *a: None)
        line = f.read_text()
        assert TOKEN not in line and json.loads(line)["payload"]["arguments"]   # masked, and still JSON


def test_the_first_round_reads_the_file_as_fetched():
    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    with tempfile.TemporaryDirectory() as d:
        line = json.dumps({"message": {"content": f'export API_KEY="{TOKEN}Q"'}}) + "\n"
        original, staged = Path(d) / "raw.jsonl", Path(d) / "staged.jsonl.txt"
        original.write_text(line)
        staged.write_text(line.replace(TOKEN + "Q", MASK))   # the raw scan masked it already: passed over, not refused
        with process_pool(2) as pool:
            masked, found = build._decoded([str(staged.resolve())], Path(d), pool, 2, log=lambda *a: None,
                                           originals={str(staged.resolve()): str(original)})
        assert found == {str(staged.resolve()): {TOKEN + "Q"}} and not masked


def test_secrets_found_in_a_sessions_files_are_carried_into_its_rows():
    rows = rows_for("openhands-feedback:x:0", 2)
    rows[0]["tool_input_json"] = json.dumps({"command": f'export API_KEY="{TOKEN}Q"'})
    rows[1]["content"] = f"the key is {TOKEN}Q"
    conv_build._carry(rows, [TOKEN + "Q", "short"])
    assert json.loads(rows[0]["tool_input_json"]) == {"command": f'export API_KEY="{MASK}"'}
    assert rows[1]["content"] == f"the key is {MASK}" and rows[1]["char_count"] == len(rows[1]["content"])


def test_the_decoded_pass_goes_in_batches(monkeypatch):
    """Every file is still read when the views are written and scanned a batch at a time."""
    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    monkeypatch.setattr(build, "DECODE_BATCH_BYTES", 10)   # a file a batch
    with tempfile.TemporaryDirectory() as d:
        files = []
        for i in range(3):
            f = Path(d) / f"f{i}.jsonl.txt"
            f.write_text(json.dumps({"content": f'export API_KEY="{TOKEN}{i}Q"'}) + "\n")
            files.append(str(f.resolve()))
        with process_pool(2) as pool:
            _, found = build._decoded(files, Path(d), pool, 2, log=lambda *a: None)
        assert sorted(found) == sorted(files) and all(TOKEN not in Path(f).read_text() for f in files)


def test_a_batch_folder_that_could_not_be_removed_does_not_stop_the_pass(monkeypatch):
    """On a network disk a folder whose file is still held open is not removed; the next batch must not need it gone
    (a full build stopped on it, 2026-10-26)."""
    import types

    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    monkeypatch.setattr(build, "DECODE_BATCH_BYTES", 10)   # a file a batch
    monkeypatch.setattr(build, "shutil", types.SimpleNamespace(rmtree=lambda *a, **k: None))   # nothing is removed
    with tempfile.TemporaryDirectory() as d:
        files = []
        for i in range(3):
            f = Path(d) / f"f{i}.jsonl.txt"
            f.write_text(json.dumps({"content": f'export API_KEY="{TOKEN}{i}Q"'}) + "\n")
            files.append(str(f.resolve()))
        with process_pool(2) as pool:
            _, found = build._decoded(files, Path(d) / "work", pool, 2, log=lambda *a: None)
        assert sorted(found) == sorted(files) and all(TOKEN not in Path(f).read_text() for f in files)


def test_the_second_round_reads_every_staged_file(monkeypatch):
    """A secret that shows only in the staged text, as published, is found though the file as fetched held none."""
    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    with tempfile.TemporaryDirectory() as d:
        original, staged = Path(d) / "raw.jsonl", Path(d) / "staged.jsonl.txt"
        original.write_text(json.dumps({"content": "nothing to see"}) + "\n")
        staged.write_text(json.dumps({"content": f'export API_KEY="{TOKEN}Q"'}) + "\n")
        # STRICT: the second round reads every staged file. By default only the files the first round changed: a
        # secret that only the staged text shows is left (most secrets gone is the bar, 2026-10-04), and the second
        # full read of 180 GB (about 2 hours) is saved.
        monkeypatch.setattr(build, "STRICT", False)
        with process_pool(2) as pool:
            _, found = build._decoded([str(staged.resolve())], Path(d), pool, 2, log=lambda *a: None,
                                      originals={str(staged.resolve()): str(original)})
        assert found == {} and TOKEN in staged.read_text()
        monkeypatch.setattr(build, "STRICT", True)
        with process_pool(2) as pool:
            _, found = build._decoded([str(staged.resolve())], Path(d), pool, 2, log=lambda *a: None,
                                      originals={str(staged.resolve()): str(original)})
        assert found == {str(staged.resolve()): {TOKEN + "Q"}} and TOKEN not in staged.read_text()


def test_a_secret_found_in_one_file_is_masked_in_every_file_that_holds_it():
    """The partner session's probe: the main transcript says `DB_PASSWORD=...` (found), the subagent's transcript and
    another session's file hold the same value bare (not found by any rule)."""
    import shutil as _sh

    import pytest

    from collect import build
    from collect.util import process_pool

    if not _sh.which("grep"):
        pytest.skip("grep is not here")
    secret = "Hx7kQ2vN9mLp4Rt8"
    with tempfile.TemporaryDirectory() as d:
        main = Path(d) / "a.jsonl.txt"
        sub = Path(d) / "b.jsonl.txt"
        other = Path(d) / "c.jsonl.txt"
        main.write_text(json.dumps({"content": f"DB_PASSWORD={secret}"}) + "\n")
        sub.write_text(json.dumps({"content": f"logged in as admin with {secret}"}) + "\n")
        other.write_text(json.dumps({"args": json.dumps({"cmd": f"login --pw {secret}"})}) + "\n")   # nested
        with process_pool(2) as pool:
            masked, held = build._sweep({secret}, [str(p) for p in (main, sub, other)], Path(d) / "w", pool, 2,
                                        log=lambda *a: None)
        for p in (main, sub, other):
            text = p.read_text()
            assert secret not in text and MASK in text, p.name
            json.loads(text)   # still JSON
        assert set(held) == {str(p) for p in (main, sub, other)}


def test_a_text_listed_as_a_secret_is_masked_in_files_and_rows_though_no_rule_finds_it(tmp_path, monkeypatch):
    """A text a version before masked by hand, which no rule finds (an ordinary-looking name, with no key's word near
    it), is listed: the sweep looks for it without strict redaction, masks it in every file that holds it, and the rows
    read from such a file get it masked too (2026-10-09)."""
    import shutil as _sh

    import pytest

    from collect import build, scan
    from collect.util import process_pool

    if not _sh.which("grep"):
        pytest.skip("grep is not here")
    text = "quietOrdinaryName2024x"
    listed = tmp_path / "known_secrets.txt"
    listed.write_text(f"{text}\n\nshort\n")
    monkeypatch.setattr(build, "STRICT", False)
    monkeypatch.delenv(scan.KNOWN_SECRETS_ENV, raising=False)
    assert scan.known_secrets() == frozenset() and build._sweep_literals({"a": {"FoundOnceSecret99"}}) == set()
    monkeypatch.setenv(scan.KNOWN_SECRETS_ENV, str(listed))
    assert scan.known_secrets() == {text, "short"}
    assert build._sweep_literals({"a": {"FoundOnceSecret99"}}) == {text, "short"}
    monkeypatch.setattr(build, "STRICT", True)
    assert build._sweep_literals({"a": {"FoundOnceSecret99"}}) == {text, "short", "FoundOnceSecret99"}
    monkeypatch.setattr(build, "STRICT", False)
    f, other = tmp_path / "a.jsonl.txt", tmp_path / "b.jsonl.txt"
    f.write_text(json.dumps({"content": f"the name {text} again", "args": json.dumps({"x": text})}) + "\n")
    other.write_text(json.dumps({"content": "nothing listed here"}) + "\n")
    with process_pool(2) as pool:
        _, held = build._sweep(build._sweep_literals({}), [str(f), str(other)], tmp_path / "w", pool, 2,
                               log=lambda *a: None)
    assert text not in f.read_text() and f.read_text().count(MASK) == 2 and held == {str(f): {text}}
    json.loads(f.read_text())
    assert other.read_text() == json.dumps({"content": "nothing listed here"}) + "\n"
    rows = rows_for("entire:o/r:s1", 1)
    rows[0]["content"], rows[0]["tool_input_json"] = f"the name {text} again", json.dumps({"x": text})
    conv_build._carry(rows, sorted(held[str(f)]))
    assert rows[0]["content"] == f"the name {MASK} again" and rows[0]["tool_input_json"] == json.dumps({"x": MASK})


def test_a_keys_header_and_end_in_different_cells_mask_neither_other_cell():
    """A `head -1 key.pem` and a `tail -1 key.pem` in two rows of one part: no mask over several cells, no cell after a
    header taken for a key's line (the partner session's review)."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        rows = rows_for("entire:o/r:s1", 4)
        rows[0]["content"] = "-----BEGIN RSA PRIVATE KEY-----"
        rows[1]["content"] = "Done"
        rows[2]["content"] = "Error: port 8080 is in use"
        rows[3]["content"] = "-----END RSA PRIVATE KEY-----"
        write(src, rows)
        staged, _ = conv_build.stage_views([str(src)], Path(d) / "views")
        dest = Path(d) / "out.parquet"
        conv_build.rebuild(str(src), [s["path"] for s in staged], dest)
        got = pq.read_table(dest).column("content").to_pylist()
        assert got[1:3] == ["Done", "Error: port 8080 is in use"] and "PRIVATE KEY" not in got[0]


def test_a_bad_first_line_loses_only_itself():
    text = 'not json at all\n{"content": "export API_KEY=\\"x\\""}\n{"a": "b"}\n'
    unread = []
    got = list(conv_build.decoded_lines(text, unread))
    assert got == ['export API_KEY="x"', "b"] and unread == [1]


def test_scraped_rows_are_masked_for_personal_data_and_copied_ones_are_not():
    def one(source: str) -> list[dict]:
        rows = rows_for(f"{source}:s", 1)
        rows[0]["content"] = "mail jane.doe@corp-mail.io from /Users/jane/x"
        if source not in conv_build.COPIED:
            conv_build._personal(rows)
        return rows
    assert one("entire")[0]["content"] == f"mail {MASK} from /Users/{MASK}/x"
    assert "jane.doe@corp-mail.io" in one("dataclaw")[0]["content"]


def test_a_keys_second_half_in_a_cell_is_masked_and_the_sweep_leaves_no_patterns():
    from collect import build
    from collect.util import process_pool

    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        rows = rows_for("entire:o/r:s1", 2)
        rows[1]["content"] = ("VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n"
                              "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n-----END RSA PRIVATE KEY-----")
        write(src, rows)
        staged, keys = conv_build.stage_views([str(src)], Path(d) / "views")
        text = staged[0]["path"].read_text()
        assert "VTLw7onL" not in text and keys == {"private-key": 1}
        f = Path(d) / "a.jsonl.txt"
        f.write_text(json.dumps({"content": "pw Hx7kQ2vN9mLp4Rt8"}) + "\n")
        with process_pool(2) as pool:
            build._sweep({"Hx7kQ2vN9mLp4Rt8"}, [str(f)], Path(d) / "w", pool, 2, log=lambda *a: None)
        assert not (Path(d) / "w" / "sweep-patterns.txt").exists() and "Hx7kQ2vN9mLp4Rt8" not in f.read_text()


def test_a_secret_found_many_times_is_looked_for_once(tmp_path):
    """gitleaks reports every occurrence of a secret; each is a finding, and the decoded pass passes them all. Looked
    for once per finding, a file was read once for each (review of rc7): one held finding per line holding it."""
    secret = "tok_" + "Ab3" * 9
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps({"i": i, "text": f"key={secret}" if i % 10 == 0 else "nothing"}) + "\n"
                         for i in range(100)))
    f = {"rule": "generic-api-key", "secret": secret, "line": 1, "end_line": 1, "match": f"key={secret}", "decoded": True}
    held = conv_build.mask_files([{**f, "line": n, "end_line": n} for n in range(1, 51)], [str(p)])[str(p)]   # one per occurrence
    assert sorted(h["line"] for h in held) == list(range(1, 101, 10))


def test_a_secret_the_decoded_text_shows_but_no_line_holds_stops_the_build(monkeypatch):
    """The decoded pass places each secret it finds in the staged file's lines; one placed in none stops the build
    (a file not read as fetched, so not one the raw scan masked already)."""
    import pytest

    from collect import build, scan

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")

    class Serial:   # the pool's map in this process, so the stand-in below is the one called
        def map(self, fn, items, chunksize=1):
            return list(map(fn, items))

    monkeypatch.setattr(build.conv_build, "mask_files", lambda fs, paths: {})
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "codex.jsonl.txt"
        f.write_text(json.dumps(CODEX_CALL) + "\n")
        monkeypatch.setattr(build, "STRICT", True)
        with pytest.raises(RuntimeError, match="cannot be placed"):
            build._decoded([str(f.resolve())], Path(d), Serial(), 2, log=lambda *a: None)
        # Most secrets gone is the bar (2026-10-04): by default the file's secret is left where it is, and counted.
        monkeypatch.setattr(build, "STRICT", False)
        said = []
        masked, _ = build._decoded([str(f.resolve())], Path(d), Serial(), 2, log=said.append)
        assert masked == {} and any("could not be placed, left" in m for m in said), said



def test_a_line_that_is_no_json_value_still_reads_decoded_by_its_strings(monkeypatch, tmp_path):
    """A record cut over two lines, and a pretty-printed document too large to read whole, read a line at a time: each
    line's string literals read as JSON. A 760 MB export of records over several lines stopped the full build of
    2026-10-04 when a document over 512 MB was refused."""
    cut = ('{"type": "user", "content": "run it with \\"KEY=Zq8vN3xLw7PkR2tYb5\\" set", "more": "a string that\n'
           'goes on", "n": 1}\n')
    unread = []
    got = list(conv_build.decoded_lines(cut, unread))
    assert 'run it with "KEY=Zq8vN3xLw7PkR2tYb5" set' in got and unread == [2], (got, unread)
    doc = tmp_path / "pretty.json"
    doc.write_text(json.dumps({"cmd": 'export TOKEN="Zq8vN3xLw7PkR2tYb5"', "list": [{"x": "y"}]}, indent=2))
    monkeypatch.setattr(conv_build, "WHOLE_BYTES", 10)   # as if over 512 MB: never read whole
    unread = []
    got = list(conv_build.decoded_lines(doc, unread))
    assert 'export TOKEN="Zq8vN3xLw7PkR2tYb5"' in got and unread and sum(unread) >= 3, (got, unread)



def test_a_lines_literals_are_found_in_linear_time():
    """The review of rc10: a line ending inside an open string, full of escaped quotes, was read from each quote again.
    In a process of its own, so a hang fails the test rather than stopping the suite."""
    import os
    import subprocess
    import sys

    code = r"""
import time
from collect import conv_build
line = b'{"a": "start ' + (b"x" * 90 + b'\\"') * 16000   # 1.5 MB of escaped quotes; its last string never closed
t = time.perf_counter()
got = list(conv_build.decoded_lines(line.decode() + "\n" + '{"b": "ok"}'))
assert got == ["a", "ok"], got[-3:]   # the open string dropped; the next line's value read
assert list(conv_build._literals(b'{"k": "v\\"w", "n": "x"}')) == [b'"k"', b'"v\\"w"', b'"n"', b'"x"']
print(round(time.perf_counter() - t, 3))
"""
    root = Path(__file__).resolve().parents[1]
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)})
    assert got.returncode == 0 and float(got.stdout) < 5, (got.stdout, got.stderr[-800:])


def test_a_secret_gitleaks_decoded_from_a_base64_run_is_held_where_the_run_is(tmp_path):
    """rc11's full build stopped in its decoded pass: gitleaks decoded a secret from a base64 run in a copied view
    part, and mask_files offered place() no line, since the escape-tolerant pattern cannot read base64."""
    import base64

    from collect import scan

    secret = "Zq8vN3xLw7PkR2tYb5HsQd1c"
    blob = base64.b64encode(json.dumps({"api_key": secret}).encode()).decode()
    p = tmp_path / "part.view.00000.jsonl"
    p.write_text("\n".join(json.dumps({"i": i, "data": blob if i == 3 else "plain"}) for i in range(6)) + "\n")
    f = {"rule": "generic-api-key", "secret": secret, "line": 1, "end_line": 1, "match": f"api_key={secret}",
         "decoded": True}
    assert conv_build.mask_files([f], [str(p)]) == {}   # gitleaks' own decoding not kept: no line offered
    held = conv_build.mask_files([{**f, "gitleaks_decoded": True}], [str(p)])[str(p)]
    assert [h["line"] for h in held] == [4]
    out, _ = scan.redact(p.read_text(), held, pii=False, json_like=True)
    assert blob not in out and secret not in out and json.loads(out.splitlines()[3])["data"] == scan.MASK


def test_a_plain_run_is_looked_for_first_and_escapes_still_find_the_secret(tmp_path):
    """The exact prefilter of issue #13: a line without `%` or `\\u` must hold the secret's plain run; a secret written
    wholly in percent or `\\u` escapes is still found where those escapes are."""
    secret = "tok_Ab3Cd4Ef5Gh6Ij7"
    pct = "".join(f"%{ord(c):02X}" for c in secret)
    uni = "".join(f"\\u{ord(c):04x}" for c in secret)   # JSON's own escape: one backslash, then u00XX
    uni2 = "".join(f"\\\\u{ord(c):04x}" for c in secret)   # an escaped backslash, then u00XX: JSON that holds JSON
    lines = [json.dumps({"t": f"key={secret}"}), json.dumps({"t": "nothing here at all"}), f'{{"t": "q={pct}"}}',
             f'{{"t": "{uni2}"}}', f'{{"t": "{uni}"}}']
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n")
    f = {"rule": "generic-api-key", "secret": secret, "line": 1, "end_line": 1, "match": f"key={secret}", "decoded": True}
    held = conv_build.mask_files([f], [str(p)])[str(p)]
    assert sorted(h["line"] for h in held) == [1, 3, 4, 5], held


def test_the_decoded_pass_masks_a_secret_gitleaks_decoded_from_base64_rather_than_stop():
    """End to end, with gitleaks: a JSON line holding base64 of a keyed secret. The decoded pass keeps gitleaks' own
    decoding in the finding, so the file's line is offered to place() and the run masked (rc11 stopped instead)."""
    import base64

    import pytest

    from collect import build, scan
    from collect.util import process_pool

    try:
        scan.gitleaks_path()
    except Exception:
        pytest.skip("gitleaks is not here")
    blob = base64.b64encode(b'{"api_key": "Zq8vN3xLw7PkR2tYb5HsQd1cWx9"}').decode()
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "part.view.00000.jsonl.txt"
        f.write_text(json.dumps({"data": blob}) + "\n" + json.dumps({"data": "plain"}) + "\n")
        with process_pool(2) as pool:
            build._decoded([str(f.resolve())], Path(d), pool, 2, log=lambda *a: None)
        assert blob not in f.read_text() and json.loads(f.read_text().splitlines()[1])["data"] == "plain"


def test_feedback_keeps_gitleaks_own_decoding():
    rows = rows_for("entire:o/r:s1", 1)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "c.parquet"
        write(src, rows)
        staged, _ = conv_build.stage_views([str(src)], Path(d) / "views")
        path = str(staged[0]["path"].resolve())
        f = {"rule": "generic-api-key", "secret": "Zq8vN3xLw7PkR2tYb5", "line": 1, "decoded": True}
        got = conv_build.feedback({path: [f]}, staged)
        assert [x["gitleaks_decoded"] for xs in got.values() for x in xs] == [True], got


def test_a_slash_written_escaped_is_found_past_the_prefilter(tmp_path):
    """The partner's review of rc12: a slash has a JSON escape (`\\/`), so it is never in the plain run."""
    secret = "tok_Ab3Cd4/Ef5Gh6Ij7Kl8"
    p = tmp_path / "s.jsonl"
    p.write_text('{"t": "key=' + secret.replace("/", "\\/") + '"}\n')   # PHP's and JavaScript's escaped slash
    f = {"rule": "generic-api-key", "secret": secret, "line": 1, "end_line": 1, "match": f"key={secret}", "decoded": True}
    assert conv_build.mask_files([f], [str(p)])[str(p)]


def test_findings_of_one_secret_look_in_encoded_runs_if_any_was_decoded(tmp_path):
    """Merged by scan._key, which leaves gitleaks' own decoding out: the first finding of a secret, read as written,
    must not take away the encoded look a later one, decoded by gitleaks, needs."""
    import base64

    secret = "Zq8vN3xLw7PkR2tYb5HsQd1c"
    blob = base64.b64encode(json.dumps({"api_key": secret}).encode()).decode()
    p = tmp_path / "part.jsonl"
    p.write_text(json.dumps({"data": blob}) + "\n")
    plain = {"rule": "generic-api-key", "secret": secret, "line": 1, "end_line": 1, "decoded": True,
             "gitleaks_decoded": False}
    held = conv_build.mask_files([plain, {**plain, "gitleaks_decoded": True}], [str(p)])
    assert [h["line"] for h in held[str(p)]] == [1]


def _old_carry(rows, secrets):
    """_carry as rc12 had it: every secret's pattern on every value, in order."""
    from collect import scan

    probes = [scan.escape_tolerant(s) for s in secrets if len(s) >= 6]
    if not probes:
        return
    for r in rows:
        changed = False
        for c in conv_build.SCANNED:
            v = r.get(c)
            if v:
                for rx in probes:
                    v, n = rx.subn(MASK, v)
                    changed |= bool(n)
                r[c] = v
        if changed:
            text = r.get("content") or ""
            r["word_count"], r["char_count"] = len(text.split()), len(text)


def _forms(s: str, rnd) -> list[str]:
    """Ways a text may hold a secret that escape_tolerant reads: as written, each or one character percent-encoded
    (at depth), as `\\uXXXX`, JSON-escaped once or twice, a stray `%` before it."""
    out = [s, json.dumps(s)[1:-1], json.dumps(json.dumps(s)[1:-1])[1:-1]]
    if s.isascii():
        out += ["".join(f"%{ord(c):02X}" for c in s), "".join(f"%25{ord(c):02x}" for c in s),
                "".join(f"\\u{ord(c):04x}" for c in s)]
        k = rnd.randrange(len(s))
        if s[k] != "\\":
            out += [s[:k] + f"%{ord(s[k]):02X}" + s[k + 1:], s[:k] + f"\\u{ord(s[k]):04X}" + s[k + 1:],
                    "%" + s[:k] + f"%{ord(s[k]):02x}" + s[k + 1:]]
    return out


def _secrets(rnd, n: int) -> list[str]:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    out = ["4142cdefghij", "ab\\cdefghijk", "zz[REDACTED]", "a!b@c#d$e%", 'k"ey:va/lue+12=', "REDACTEDx",
           "ef12_Ab.cd-9"]
    while len(out) < n:
        s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(6, 30)))
        if rnd.random() < 0.3:
            k = rnd.randrange(len(s))
            s = s[:k] + rnd.choice('/+=:@!\\"') + s[k:]
        out.append(s)
    return out


def _rows(rnd, secrets: list[str], n: int) -> list[dict]:
    filler = ["printf %s", "100% done", "a%20b", "C:\\dir\\x", "line\\nnext", "50%41", "%", "\\", "x" * 40,
              "abcdefgh", "zz", "%5C", "u0063", "%2541", "REDACTED"]
    rows = []
    for _ in range(n):
        r = {}
        for c in conv_build.SCANNED:
            parts = [rnd.choice(filler) for _ in range(rnd.randint(0, 4))]
            for _ in range(rnd.randint(0, 2)):
                parts.insert(rnd.randint(0, len(parts)), rnd.choice(_forms(rnd.choice(secrets), rnd)))
            r[c] = "".join(parts) if parts or rnd.random() < 0.5 else None
        rows.append(r)
    rows.append({"content": "zzabcdefghij", "command": "ab%5Cu0063defghijk", "file_path": "%4142%63defghij"})
    return rows


def test_carry_masks_exactly_what_every_pattern_on_every_value_masked():
    """The candidates _carry tries (a value with no escape of a run's characters must hold the run as written; runs
    found through an index of first characters when the secrets are many) lose nothing against trying every
    pattern on every value, in the order given, on values made to hold secrets in every form escape_tolerant
    reads, beside text that is almost them."""
    import copy
    import random

    rnd = random.Random(20261004)
    for n_secrets in (12, 300):
        for _ in range(4):
            secrets = _secrets(rnd, n_secrets)
            planted = (["abcdefghij"] if n_secrets == 12 else []) + secrets
            rows = _rows(rnd, planted[:40], 60)
            order = planted[:]
            rnd.shuffle(order)
            old, new = copy.deepcopy(rows), copy.deepcopy(rows)
            _old_carry(old, order)
            conv_build._carry(new, order)
            assert new == old
            assert any(MASK in (r.get(c) or "") for r in new for c in conv_build.SCANNED)


def test_carry_with_thousands_of_secrets_takes_a_moment(tmp_path):
    """A session of a large export carries thousands of secrets: rc12 compiled and ran every one on every value."""
    import os
    import subprocess
    import sys

    code = r"""
import random, time
from collect import conv_build
rnd = random.Random(7)
abc = "abcdefghijklmnopqrstuvwxyz0123456789"
secrets = sorted({"".join(rnd.choice(abc) for _ in range(24)) for _ in range(3000)})
rows = [{"content": " ".join("".join(rnd.choice(abc) for _ in range(7)) for _ in range(500)) + " printf %s 100%"}
        for _ in range(1000)]
rows[5]["content"] += " " + secrets[17]
t = time.perf_counter()
cache = {"plans": {}, "patterns": {}}
for k in range(0, 1000, 100):   # ten sessions of one export
    conv_build._carry(rows[k:k + 100], secrets, cache)
assert "[REDACTED]" in rows[5]["content"]
print(round(time.perf_counter() - t, 2))
"""
    root = Path(__file__).resolve().parents[1]
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)})
    assert got.returncode == 0 and float(got.stdout) < 6, (got.stdout, got.stderr[-800:])


def test_feedback_is_placed_a_file_at_a_time_with_every_sessions_findings(tmp_path: Path) -> None:
    """The conversations' findings, gathered by file: each file gets every finding of every session that reads it,
    once; an export shared by its sessions is one job, not one per session."""
    from collect.build import _by_file

    export, own, small = tmp_path / "export.jsonl.txt", tmp_path / "own.jsonl.txt", tmp_path / "small.txt"
    export.write_text("x" * 300)
    own.write_text("x" * 200)
    small.write_text("x")
    a, b, c = ({"rule": "r", "secret": f"secret-{k}-0123"} for k in "abc")
    back = {"dataclaw:e:1": [a], "dataclaw:e:2": [b], "entire:s": [c]}
    files_of = {"dataclaw:e:1": [str(export)], "dataclaw:e:2": [str(export), str(export)],
                "entire:s": [str(small), str(own)]}
    got = _by_file(back, files_of)
    assert [p for p, _ in got] == [str(export), str(own), str(small)]
    assert dict(got) == {str(export): [a, b], str(own): [c], str(small): [c]}


def test_each_session_carries_the_secrets_of_its_own_files_and_an_exports_share_one_list():
    from collect.build import _secrets_of

    stage_of = {"a" * 64: "/s/a.jsonl.txt", "b" * 64: "/s/b.jsonl.txt"}
    staged_of_copy = {"/dl/export.jsonl": ["/s/copied/e.jsonl.txt"], "/dl/t.parquet": ["/s/v.00000.jsonl", "/s/v.00001.jsonl"]}
    decoded = {"/s/a.jsonl.txt": {"secret-in-a-0001"}, "/s/b.jsonl.txt": {"secret-in-b-0002"},
               "/s/copied/e.jsonl.txt": {"secret-in-e-0003", "secret-in-e-0004"},
               "/s/v.00000.jsonl": {"secret-in-v-0005"}, "/s/v.00001.jsonl": {"secret-in-v-0006"}}
    known: dict = {}
    of = lambda r: _secrets_of(r, staged_of_copy, stage_of, decoded, known)
    a, b = {"files": [{"sha256": "a" * 64}]}, {"files": [{"sha256": "b" * 64}]}
    e1, e2 = ({"_read": [(f"export.jsonl:{n}", "/dl/export.jsonl")]} for n in (1, 2))
    t = {"_read": [("t.parquet:3", "/dl/t.parquet")]}
    assert of(a) == ["secret-in-a-0001"] and of(b) == ["secret-in-b-0002"]
    assert of(e1) == ["secret-in-e-0003", "secret-in-e-0004"] and of(e1) is of(e2)
    assert of(t) == ["secret-in-v-0005", "secret-in-v-0006"]
    assert of({"files": []}) == [] and of({"_read": []}) == []


def test_carry_reads_a_percent_escape_nested_four_deep():
    """The partner's review of rc13: escape_tolerant reads `%XX` under up to four `%25`s, so _RUN_ESCAPE must too."""
    secret = "tok_Ab3Cd4Ef5Gh6"
    v = "x=%25252525" + f"{ord('t'):02X}" + secret[1:]   # its first letter, percent-encoded four levels deep
    rows = [{c: (v if c == "content" else None) for c in conv_build.SCANNED}]
    conv_build._carry(rows, [secret])
    assert "[REDACTED]" in rows[0]["content"]


def test_a_secret_that_cannot_be_placed_is_left_and_the_rest_are_masked(tmp_path, monkeypatch):
    """By default (not STRICT) a finding that cannot be placed is left where it is, counted, and the file's other
    findings are masked; a file in which nothing could be masked is not scanned again (it would give the same)."""
    import pytest

    from collect import build

    p = tmp_path / "s.jsonl.txt"
    good = "tok_Ab3Cd4Ef5Gh6Ij7Kl8"
    p.write_text(json.dumps({"t": f"key={good}"}) + "\n")
    placed = {"rule": "generic-api-key", "secret": good, "line": 1, "end_line": 1, "match": f"key={good}"}
    nowhere = {"rule": "generic-api-key", "secret": "nowhere_Zq8vN3xLw7Pk", "line": 1, "end_line": 1,
               "match": "key=nowhere_Zq8vN3xLw7Pk"}
    real = build.scan.redact

    def redact(text, findings, **kw):   # redact's last resort places most findings: this one it cannot
        if any(f["secret"] == nowhere["secret"] for f in findings):
            raise RuntimeError("redact: a generic-api-key finding (line 1) cannot be placed in the text")
        return real(text, findings, **kw)

    monkeypatch.setattr(build.scan, "redact", redact)
    monkeypatch.setattr(build, "STRICT", True)
    with pytest.raises(RuntimeError):
        build._mask((str(p), [placed, nowhere]))
    assert good in p.read_text()   # a failed masking leaves the file as it was
    monkeypatch.setattr(build, "STRICT", False)
    staged, counts = build._mask((str(p), [placed, nowhere]))
    assert counts["left"] == 1 and sum(counts["secrets"].values()) == 1
    assert good not in p.read_text() and MASK in p.read_text()

    class Serial:
        def map(self, fn, items, chunksize=1):
            return list(map(fn, items))

    calls = []
    monkeypatch.setattr(build.scan, "find_all",
                        lambda todo, *a, **k: calls.append(list(todo)) or {str(t): [nowhere] for t in todo})
    said = []
    assert build.until_clean([p], tmp_path / "scan", Serial(), 1, log=said.append) == {str(p): {}}
    assert len(calls) == 1 and any("could not be placed" in m for m in said), (calls, said)
    monkeypatch.setattr(build, "STRICT", True)
    with pytest.raises(RuntimeError):
        build.until_clean([p], tmp_path / "scan", Serial(), 1, log=lambda m: None)
