"""collect.names: full names found in typed messages, masked wherever they occur in the same session."""

import json
import re

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import names

FILES = pa.list_(pa.struct([("path", pa.string()), ("bytes", pa.int64()), ("sha256", pa.string()),
                            ("content", pa.large_string())]))


def finder(text):
    """A stand-in for Presidio: two real names, a code span it wrongly calls a person, and a low-score span."""
    out = []
    for span, score in (("Harriet Quill", 0.85), ("Ludo Courtè", 0.85), ("dict[str]", 0.85), ("Weak Match", 0.5)):
        i = text.find(span)
        if i >= 0:
            out.append((i, i + len(span), score))
    return out


@pytest.fixture
def tier(tmp_path):
    t = tmp_path / "a"
    (t / "conversations" / "entire").mkdir(parents=True)
    (t / "data" / "entire").mkdir(parents=True)
    rows = [
        {"session_key": "s1", "turn_number": 0, "typed_by_developer": True,
         "content": "Ask Harriet Quill about dict[str] and Weak Match"},
        {"session_key": "s1", "turn_number": 1, "typed_by_developer": False,
         "content": "Sure, I will message Harriet Quill. (Harriet Quillberg is someone else.)"},
        {"session_key": "s2", "turn_number": 0, "typed_by_developer": False,
         "content": "Authors: Rico Sennrich and Barry Haddow"},   # an agent's text only: left
        {"session_key": "s3", "turn_number": 0, "typed_by_developer": True, "content": "thanks Ludo Courtè"},
    ]
    pq.write_table(pa.Table.from_pylist(rows), t / "conversations" / "entire" / "0000.parquet")
    raw = json.dumps({"message": "hi\nHarriet Quill said so"}) + "\n" + json.dumps({"m": "Ludo Courtè"}) + "\n"
    sess = [{"session_key": "s1", "files": [{"path": "full.jsonl", "bytes": 1, "sha256": "x", "content": raw}]},
            {"session_key": "s2", "files": [{"path": "full.jsonl", "bytes": 1, "sha256": "y",
                                             "content": "Rico Sennrich"}]}]
    pq.write_table(pa.Table.from_pylist(sess, schema=pa.schema([("session_key", pa.string()), ("files", FILES)])),
                   t / "data" / "entire" / "0000.parquet")
    (t / "changelog.json").write_text(json.dumps({"tier": "A"}))
    (t / "copied").mkdir()
    (t / "copied" / "note.txt").write_text("untouched")
    return t


def read(path):
    return pq.read_table(path).to_pylist()


def test_names_from_typed_messages_masked_across_the_session(tier, tmp_path):
    stats = names.write(tier, tmp_path / "out", finder=finder, log=lambda m: None)
    rows = read(tmp_path / "out" / "conversations" / "entire" / "0000.parquet")
    assert rows[0]["content"] == "Ask [REDACTED] about dict[str] and Weak Match"   # code and low scores left
    assert rows[1]["content"] == "Sure, I will message [REDACTED]. (Harriet Quillberg is someone else.)"
    assert rows[2]["content"] == "Authors: Rico Sennrich and Barry Haddow"   # found only in an agent's text: left
    assert stats["sessions"] == 2 and stats["names"] == 2


def test_raw_files_masked_too_json_escapes_included(tier, tmp_path):
    names.write(tier, tmp_path / "out", finder=finder, log=lambda m: None)
    sess = {r["session_key"]: r for r in read(tmp_path / "out" / "data" / "entire" / "0000.parquet")}
    raw = sess["s1"]["files"][0]["content"]
    assert "Harriet Quill" not in raw and json.loads(raw.splitlines()[0])["message"] == "hi\n[REDACTED] said so"
    assert sess["s2"]["files"][0]["content"] == "Rico Sennrich"


def test_non_ascii_name_masked_in_its_json_escaped_form(tier, tmp_path):
    (tier / "data" / "entire" / "0000.parquet").unlink()
    sess = [{"session_key": "s3", "files": [{"path": "f", "bytes": 1, "sha256": "z",
                                             "content": json.dumps({"m": "Ludo Courtè"})}]}]
    pq.write_table(pa.Table.from_pylist(sess, schema=pa.schema([("session_key", pa.string()), ("files", FILES)])),
                   tier / "data" / "entire" / "0000.parquet")
    names.write(tier, tmp_path / "out", finder=finder, log=lambda m: None)
    raw = read(tmp_path / "out" / "data" / "entire" / "0000.parquet")[0]["files"][0]["content"]
    assert "Court" not in raw and json.loads(raw)["m"] == "[REDACTED]"


def test_other_files_copied_and_changelog_notes_the_stage(tier, tmp_path):
    names.write(tier, tmp_path / "out", finder=finder, log=lambda m: None)
    assert (tmp_path / "out" / "copied" / "note.txt").read_text() == "untouched"
    c = json.loads((tmp_path / "out" / "changelog.json").read_text())
    assert c["tier"] == "A" and c["names_masked"]["sessions"] == 2


def test_refuses_a_folder_already_written(tier, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    with pytest.raises(FileExistsError):
        names.write(tier, tmp_path / "out", finder=finder, log=lambda m: None)


@pytest.mark.parametrize("span,ok", [("Harriet Quill", True), ("Per Arne Lysvold", True), ("O'Brien", False),
                                     ("dict[str]", False), ("amdgpu", False), ("Jean-Francois Puget", True),
                                     ("Swift", False), ("MAX VALUE", False), ("Harriet Quill\n", False)])
def test_full_name_shape(span, ok):
    assert bool(names.FULL_NAME.fullmatch(span)) is ok


def test_a_span_ending_in_a_newline_is_the_name_without_it():
    found = names.names_in("met Harriet Quill\nyesterday", lambda text: [(4, 18, 0.9)])   # the span takes the newline
    assert found == {"Harriet Quill"}
    pattern = names._pattern(found)
    assert names.mask("Harriet Quill, and later Harriet Quill\n", pattern) == ("[REDACTED], and later [REDACTED]\n", 2)


def test_a_name_written_against_a_masked_one_is_masked_too():
    # a pattern whose first pass leaves a match that the replacement frees: masked again until nothing matches
    pattern = re.compile(r"a(?!b)|b")   # "a" stands alone only once the "b" after it is masked
    assert names.mask("ab x", pattern) == ("[REDACTED][REDACTED] x", 2)


def test_a_long_message_is_read_in_pieces_and_its_names_still_found():
    seen = []

    def strict(text):   # like spaCy: refuses a text over the limit
        assert len(text) <= names.CHUNK
        seen.append(len(text))
        return finder(text)

    text = ("log line\n" * 12_000) + "ping Harriet Quill\n" + ("x" * 120_000)
    assert names.names_in(text, strict) == {"Harriet Quill"}
    assert len(seen) >= 4 and sum(seen) == len(text)


def test_deny_list_and_the_names_found_kept_beside_the_release(tier, tmp_path):
    stats = names.write(tier, tmp_path / "out", finder=finder, log=lambda m: None, deny={"Ludo Courtè"})
    side = json.loads((tmp_path / "out.names.json").read_text())
    assert side == {"s1": ["Harriet Quill"]} and stats["names"] == 1
    assert not any(p.name.endswith(".names.json") for p in (tmp_path / "out").rglob("*"))   # never in the release
    rows = read(tmp_path / "out" / "conversations" / "entire" / "0000.parquet")
    assert rows[3]["content"] == "thanks Ludo Courtè"   # denied: left as written


def test_deny_file_reads_one_term_a_line(tmp_path):
    f = tmp_path / "deny.txt"
    f.write_text("# products\nClaude Code\n\n  Docker Compose \n")
    assert names.deny_list(f) == {"Claude Code", "Docker Compose"}


@pytest.mark.parametrize("span,technical", [("Claude Code", True), ("Docker Desktop", True), ("Qwen-Image-Edit", True),
                                            ("Jupyter Kernel", True), ("Kanya Rashifal", True), ("Harriet Quill", False),
                                            ("Lan Anh Trinh", False), ("Clara Whitlow", False)])
def test_technical_words_are_not_names(span, technical):
    assert names.looks_technical(span) is technical
    assert names.names_in(span, lambda t: [(0, len(t), 0.9)]) == (set() if technical else {span})


# The copied datasets keep their sessions' raw text in their own files: each session's names are masked in its record.
def copied_tier(tmp_path):
    t = tmp_path / "a"
    d = t / "copied" / "dataclaw" / "u__d"
    d.mkdir(parents=True)
    other = json.dumps({"messages": [{"role": "user", "content": "Harriet Quill, again"}]})
    mine = json.dumps({"messages": [{"role": "user", "content": "ask Harriet Quill and Ludo Courtè"}]})
    (d / "conversations.jsonl").write_bytes((other + "\n" + mine + "\n").encode())
    (d / "sessions").mkdir()
    (d / "sessions" / "x.jsonl").write_text('{"m": "Ludo Courtè"}\n{"m": "bye Ludo Courtè"}\n')
    o = t / "copied" / "openhands-feedback" / "O__f" / "data"
    o.mkdir(parents=True)
    traj = pa.list_(pa.struct([("source", pa.string()), ("message", pa.string())]))
    pq.write_table(pa.table({"trajectory": pa.array([[{"source": "user", "message": "Harriet Quill here"}],
                                                     [{"source": "user", "message": "hi, Harriet Quill"}]], traj),
                             "feedback": ["positive", "negative"]}), o / "t.parquet")
    found = {"dataclaw:u/d:conversations.jsonl:2": {"Harriet Quill", "Ludo Courtè"},
             "dataclaw:u/d:sessions/x.jsonl": {"Ludo Courtè"},
             "openhands-feedback:O/f:data/t.parquet:1": {"Harriet Quill"},
             "entire:o/r:s1": {"Harriet Quill"}}
    return t, found


def credit_note(out):
    out.mkdir(parents=True, exist_ok=True)
    end = "2 secret spans redacted, nothing else changed."
    (out / "changelog.json").write_text(json.dumps({"copied_datasets": {
        "dataclaw/u__d": {"credit": "Copied from u/d; " + end},
        "openhands-feedback": {"credit": "Copied from O/f; " + end}}, "names_masked": {"sessions": 4}}))


def test_copied_sessions_names_masked_in_their_own_record_only(tmp_path):
    t, found = copied_tier(tmp_path)
    out = tmp_path / "out"
    credit_note(out)
    stats = names.mask_copied(t, out, found, log=lambda m: None)
    lines = (out / "copied" / "dataclaw" / "u__d" / "conversations.jsonl").read_text().splitlines()
    assert "Harriet Quill, again" in lines[0]                      # another session's record: left
    assert json.loads(lines[1])["messages"][0]["content"] == "ask [REDACTED] and [REDACTED]"
    assert (out / "copied" / "dataclaw" / "u__d" / "sessions" / "x.jsonl").read_text() == (
        '{"m": "[REDACTED]"}\n{"m": "bye [REDACTED]"}\n')      # a whole transcript: every line
    rows = pq.read_table(out / "copied" / "openhands-feedback" / "O__f" / "data" / "t.parquet").to_pylist()
    assert rows[0]["trajectory"][0]["message"] == "Harriet Quill here"
    assert rows[1]["trajectory"][0]["message"] == "hi, [REDACTED]" and rows[1]["feedback"] == "negative"
    assert stats == {"files": 3, "sessions": 3, "occurrences": 5}
    c = json.loads((out / "changelog.json").read_text())
    assert c["copied_datasets"]["dataclaw/u__d"]["credit"] == (
        "Copied from u/d; 2 secret spans redacted; people's full names masked in 2 of its sessions (4 occurrences); "
        "nothing else changed.")
    assert c["copied_datasets"]["openhands-feedback"]["names_masked"] == {"sessions": 1, "occurrences": 1}
    assert c["names_masked"]["copied_files"] == stats


def test_copied_masking_run_again_gives_the_same_files(tmp_path):
    t, found = copied_tier(tmp_path)
    out = tmp_path / "out"
    credit_note(out)
    names.mask_copied(t, out, found, log=lambda m: None)
    first = {p: p.read_bytes() for p in (out / "copied").rglob("*") if p.is_file()}
    names.mask_copied(t, out, found, log=lambda m: None)
    assert {p: p.read_bytes() for p in (out / "copied").rglob("*") if p.is_file()} == first


def test_the_stage_masks_copied_files_from_the_names_it_finds(tmp_path):
    t, _ = copied_tier(tmp_path)
    (t / "conversations" / "dataclaw").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"session_key": "dataclaw:u/d:conversations.jsonl:2", "turn_number": 0,
                                          "typed_by_developer": True, "content": "ask Harriet Quill and Ludo Courtè"}]),
                   t / "conversations" / "dataclaw" / "0000.parquet")
    names.write(t, tmp_path / "out", finder=finder, log=lambda m: None)
    lines = (tmp_path / "out" / "copied" / "dataclaw" / "u__d" / "conversations.jsonl").read_text().splitlines()
    assert "Harriet Quill, again" in lines[0] and "Harriet Quill" not in lines[1] and "Court" not in lines[1]


def test_copied_masking_never_writes_through_a_link_to_the_source(tmp_path):
    t, found = copied_tier(tmp_path)
    out = tmp_path / "out"
    credit_note(out)
    src = t / "copied" / "dataclaw" / "u__d" / "conversations.jsonl"
    before = src.read_bytes()
    (out / "copied" / "dataclaw" / "u__d").mkdir(parents=True)
    (out / "copied" / "dataclaw" / "u__d" / "conversations.jsonl").hardlink_to(src)   # a tier written by links
    names.mask_copied(t, out, found, log=lambda m: None)
    assert src.read_bytes() == before
    assert "[REDACTED]" in (out / "copied" / "dataclaw" / "u__d" / "conversations.jsonl").read_text()


def test_names_given_are_masked_without_finding_them_again(tier, tmp_path):
    stats = names.write(tier, tmp_path / "out", found={"s1": {"Harriet Quill"}}, log=lambda m: None)   # no finder
    rows = read(tmp_path / "out" / "conversations" / "entire" / "0000.parquet")
    assert rows[0]["content"] == "Ask [REDACTED] about dict[str] and Weak Match"
    assert rows[3]["content"] == "thanks Ludo Courtè"   # not given: left
    assert stats["sessions"] == 1
